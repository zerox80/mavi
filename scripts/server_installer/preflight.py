"""Read-only Linux and Docker checks used before any installer changes."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
from typing import Callable, Iterable

try:
    from .config import InstallPlan, PANEL, PROXY_NGINX, PROXY_TRAEFIK
except ImportError:  # Direct execution from the repository checkout.
    from config import InstallPlan, PANEL, PROXY_NGINX, PROXY_TRAEFIK

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"


@dataclass
class PreflightReport:
    checks: list[Check]

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if check.failed]

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(name, status, detail))


def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


def docker_install_commands(os_release: dict[str, str]) -> list[list[str]]:
    distro = os_release.get("ID", "")
    if distro in {"debian", "ubuntu"}:
        return [
            ["apt-get", "update"],
            ["apt-get", "install", "-y", "docker.io", "docker-compose-v2"],
        ]
    if distro in {"arch", "manjaro"}:
        return [["pacman", "-S", "--needed", "docker", "docker-compose"]]
    return []


def _run(runner: Runner, args: list[str]) -> subprocess.CompletedProcess[str]:
    return runner(args, capture_output=True, text=True, timeout=20, check=False)


def _docker_checks(report: PreflightReport, runner: Runner) -> None:
    if not shutil.which("docker"):
        report.add("docker", "FAIL", "Docker is not installed. The installer can use distribution packages after confirmation.")
        return
    try:
        result = _run(runner, ["docker", "info", "--format", "{{.ServerVersion}}"])
    except (OSError, subprocess.SubprocessError) as exc:
        report.add("docker", "FAIL", f"Cannot query the Docker daemon: {exc}")
        return
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        report.add("docker", "FAIL", f"Docker daemon is unavailable: {detail}")
        return
    report.add("docker", "PASS", f"Docker server {result.stdout.strip()} is reachable.")
    try:
        compose = _run(runner, ["docker", "compose", "version", "--short"])
    except (OSError, subprocess.SubprocessError) as exc:
        report.add("compose", "FAIL", f"Cannot run the Docker Compose plugin: {exc}")
        return
    if compose.returncode:
        report.add("compose", "FAIL", "The 'docker compose' plugin is missing or not working.")
    else:
        report.add("compose", "PASS", f"Docker Compose {compose.stdout.strip()} is available.")


def _memory_check(report: PreflightReport, plan: InstallPlan) -> None:
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        total_kib = int(next(line.split()[1] for line in meminfo.splitlines() if line.startswith("MemTotal:")))
    except (OSError, ValueError, StopIteration):
        report.add("memory", "WARN", "Cannot determine total memory from /proc/meminfo.")
        return
    total_gib = total_kib / 1024 / 1024
    recommended = 4 if plan.uses_keycloak else 1
    status = "PASS" if total_gib >= recommended else "WARN"
    report.add("memory", status, f"{total_gib:.1f} GiB detected; {recommended} GiB or more is recommended for this mode.")


def _storage_check(report: PreflightReport, backend: Path, plan: InstallPlan) -> None:
    try:
        free_gib = shutil.disk_usage(backend).free / 1024**3
    except OSError as exc:
        report.add("storage", "WARN", f"Cannot determine free storage: {exc}")
        return
    recommended = 12 if plan.mode == PANEL else 8
    status = "PASS" if free_gib >= recommended else "WARN"
    report.add("storage", status, f"{free_gib:.1f} GiB free; {recommended} GiB or more is recommended for images and data.")


def _address_checks(report: PreflightReport, runner: Runner) -> None:
    if not shutil.which("ip"):
        report.add("network", "WARN", "The 'ip' utility is unavailable; host IPv4/IPv6 addresses were not inspected.")
        return
    for version, name in ((4, "ipv4"), (6, "ipv6")):
        try:
            result = _run(runner, ["ip", f"-{version}", "-o", "addr", "show", "scope", "global"])
        except (OSError, subprocess.SubprocessError) as exc:
            report.add(name, "WARN", f"Cannot inspect {name.upper()} addresses: {exc}")
            continue
        addresses = [line.split()[3] for line in result.stdout.splitlines() if len(line.split()) > 3]
        if result.returncode == 0 and addresses:
            report.add(name, "PASS", f"Global {name.upper()} addresses: {', '.join(addresses)}")
        elif version == 4:
            report.add(name, "WARN", "No global IPv4 address was found. VPN clients need a reachable IPv4 or IPv6 endpoint.")
        else:
            report.add(name, "WARN", "No global IPv6 address was found; use VPN_DISABLE_IPV6=true if the host is IPv4-only.")


def _dns_check(report: PreflightReport, hostname: str) -> None:
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)})
    except socket.gaierror as exc:
        report.add(f"dns:{hostname}", "WARN", f"DNS does not resolve {hostname}: {exc}")
        return
    report.add(f"dns:{hostname}", "PASS", f"{hostname} resolves to {', '.join(addresses)}.")


def _port_check(report: PreflightReport, port: int, protocol: str, required: bool) -> None:
    kind = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    try:
        with socket.socket(socket.AF_INET, kind) as listener:
            listener.bind(("0.0.0.0", port))
    except OSError as exc:
        status = "FAIL" if required else "WARN"
        report.add(f"port:{port}/{protocol}", status, f"Port {port}/{protocol} is already in use or cannot be bound: {exc}")
    else:
        report.add(f"port:{port}/{protocol}", "PASS", f"Port {port}/{protocol} can be bound on IPv4.")


def _network_checks(report: PreflightReport, plan: InstallPlan, values: dict[str, str], runner: Runner) -> None:
    try:
        vpn_port = int(values.get("VPN_PORT", "10443"))
        if not 1 <= vpn_port <= 65535:
            raise ValueError
    except ValueError:
        report.add("port:vpn", "FAIL", "VPN_PORT must be an integer between 1 and 65535.")
        return
    _port_check(report, vpn_port, "udp", required=False)
    if plan.uses_keycloak:
        _dns_check(report, f"auth.{plan.domain}")
        if plan.mode == PANEL:
            _dns_check(report, f"panel.{plan.domain}")
        if plan.proxy == PROXY_TRAEFIK:
            for key, protocol in (("TRAEFIK_HTTP_PORT", "tcp"), ("TRAEFIK_HTTPS_PORT", "tcp")):
                try:
                    port = int(values.get(key, "80" if key.endswith("HTTP_PORT") else "443"))
                    _port_check(report, port, protocol, required=False)
                except ValueError:
                    report.add(f"port:{key}", "FAIL", f"{key} must be an integer between 1 and 65535.")
        elif plan.proxy == PROXY_NGINX:
            _port_check(report, 80, "tcp", required=False)
            _port_check(report, 443, "tcp", required=False)
    http2 = values.get("VPN_HTTP2_BIND_ADDR", "")
    if http2:
        try:
            _port_check(report, int(http2.rsplit(":", 1)[1]), "tcp", required=False)
        except (ValueError, IndexError):
            report.add("port:http2", "FAIL", "VPN_HTTP2_BIND_ADDR must end in a numeric TCP port.")


def collect_preflight(
    plan: InstallPlan,
    values: dict[str, str],
    backend: Path,
    runner: Runner = subprocess.run,
    include_docker: bool = True,
) -> PreflightReport:
    """Run read-only checks. This function never installs packages or writes files."""
    report = PreflightReport([])
    if platform.system() != "Linux":
        report.add("os", "FAIL", "The server installer supports Linux only.")
        return report
    report.add("os", "PASS", "Linux detected.")
    os_release = read_os_release()
    distro = os_release.get("PRETTY_NAME", "unknown distribution")
    report.add("distro", "PASS", distro)
    architecture = platform.machine().lower()
    if architecture in {"x86_64", "amd64", "aarch64", "arm64"}:
        report.add("architecture", "PASS", f"{architecture} is supported by the container images.")
    else:
        report.add("architecture", "FAIL", f"Unsupported CPU architecture: {architecture}; use x86_64 or arm64.")
    _memory_check(report, plan)
    _storage_check(report, backend, plan)
    if include_docker:
        _docker_checks(report, runner)
    if not Path("/dev/net/tun").exists():
        report.add("tun", "FAIL", "/dev/net/tun is missing. Load the tun module before starting the VPN.")
    else:
        report.add("tun", "PASS", "/dev/net/tun is available.")
    _address_checks(report, runner)
    if plan.uses_keycloak and not plan.domain:
        report.add("domain", "FAIL", "DOMAIN_NAME is required for Keycloak and the web panel.")
    elif plan.uses_keycloak:
        _network_checks(report, plan, values, runner)
    else:
        _network_checks(report, plan, values, runner)
    return report


def privileged_commands(commands: Iterable[list[str]]) -> list[list[str]]:
    if getattr(os, "geteuid", lambda: 1)() == 0:
        return [list(command) for command in commands]
    if not shutil.which("sudo"):
        raise RuntimeError("Root privileges are required and sudo is not installed.")
    return [["sudo", *command] for command in commands]
