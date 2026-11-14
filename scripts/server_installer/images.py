"""Download runtime images before changing a server; never fall back to compilation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

try:
    from .config import ConfigurationError, InstallPlan, PROXY_TRAEFIK, update_env_text, uses_traefik_profile
except ImportError:
    from config import ConfigurationError, InstallPlan, PROXY_TRAEFIK, update_env_text, uses_traefik_profile


def compose_environment(values: dict[str, str]) -> dict[str, str]:
    # Planned values take precedence over stale variables exported in the shell.
    return {**os.environ, **values}


def compose_command(backend: Path, values: dict[str, str], *arguments: str, env_file: str | Path | None = None) -> list[str]:
    command = ["docker", "compose", "--project-directory", str(backend),
               "--env-file", str(env_file or backend / ".env")]
    for filename in values["COMPOSE_FILE"].split(":"):
        command += ["-f", str(backend / filename)]
    return [*command, *arguments]


def stack_services(plan: InstallPlan, values: dict[str, str], include_panel: bool = True) -> list[str]:
    services = ["vpn-server"]
    if plan.uses_keycloak:
        services += ["keycloak-db", "keycloak"]
        if plan.proxy == PROXY_TRAEFIK or uses_traefik_profile(values):
            services.append("traefik")
    if plan.uses_panel and include_panel:
        services += ["panel-db", "panel"]
    return services


def prepare_images(backend: Path, plan: InstallPlan, values: dict[str, str]) -> None:
    """Resolve and pull every selected image without starting services or editing .env."""
    if plan.build_from_source:
        print("Source build explicitly selected; Rust compilation will run on this host.")
        return
    environment = compose_environment(values)
    descriptor, name = tempfile.mkstemp(prefix=".mavi-image-check-", suffix=".env", dir=backend)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(update_env_text("", values))
        result = subprocess.run(
            compose_command(backend, values, "config", "--format", "json", env_file=name),
            cwd=backend, env=environment, capture_output=True, text=True, check=False,
        )
        if result.returncode:
            # Compose output may contain resolved credentials. Do not print it.
            raise ConfigurationError("Cannot resolve the selected Compose images. Check the Compose files; no services were changed.")
        try:
            services = json.loads(result.stdout).get("services", {})
        except (ValueError, AttributeError):
            raise ConfigurationError("Compose returned invalid image configuration; no services were changed.") from None
        images = []
        for service in stack_services(plan, values):
            config = services.get(service, {})
            image = config.get("image", "")
            if not image or config.get("build"):
                raise ConfigurationError(f"Service {service} requires an explicit image. Use --build-from-source only if you intend to compile locally.")
            if image not in images:
                images.append(image)
        print("Downloading prebuilt images before applying configuration...")
        for image in images:
            print(f"  {image}", flush=True)
            pulled = subprocess.run(["docker", "pull", image], cwd=backend, env=environment, check=False)
            if pulled.returncode:
                raise ConfigurationError(f"Could not pull {image}. No configuration or services were changed; local compilation will not be attempted.")
    finally:
        Path(name).unlink(missing_ok=True)
