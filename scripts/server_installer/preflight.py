"""Read-only Linux and Docker checks used before any installer changes."""

from __future__ import annotations

from dataclasses import dataclass, field
import errno
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
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
    public_ipv4: list[str] = field(default_factory=list)
    public_ipv6: list[str] = field(default_factory=list)

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
            public, local = [], []
            for address in addresses:
                try:
                    ip = ipaddress.ip_interface(address).ip
                except ValueError:
                    continue
                (public if ip.is_global else local).append(str(ip))
            setattr(report, "public_" + name, sorted(set(public)))
            detail = f"Public {name.upper()} addresses: {', '.join(public) or 'none detected'}"
            if local:
                detail += f"; private/VPN/container addresses: {', '.join(local)}"
            if not public:
                detail += ". A public endpoint may still be provided through NAT."
            report.add(name, "PASS" if public else "WARN", detail)
        elif version == 4:
            report.add(name, "WARN", "No global IPv4 address was found. VPN clients need a reachable IPv4 or IPv6 endpoint.")
        else:
            report.add(name, "WARN", "No global IPv6 address was found; use VPN_DISABLE_IPV6=true if the host is IPv4-only.")


def _dns_check(report: PreflightReport, hostname: str, required: bool = False) -> None:
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)})
        if not addresses:
            raise socket.gaierror("No addresses returned")
    except OSError as exc:
        report.add(f"dns:{hostname}", "FAIL" if required else "WARN",
                   f"DNS does not resolve {hostname}: {exc}. Create its A/AAAA record at your DNS provider, then retry.")
        return
    report.add(f"dns:{hostname}", "PASS", f"{hostname} resolves to {', '.join(addresses)}.")
    local = set(report.public_ipv4 + report.public_ipv6)
    if local and any(address not in local for address in addresses):
        report.add(f"dns-target:{hostname}", "WARN",
                   "Some DNS addresses are not on this host. Verify NAT/CDN forwarding; stale AAAA records can break certificate validation.")


def _listener_details(port: int, protocol: str, runner: Runner) -> tuple[list[tuple[str, int]], str]:
    if not shutil.which("ss"):
        return [], "owner unknown; install iproute2 or inspect the listener manually"
    try:
        result = _run(runner, ["ss", "-H", "-ltnp" if protocol == "tcp" else "-lunp", f"sport = :{port}"])
    except (OSError, subprocess.SubprocessError):
        return [], "listener inspection failed"
    lines = result.stdout.splitlines()
    owners = [(name, int(pid)) for name, pid in re.findall(r'\("([^"\n]+)",pid=(\d+)', result.stdout)]
    if result.returncode or not lines or any(not re.search(r'\("[^"\n]+",pid=\d+', line) for line in lines):
        return [], "owner unknown; rerun with sudo to inspect all listeners"
    detail = ", ".join(f"{name} (PID {pid})" for name, pid in sorted(set(owners)))
    if any(name == "docker-proxy" for name, _ in owners) and shutil.which("docker"):
        try:
            containers = _run(runner, ["docker", "ps", "--filter", f"publish={port}/{protocol}", "--format", "{{.Names}}: {{.Ports}}"])
            if not containers.returncode and containers.stdout.strip():
                detail += "; containers: " + " | ".join(containers.stdout.splitlines())
        except (OSError, subprocess.SubprocessError):
            pass
    return owners, detail


def _host_nginx(owners: list[tuple[str, int]]) -> bool:
    if not owners or any(name != "nginx" for name, _ in owners):
        return False
    executable = shutil.which("nginx")
    if not executable:
        return False
    try:
        return all(Path(f"/proc/{pid}/root").samefile("/")
                   and Path(f"/proc/{pid}/exe").samefile(executable) for _, pid in owners)
    except OSError:
        return False


def _published_containers(port: int, protocol: str, runner: Runner) -> list[tuple[str, dict[str, str]]]:
    """Inspect exact host port mappings; a successful bind does not rule out Docker DNAT."""
    if not shutil.which("docker"):
        return []
    try:
        ids = _run(runner, ["docker", "ps", "-q"])
        if ids.returncode or not ids.stdout.strip():
            return []
        template = '{{json .Name}}\t{{json .Config.Labels}}\t{{json .NetworkSettings.Ports}}'
        result = _run(runner, ["docker", "inspect", "--format", template, *ids.stdout.split()])
        if result.returncode:
            return []
        owners = []
        for line in result.stdout.splitlines():
            name, labels, mappings = [json.loads(value) for value in line.split("\t")]
            if any(key.endswith("/" + protocol) and any(str(b.get("HostPort")) == str(port) for b in bindings or [])
                   for key, bindings in (mappings or {}).items()):
                owners.append((name.lstrip("/"), labels or {}))
        return owners
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return []


def _same_compose_service(owners: list[tuple[str, dict[str, str]]], backend: Path | None, service: str) -> bool:
    return bool(owners and backend and all(
        labels.get("com.docker.compose.service") == service
        and labels.get("com.docker.compose.project.working_dir")
        and Path(labels["com.docker.compose.project.working_dir"]).resolve() == backend.resolve()
        for _, labels in owners))


def _port_check(report: PreflightReport, port: int, protocol: str, required: bool,
                runner: Runner = subprocess.run, reuse_nginx: bool = False,
                inspect_docker: bool = False, compose_backend: Path | None = None) -> None:
    if not 1 <= port <= 65535:
        report.add(f"port:{port}/{protocol}", "FAIL", "Port must be between 1 and 65535.")
        return
    if inspect_docker:
        owners = _published_containers(port, protocol, runner)
        if owners:
            if _same_compose_service(owners, compose_backend, "traefik"):
                report.add(f"port:{port}/{protocol}", "PASS", "This installation's existing Traefik service will be reused.")
            else:
                names = ", ".join(name for name, _ in owners)
                report.add(f"port:{port}/{protocol}", "FAIL" if required else "WARN",
                           f"Published by Docker container(s): {names}. Use an external reverse proxy or free the port yourself; no container will be stopped.")
            return
    kind = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    try:
        with socket.socket(socket.AF_INET, kind) as listener:
            listener.bind(("0.0.0.0", port))
    except OSError as exc:
        status = "FAIL" if required else "WARN"
        if exc.errno in (errno.EACCES, errno.EPERM):
            detail = "Permission denied while checking the port; rerun with sudo. This does not prove the port is occupied."
        elif exc.errno == errno.EADDRINUSE:
            owners, detail = _listener_details(port, protocol, runner)
            if reuse_nginx and _host_nginx(owners):
                try:
                    valid = _run(runner, ["nginx", "-t"]).returncode == 0
                except (OSError, subprocess.SubprocessError):
                    valid = False
                if valid:
                    report.add(f"port:{port}/{protocol}", "PASS", "Existing host Nginx will be reused; nginx -t passed. Only the Mavi site is managed.")
                    return
                detail = "Existing host Nginx has an invalid configuration. Fix nginx -t before setup."
            else:
                detail = f"Port is in use by {detail}. Choose an external reverse proxy or free this port yourself, then retry."
        else:
            detail = f"Cannot bind the port: {exc}"
        report.add(f"port:{port}/{protocol}", status, detail)
    else:
        report.add(f"port:{port}/{protocol}", "PASS", f"Port {port}/{protocol} can be bound on IPv4.")


def _network_checks(report: PreflightReport, plan: InstallPlan, values: dict[str, str], runner: Runner,
                    network_required: bool = True, backend: Path | None = None) -> None:
    try:
        vpn_port = int(values.get("VPN_PORT", "10443"))
        if not 1 <= vpn_port <= 65535:
            raise ValueError
    except ValueError:
        report.add("port:vpn", "FAIL", "VPN_PORT must be an integer between 1 and 65535.")
        return
    _port_check(report, vpn_port, "udp", required=False, runner=runner)
    if plan.uses_keycloak:
        managed = plan.proxy in (PROXY_NGINX, PROXY_TRAEFIK) and network_required
        _dns_check(report, f"auth.{plan.domain}", required=managed)
        if plan.mode == PANEL:
            _dns_check(report, f"panel.{plan.domain}", required=managed)
        if plan.proxy == PROXY_TRAEFIK:
            for key, protocol in (("TRAEFIK_HTTP_PORT", "tcp"), ("TRAEFIK_HTTPS_PORT", "tcp")):
                try:
                    port = int(values.get(key, "80" if key.endswith("HTTP_PORT") else "443"))
                    _port_check(report, port, protocol, required=network_required, runner=runner,
                                inspect_docker=True, compose_backend=backend)
                except ValueError:
                    report.add(f"port:{key}", "FAIL", f"{key} must be an integer between 1 and 65535.")
        elif plan.proxy == PROXY_NGINX:
            for port in (80, 443):
                _port_check(report, port, "tcp", required=network_required, runner=runner, reuse_nginx=True, inspect_docker=True)
    http2 = values.get("VPN_HTTP2_BIND_ADDR", "")
    if http2:
        try:
            _port_check(report, int(http2.rsplit(":", 1)[1]), "tcp", required=False, runner=runner)
        except (ValueError, IndexError):
            report.add("port:http2", "FAIL", "VPN_HTTP2_BIND_ADDR must end in a numeric TCP port.")


def collect_preflight(
    plan: InstallPlan,
    values: dict[str, str],
    backend: Path,
    runner: Runner = subprocess.run,
    include_docker: bool = True,
    network_required: bool = True,
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
        _network_checks(report, plan, values, runner, network_required, backend)
    else:
        _network_checks(report, plan, values, runner, network_required, backend)
    return report


def privileged_commands(commands: Iterable[list[str]]) -> list[list[str]]:
    if getattr(os, "geteuid", lambda: 1)() == 0:
        return [list(command) for command in commands]
    if not shutil.which("sudo"):
        raise RuntimeError("Root privileges are required and sudo is not installed.")
    return [["sudo", *command] for command in commands]
