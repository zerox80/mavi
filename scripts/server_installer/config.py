"""Configuration planning and atomic .env updates for the server installer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
import shlex
import tempfile
from typing import Callable, Mapping

CLASSIC = "classic"
KEYCLOAK = "keycloak"
PANEL = "panel"
MODES = (PANEL, CLASSIC, KEYCLOAK)
PROXY_NGINX = "nginx"
PROXY_TRAEFIK = "traefik"
PROXY_EXTERNAL = "external"
BASE_COMPOSE = "docker-compose.yml"
KEYCLOAK_COMPOSE = "keycloak/docker-compose.yml"
PANEL_COMPOSE = "panel/docker-compose.yml"
# Optional overrides that compile the images on the host instead of pulling them.
BUILD_COMPOSE = "docker-compose.build.yml"
PANEL_BUILD_COMPOSE = "panel/docker-compose.build.yml"
_SECRET_KEYS = (
    "VPN_AUTH_TOKEN",
    "KEYCLOAK_DB_PASSWORD",
    "KEYCLOAK_ADMIN_PASS",
    "MAVI_PANEL_DB_PASSWORD",
    "MAVI_PANEL_CLIENT_SECRET",
)
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:@%+,=-]*$")


class ConfigurationError(ValueError):
    """The requested installation cannot produce a valid configuration."""


@dataclass(frozen=True)
class InstallPlan:
    mode: str
    domain: str = ""
    acme_email: str = ""
    proxy: str = PROXY_NGINX
    create_admin: bool = False
    admin_username: str = "panel-admin"
    admin_email: str = ""
    existing: bool = False
    source_build: bool = False

    @property
    def uses_keycloak(self) -> bool:
        return self.mode in (KEYCLOAK, PANEL)

    @property
    def uses_panel(self) -> bool:
        return self.mode == PANEL


@dataclass(frozen=True)
class SaveResult:
    changed: bool
    backup: Path | None = None


def parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def mode_from_environment(values: Mapping[str, str]) -> str:
    """The panel always implies Keycloak, even in an inconsistent old .env."""
    compose_file = values.get("COMPOSE_FILE", "")
    panel = parse_bool(values.get("MAVI_WEB_PANEL_ENABLED")) or PANEL_COMPOSE in compose_file
    keycloak = parse_bool(values.get("VPN_KEYCLOAK_ENABLED")) or KEYCLOAK_COMPOSE in compose_file
    if panel:
        return PANEL
    if keycloak:
        return KEYCLOAK
    return CLASSIC


def source_build_from_environment(values: Mapping[str, str]) -> bool:
    """An existing installation compiles locally when COMPOSE_FILE lists the build override."""
    return BUILD_COMPOSE in values.get("COMPOSE_FILE", "").split(":")


def compose_file_for_mode(mode: str, source_build: bool = False) -> str:
    if mode == PANEL:
        files = [BASE_COMPOSE, KEYCLOAK_COMPOSE, PANEL_COMPOSE]
    elif mode == KEYCLOAK:
        files = [BASE_COMPOSE, KEYCLOAK_COMPOSE]
    elif mode == CLASSIC:
        files = [BASE_COMPOSE]
    else:
        raise ConfigurationError(f"Unknown installation mode: {mode}")
    if source_build:
        # Overrides come last so they replace the prebuilt image references.
        files.append(BUILD_COMPOSE)
        if mode == PANEL:
            files.append(PANEL_BUILD_COMPOSE)
    return ":".join(files)


def validate_domain(domain: str) -> str:
    domain = domain.strip().rstrip(".").lower()
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if not re.fullmatch(rf"{label}(?:\.{label})+", domain):
        raise ConfigurationError("Enter a fully qualified DNS name such as example.com.")
    if len(domain) > 253:
        raise ConfigurationError("The domain name is longer than 253 characters.")
    return domain


def validate_username(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,128}", value):
        raise ConfigurationError("The admin username may contain letters, digits, '.', '_', '@', and '-'.")
    return value


def generate_secret(_name: str = "") -> str:
    return secrets.token_urlsafe(36)


def proxy_from_environment(values: Mapping[str, str]) -> str:
    """Classify the proxy of an existing installation without changing it.

    The old Traefik profile binds publicly for standalone HTTPS or to loopback
    behind an external proxy. A .env without Traefik bindings uses host Nginx.
    """
    bind = values.get("TRAEFIK_BIND_ADDR", "").strip()
    if bind == "0.0.0.0":
        return PROXY_TRAEFIK
    if bind:
        return PROXY_EXTERNAL
    return PROXY_NGINX


def plan_from_existing(values: Mapping[str, str]) -> InstallPlan:
    mode = mode_from_environment(values)
    return InstallPlan(
        mode=mode,
        domain=values.get("DOMAIN_NAME", ""),
        acme_email=values.get("ACME_EMAIL", ""),
        proxy=proxy_from_environment(values),
        existing=True,
        source_build=source_build_from_environment(values),
    )


def _secret(values: dict[str, str], key: str, factory: Callable[[str], str], generated: set[str]) -> None:
    if not values.get(key):
        values[key] = factory(key)
        generated.add(key)


def build_configuration(
    plan: InstallPlan,
    existing: Mapping[str, str] | None = None,
    secret_factory: Callable[[str], str] = generate_secret,
) -> tuple[dict[str, str], set[str]]:
    """Merge a plan into existing values without replacing existing secrets."""
    values = dict(existing or {})
    generated: set[str] = set()
    if plan.mode not in MODES:
        raise ConfigurationError(f"Unknown installation mode: {plan.mode}")

    values.update(
        {
            "RUST_LOG": values.get("RUST_LOG", "info"),
            "VPN_PORT": values.get("VPN_PORT", "10443"),
            "VPN_BIND_ADDR": values.get("VPN_BIND_ADDR", f"0.0.0.0:{values.get('VPN_PORT', '10443')}"),
            "VPN_MTU": "1280",
            "VPN_NETWORK": values.get("VPN_NETWORK", "10.8.0.0/24"),
            "VPN_NETWORK_V6": values.get("VPN_NETWORK_V6", "fd00::/64"),
            "VPN_DNS": values.get("VPN_DNS", "1.1.1.1"),
            "VPN_CENSORSHIP_RESISTANT": values.get("VPN_CENSORSHIP_RESISTANT", "true"),
            "VPN_MSS_CLAMPING": values.get("VPN_MSS_CLAMPING", "true"),
            "MAVI_WEB_PANEL_ENABLED": "true" if plan.uses_panel else "false",
            "VPN_KEYCLOAK_ENABLED": "true" if plan.uses_keycloak else "false",
            "COMPOSE_FILE": compose_file_for_mode(plan.mode, plan.source_build),
            "COMPOSE_PROFILES": values.get(
                "COMPOSE_PROFILES",
                "traefik" if plan.proxy == PROXY_TRAEFIK and plan.uses_keycloak else "",
            ),
        }
    )

    if plan.mode == CLASSIC:
        _secret(values, "VPN_AUTH_TOKEN", secret_factory, generated)
        return values, generated

    domain = validate_domain(plan.domain or values.get("DOMAIN_NAME", ""))
    values["DOMAIN_NAME"] = domain
    values["VPN_KEYCLOAK_URL"] = values.get("VPN_KEYCLOAK_URL") or f"https://auth.{domain}"
    values["VPN_KEYCLOAK_REALM"] = values.get("VPN_KEYCLOAK_REALM", "mavi-vpn")
    values["VPN_KEYCLOAK_CLIENT_ID"] = values.get("VPN_KEYCLOAK_CLIENT_ID", "mavi-client")
    values["VPN_KEYCLOAK_REQUIRED_ROLE"] = values.get(
        "VPN_KEYCLOAK_REQUIRED_ROLE", "vpn-user" if plan.uses_panel else ""
    )
    values["KEYCLOAK_ADMIN_USER"] = values.get("KEYCLOAK_ADMIN_USER", "admin")
    _secret(values, "KEYCLOAK_DB_PASSWORD", secret_factory, generated)
    _secret(values, "KEYCLOAK_ADMIN_PASS", secret_factory, generated)

    if plan.uses_panel:
        _secret(values, "MAVI_PANEL_DB_PASSWORD", secret_factory, generated)
        _secret(values, "MAVI_PANEL_CLIENT_SECRET", secret_factory, generated)
        public_host = values.get("VPN_PUBLIC_HOST") or domain
        values["MAVI_PANEL_VPN_ENDPOINT"] = f"{public_host}:{values.get('VPN_PORT', '10443')}"
        if values.get("VPN_HTTP2_BIND_ADDR"):
            transport = "http2"
        elif parse_bool(values.get("VPN_CENSORSHIP_RESISTANT")):
            transport = "http3_censorship_resistant"
        else:
            transport = "quic"
        values["MAVI_PANEL_TRANSPORT"] = transport

    if not plan.existing:
        values["ACME_EMAIL"] = plan.acme_email or values.get("ACME_EMAIL", "")
        if plan.proxy == PROXY_TRAEFIK:
            values.update(
                {
                    "TRAEFIK_BIND_ADDR": "0.0.0.0",
                    "TRAEFIK_HTTP_PORT": values.get("TRAEFIK_HTTP_PORT", "80"),
                    "TRAEFIK_HTTPS_PORT": values.get("TRAEFIK_HTTPS_PORT", "443"),
                    "TRAEFIK_ROUTER_ENTRYPOINTS": "websecure",
                    "TRAEFIK_TLS_ENABLED": "true",
                    "TRAEFIK_ACME_RESOLVER": "myresolver",
                    "TRAEFIK_FORWARDED_HEADERS_INSECURE": "false",
                }
            )
        # Nginx and external-proxy modes need no Traefik settings: Keycloak and
        # the panel publish loopback ports that the reverse proxy forwards to.
    return values, generated


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _ENV_LINE.match(line)
        if not match:
            continue
        raw = line[line.index("=", match.start()) + 1 :].strip()
        if not raw:
            values[match.group(1)] = ""
            continue
        lexer = shlex.shlex(raw, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        try:
            values[match.group(1)] = " ".join(lexer)
        except ValueError as exc:
            raise ConfigurationError(f"Invalid value for {match.group(1)} in .env: {exc}") from exc
    return values


def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_env(path.read_text(encoding="utf-8"))


def _encode_value(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ConfigurationError("Environment values cannot contain newlines or null bytes.")
    if _SAFE_VALUE.fullmatch(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    return f'"{escaped}"'


def update_env_text(original: str, desired: Mapping[str, str]) -> str:
    """Preserve unchanged lines and comments, replacing only effective changes."""
    current = parse_env(original) if original else {}
    updates = {key: value for key, value in desired.items() if current.get(key) != value}
    if original and not updates:
        return original
    lines = original.splitlines(keepends=True)
    replaced: set[str] = set()
    output: list[str] = []
    for line in lines:
        match = _ENV_LINE.match(line)
        key = match.group(1) if match else ""
        if key in updates:
            if key not in replaced:
                ending = "\r\n" if line.endswith("\r\n") else "\n"
                output.append(f"{key}={_encode_value(updates[key])}{ending}")
                replaced.add(key)
            continue
        output.append(line)
    missing = [key for key in desired if key not in current and key not in replaced]
    if missing:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += "\n"
        if output and output[-1].strip():
            output.append("\n")
        output.append("# Server installer settings\n")
        output.extend(f"{key}={_encode_value(desired[key])}\n" for key in missing)
    return "".join(output)


def _write_private(path: Path, content: bytes, exclusive: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    descriptor = os.open(path, flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def save_env(path: Path, desired: Mapping[str, str]) -> SaveResult:
    original_bytes = path.read_bytes() if path.exists() else b""
    original = original_bytes.decode("utf-8") if original_bytes else ""
    rendered = update_env_text(original, desired)
    if original_bytes and rendered.encode("utf-8") == original_bytes:
        return SaveResult(False)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if original_bytes:
        data = path.parent / "data"
        if not data.exists():
            data.mkdir(mode=0o755)
            os.chmod(data, 0o755)
        backup_dir = data / "env-backups"
        if not backup_dir.exists():
            backup_dir.mkdir(mode=0o700)
            os.chmod(backup_dir, 0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup = backup_dir / f".env-{stamp}-{os.getpid()}"
        _write_private(backup, original_bytes, exclusive=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(rendered.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if hasattr(os, "chmod"):
            os.chmod(path, 0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return SaveResult(True, backup)


def redacted_summary(values: Mapping[str, str]) -> dict[str, str]:
    return {key: ("<redacted>" if key in _SECRET_KEYS and value else value) for key, value in values.items()}
