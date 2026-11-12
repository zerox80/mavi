"""Install and configure the dedicated Mavi Nginx reverse proxy.

The caller is responsible for obtaining user consent and for installing Docker.
All commands use argument lists, and all filesystem writes are confined to the
Mavi site, its backup, its sites-enabled symlink, and its private proxy headers.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Callable


_PACKAGES = ["nginx", "certbot", "python3-certbot-nginx"]
_CONFIG_NAME = "mavi-vpn.conf"
_PROXY_HEADERS_FILE = "mavi-panel-proxy-headers.conf"
_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_EMAIL_LOCAL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")


class NginxSetupError(RuntimeError):
    """Raised when installation, configuration, or verification fails."""


def package_names() -> list[str]:
    """Return the Debian packages needed by this module."""
    return list(_PACKAGES)


def _domain(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ValueError("Domain must be a non-empty DNS name without whitespace")
    if len(value) > 253 or value.endswith("."):
        raise ValueError("Domain must be at most 253 characters and not end with a dot")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("Domain must be a DNS name, not an IP address")
    labels = value.split(".")
    if len(labels) < 2 or any(not _LABEL.fullmatch(label) for label in labels):
        raise ValueError("Domain must contain valid DNS labels and a dot")
    return value.lower()


def _hostnames(value: str, panel_enabled: bool) -> tuple[str, list[str]]:
    base = _domain(value)
    hosts = [f"auth.{base}"]
    if panel_enabled:
        hosts.append(f"panel.{base}")
    if any(len(host) > 253 for host in hosts):
        raise ValueError("Domain is too long for the required auth./panel. hostnames")
    return base, hosts


def _port(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError(f"{name} must be an integer from 1 to 65535")
    return value


def _email(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 254:
        raise ValueError("Email must be a non-empty address without surrounding whitespace")
    if any(ord(character) < 33 or ord(character) == 127 for character in value):
        raise ValueError("Email must not contain whitespace or control characters")
    if value.count("@") != 1:
        raise ValueError("Email must contain exactly one @")
    local, domain = value.rsplit("@", 1)
    if (
        not local
        or len(local) > 64
        or not _EMAIL_LOCAL.fullmatch(local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
    ):
        raise ValueError("Email has an invalid local part")
    try:
        _domain(domain)
    except ValueError as error:
        raise ValueError("Email has an invalid domain") from error
    return value


def _proxy_block(host: str, port: int, zone: str, authenticate_proxy: bool = False) -> str:
    proxy_headers = f"\n        include sites-available/{_PROXY_HEADERS_FILE};" if authenticate_proxy else ""
    forwarded_for = "$remote_addr" if authenticate_proxy else "$proxy_add_x_forwarded_for"
    return f"""server {{
    listen 80;
    listen [::]:80;
    server_name {host};

    client_max_body_size 1m;
    client_body_buffer_size 16k;
    client_header_buffer_size 1k;
    large_client_header_buffers 2 8k;
    server_tokens off;

    location ^~ /.well-known/acme-challenge/ {{
        root /var/www/html;
        default_type text/plain;
        try_files $uri =404;
    }}

    location / {{
        limit_req zone={zone} burst=20 nodelay;
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For {forwarded_for};{proxy_headers}
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $mavi_connection_upgrade;
        proxy_connect_timeout 10s;
        proxy_read_timeout 300s;
    }}
}}"""


def render_http_site(
    domain: str,
    panel_enabled: bool,
    keycloak_port: int = 18080,
    panel_port: int = 18081,
    *,
    authenticate_proxy: bool = False,
) -> str:
    """Render HTTP virtual hosts for ACME and loopback proxying.

    Certbot's Nginx plugin subsequently adds HTTPS listeners and redirects.
    """
    _, hosts = _hostnames(domain, panel_enabled)
    keycloak_port = _port(keycloak_port, "Keycloak port")
    panel_port = _port(panel_port, "Panel port")
    if panel_enabled and keycloak_port == panel_port:
        raise ValueError("Keycloak and panel ports must differ")
    zones = f"limit_req_zone $binary_remote_addr zone=mavi_auth_limit:10m rate=10r/s;"
    blocks = [_proxy_block(hosts[0], keycloak_port, "mavi_auth_limit")]
    if panel_enabled:
        zones += f"\nlimit_req_zone $binary_remote_addr zone=mavi_panel_limit:10m rate=10r/s;"
        blocks.append(_proxy_block(hosts[1], panel_port, "mavi_panel_limit", authenticate_proxy))
    return (
        "# Managed by Mavi. Do not edit; Certbot manages the HTTPS sections.\n"
        "map $http_upgrade $mavi_connection_upgrade {\n"
        "    default upgrade;\n"
        "    '' close;\n"
        "}\n"
        f"{zones}\n\n" + "\n\n".join(blocks) + "\n"
    )


def preflight_dns(
    domain: str,
    panel_enabled: bool,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> dict[str, Any]:
    """Resolve required hostnames and return non-fatal, structured warnings.

    A match between hostnames does not prove public reachability, firewall/NAT
    configuration, or that the addresses belong to this server.
    """
    _, names = _hostnames(domain, panel_enabled)
    hosts: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for name in names:
        addresses: set[str] = set()
        error = None
        try:
            answers = resolver(name, 443, type=socket.SOCK_STREAM)
            addresses = {str(answer[4][0]) for answer in answers}
            if not addresses:
                error = "No addresses returned"
        except (OSError, socket.gaierror) as exception:
            error = str(exception) or exception.__class__.__name__
        hosts[name] = {"addresses": sorted(addresses), "error": error}
        if error or not addresses:
            warnings.append(f"DNS resolution failed for {name}: {error}")
    if panel_enabled:
        auth_addresses = set(hosts[names[0]]["addresses"])
        panel_addresses = set(hosts[names[1]]["addresses"])
        if auth_addresses and panel_addresses and auth_addresses != panel_addresses:
            warnings.append("Auth and panel DNS addresses differ; verify both point to this server")
    warnings.append("DNS checks do not verify public reachability or external firewall/NAT rules")
    return {
        "ok": all(item["addresses"] and not item["error"] for item in hosts.values()),
        "hosts": hosts,
        "warnings": warnings,
    }


@dataclass
class _PreviousState:
    content: bytes | None
    mode: int
    link_target: str | None
    link_existed: bool


class NginxSetup:
    """Imperative Nginx setup with injectable command execution and paths."""

    def __init__(
        self,
        run: Callable[..., Any] | None = None,
        *,
        nginx_root: str | os.PathLike[str] = "/etc/nginx",
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._run = run or subprocess.run
        self._which = which
        self._available_dir = Path(nginx_root) / "sites-available"
        self._enabled_dir = Path(nginx_root) / "sites-enabled"
        self._config = self._available_dir / _CONFIG_NAME
        self._backup = self._available_dir / f"{_CONFIG_NAME}.mavi-backup"
        self._link = self._enabled_dir / _CONFIG_NAME
        self._proxy_headers = self._available_dir / _PROXY_HEADERS_FILE

    def _command(self, command: list[str], description: str) -> None:
        try:
            self._run(command, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise NginxSetupError(description) from error

    @staticmethod
    def _is_symlink(path: Path) -> bool:
        return path.is_symlink()

    @staticmethod
    def _read_symlink(path: Path) -> str:
        return os.readlink(path)

    @staticmethod
    def _make_symlink(path: Path, target: Path | str) -> None:
        path.symlink_to(target)

    def _check_layout(self) -> None:
        if not self._available_dir.is_dir():
            raise NginxSetupError(
                f"Missing Nginx sites-available directory: {self._available_dir}; "
                "refusing to modify nginx.conf"
            )
        if not self._enabled_dir.is_dir():
            raise NginxSetupError(f"Missing Nginx sites-enabled directory: {self._enabled_dir}")

    def _snapshot(self) -> _PreviousState:
        content = None
        mode = 0o644
        if os.path.lexists(self._config):
            if self._is_symlink(self._config) or not self._config.is_file():
                raise NginxSetupError(f"Refusing to replace non-regular Mavi config: {self._config}")
            content = self._config.read_bytes()
            mode = stat.S_IMODE(self._config.stat().st_mode)
        link_existed = os.path.lexists(self._link)
        link_target = None
        if link_existed:
            if not self._is_symlink(self._link):
                raise NginxSetupError(f"Refusing to replace non-symlink enabled site: {self._link}")
            link_target = self._read_symlink(self._link)
            actual = Path(link_target)
            if not actual.is_absolute():
                actual = self._link.parent / actual
            if actual.resolve(strict=False) != self._config.resolve(strict=False):
                raise NginxSetupError(f"Refusing to replace foreign enabled site: {self._link}")
        return _PreviousState(content, mode, link_target, link_existed)

    @staticmethod
    def _atomic_write(path: Path, content: bytes, mode: int) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def _write_candidate(self, content: bytes, previous: _PreviousState) -> None:
        if previous.content == content:
            os.chmod(self._config, 0o644)
        else:
            if previous.content is not None:
                if os.path.lexists(self._backup) and (
                    self._is_symlink(self._backup) or not self._backup.is_file()
                ):
                    raise NginxSetupError(f"Refusing to replace non-regular backup: {self._backup}")
                self._atomic_write(self._backup, previous.content, 0o600)
            self._atomic_write(self._config, content, 0o644)
        if not previous.link_existed:
            self._make_symlink(self._link, self._config)

    def _rollback(self, previous: _PreviousState) -> None:
        if previous.content is None:
            if os.path.lexists(self._config):
                if self._config.is_dir() and not self._is_symlink(self._config):
                    raise NginxSetupError("Rollback refused to remove a directory")
                self._config.unlink()
        else:
            self._atomic_write(self._config, previous.content, previous.mode)
        if previous.link_existed:
            if not os.path.lexists(self._link):
                self._make_symlink(self._link, previous.link_target)
        elif os.path.lexists(self._link):
            if self._link.is_dir() and not self._is_symlink(self._link):
                raise NginxSetupError("Rollback refused to remove a directory")
            self._link.unlink()

    def _reload_after_rollback(self) -> None:
        try:
            self._command(["nginx", "-t"], "Restored Nginx configuration is invalid")
            self._command(["systemctl", "reload", "nginx"], "Nginx reload after rollback failed")
        except NginxSetupError:
            pass

    def _configure_timer(self) -> None:
        if self._which("systemctl") is None:
            return
        self._command(["systemctl", "enable", "--now", "certbot.timer"], "Certbot timer enable failed")
        self._command(
            ["systemctl", "is-enabled", "--quiet", "certbot.timer"],
            "Certbot timer is not enabled",
        )
        self._command(
            ["systemctl", "is-active", "--quiet", "certbot.timer"],
            "Certbot timer is not active",
        )

    def install_and_configure(
        self,
        domain: str,
        panel_enabled: bool,
        email: str,
        *,
        install_packages: bool = False,
        proxy_token: str = "",
    ) -> None:
        """Install on explicit request, configure Mavi, and verify the result."""
        base, hostnames = _hostnames(domain, panel_enabled)
        email = _email(email)
        authenticate_proxy = panel_enabled and bool(proxy_token)
        if authenticate_proxy and not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", proxy_token):
            raise NginxSetupError("MAVI_PANEL_PROXY_TOKEN has an invalid value")
        content = render_http_site(base, panel_enabled, authenticate_proxy=authenticate_proxy).encode("utf-8")
        if install_packages:
            self._command(["apt-get", "update"], "APT package index update failed")
            self._command(
                ["apt-get", "install", "-y", "--no-install-recommends", *package_names()],
                "Nginx and Certbot installation failed",
            )
        self._check_layout()
        previous = self._snapshot()
        previous_proxy_headers = None
        if authenticate_proxy and os.path.lexists(self._proxy_headers):
            if self._is_symlink(self._proxy_headers) or not self._proxy_headers.is_file():
                raise NginxSetupError("Refusing to replace a non-regular Mavi proxy header file")
            previous_proxy_headers = self._proxy_headers.read_bytes()
        certbot = ["certbot", "--nginx", "--non-interactive", "--agree-tos", "--email", email, "--redirect"]
        for name in hostnames:
            certbot.extend(["-d", name])
        try:
            if authenticate_proxy:
                header = f"proxy_set_header X-Mavi-Proxy-Token {proxy_token};\n".encode("ascii")
                self._atomic_write(self._proxy_headers, header, 0o600)
            self._write_candidate(content, previous)
            self._command(["nginx", "-t"], "Mavi HTTP Nginx configuration is invalid")
            self._command(certbot, "Certbot Nginx configuration failed")
            self._command(["nginx", "-t"], "Certbot produced an invalid Nginx configuration")
            self._command(["systemctl", "reload", "nginx"], "Nginx reload failed")
            self._configure_timer()
        except Exception:
            self._rollback(previous)
            if authenticate_proxy:
                if previous_proxy_headers is None:
                    self._proxy_headers.unlink(missing_ok=True)
                else:
                    self._atomic_write(self._proxy_headers, previous_proxy_headers, 0o600)
            self._reload_after_rollback()
            raise
