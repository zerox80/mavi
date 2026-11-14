"""Detect permanent container startup failures without exposing credentials."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote
from urllib.request import Request, ProxyHandler, build_opener

try:
    from .images import compose_command, compose_environment
    from .keycloak_bootstrap import KeycloakError
except ImportError:
    from images import compose_command, compose_environment
    from keycloak_bootstrap import KeycloakError


def check_startup(backend: Path, values: dict[str, str]) -> bool:
    environment = compose_environment(values)
    try:
        result = subprocess.run(compose_command(backend, values, "ps", "-a", "-q", "keycloak"), cwd=backend,
                                env=environment, capture_output=True, text=True, timeout=10, check=False)
        if result.returncode or not result.stdout.strip():
            return False
        identifier = result.stdout.split()[0]
        result = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", identifier],
                                capture_output=True, text=True, timeout=10, check=False)
        if result.returncode:
            return False
        state = json.loads(result.stdout)
        if state.get("OOMKilled"):
            raise KeycloakError("The Keycloak container was killed for exceeding available memory. Inspect Docker's OOM state and free RAM before retrying.")
        started = state.get("StartedAt", "")
        if not started or started.startswith("0001-"):
            return False
        logs = subprocess.run(["docker", "logs", "--since", started, "--tail", "80", identifier],
                              capture_output=True, text=True, timeout=10, check=False)
        text = logs.stdout + logs.stderr
        invalid = re.search(r'Unrecognized field "([A-Za-z0-9_]{1,100})"', text)
        if invalid:
            raise KeycloakError(f"Keycloak cannot import the realm: unsupported field {invalid.group(1)!r}. Update the realm file and restart only Keycloak; do not delete its database.")
        if "ERROR: Failed to import realms" in text:
            raise KeycloakError("Keycloak's realm import failed. Inspect 'docker compose logs --tail=80 keycloak'; its database has not been removed.")
        if state.get("Status") in ("exited", "dead"):
            raise KeycloakError(f"Keycloak exited with code {state.get('ExitCode', '?')}. Inspect its container logs before retrying.")
        realm = quote(values.get("VPN_KEYCLOAK_REALM", "mavi-vpn"), safe="")
        hostname = "auth." + values["DOMAIN_NAME"]
        request = Request(f"http://127.0.0.1:18080/realms/{realm}/.well-known/openid-configuration",
                          headers={"Host": hostname, "X-Forwarded-Host": hostname, "X-Forwarded-Proto": "https"})
        # Never send this loopback diagnostic through a shell-configured HTTP proxy.
        with build_opener(ProxyHandler({})).open(request, timeout=2) as response:
            metadata = json.loads(response.read(128 * 1024))
        public = values.get("VPN_KEYCLOAK_URL") or f"https://{hostname}"
        return metadata.get("issuer") == f"{public.rstrip('/')}/realms/{realm}"
    except (OSError, ValueError, subprocess.SubprocessError):
        # Diagnostics must not replace a more useful readiness/authentication error.
        return False
