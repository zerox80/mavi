"""Interactive Linux server installation for Mavi VPN and its web panel."""

from __future__ import annotations

import argparse
from dataclasses import replace
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Mapping

try:
    from .config import (
        CLASSIC,
        KEYCLOAK,
        PANEL,
        PROXY_EXTERNAL,
        PROXY_NGINX,
        PROXY_TRAEFIK,
        ConfigurationError,
        InstallPlan,
        build_configuration,
        load_env,
        plan_from_existing,
        save_env,
        validate_domain,
        validate_username,
    )
    from .keycloak_bootstrap import KeycloakError, bootstrap_panel
    from .preflight import PreflightReport, collect_preflight, docker_install_commands, privileged_commands, read_os_release
    from .nginx_setup import NginxSetup, NginxSetupError, package_names as nginx_package_names, preflight_dns
except ImportError:  # Direct execution from the repository checkout.
    from config import (
        CLASSIC,
        KEYCLOAK,
        PANEL,
        PROXY_EXTERNAL,
        PROXY_NGINX,
        PROXY_TRAEFIK,
        ConfigurationError,
        InstallPlan,
        build_configuration,
        load_env,
        plan_from_existing,
        save_env,
        validate_domain,
        validate_username,
    )
    from keycloak_bootstrap import KeycloakError, bootstrap_panel
    from preflight import PreflightReport, collect_preflight, docker_install_commands, privileged_commands, read_os_release
    from nginx_setup import NginxSetup, NginxSetupError, package_names as nginx_package_names, preflight_dns

MODE_LABELS = {PANEL: "Web panel + Keycloak", KEYCLOAK: "Keycloak only", CLASSIC: "Classic token setup"}
PROXY_LABELS = {
    PROXY_NGINX: "host Nginx + Let's Encrypt",
    PROXY_TRAEFIK: "standalone Traefik HTTPS/ACME",
    PROXY_EXTERNAL: "external reverse proxy",
}


def ask(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or default


def confirm(question: str, default: bool = True) -> bool:
    choices = "Y/n" if default else "y/N"
    answer = input(f"{question} [{choices}]: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def valid_email(value: str) -> str:
    value = value.strip()
    if len(value) > 254 or not value or any(character.isspace() for character in value):
        raise ConfigurationError("Enter a valid email address.")
    local, separator, domain = value.rpartition("@")
    if not separator or not local or "." not in domain:
        raise ConfigurationError("Enter a valid email address.")
    return value


def interactive_plan(existing: Mapping[str, str]) -> InstallPlan:
    if existing:
        plan = plan_from_existing(existing)
        print(f"Existing backend/.env detected: {MODE_LABELS[plan.mode]}.")
        print("The installation mode and existing secrets will be preserved.")
        return plan
    print("Select an installation mode:")
    print("  1) Web panel + Keycloak (Recommended)")
    print("  2) Classic token setup")
    print("  3) Advanced: Keycloak only")
    choice = ask("Selection", "1")
    modes = {"1": PANEL, "2": CLASSIC, "3": KEYCLOAK}
    if choice not in modes:
        raise ConfigurationError("Choose 1, 2, or 3.")
    mode = modes[choice]
    if mode == CLASSIC:
        return InstallPlan(mode)
    domain = validate_domain(ask("Base domain for auth and panel subdomains"))
    proxy = ask_proxy()
    email = ""
    if proxy in (PROXY_NGINX, PROXY_TRAEFIK):
        email = valid_email(ask("Let's Encrypt notification email", f"admin@{domain}"))
    create_admin = mode == PANEL and confirm("Create the initial panel administrator?", True)
    username = "panel-admin"
    admin_email = email or f"admin@{domain}"
    if create_admin:
        username = validate_username(ask("Panel admin username", username))
        admin_email = valid_email(ask("Panel admin email", admin_email))
    return InstallPlan(mode, domain, email, proxy, create_admin, username, admin_email)


def ask_proxy() -> str:
    print("Select how auth and panel subdomains are served:")
    print("  1) Host Nginx with automatic Let's Encrypt certificates (Recommended)")
    print("  2) External reverse proxy (advanced: you terminate HTTPS yourself)")
    print("  3) Traefik containers (legacy standalone mode)")
    choice = ask("Selection", "1")
    proxies = {"1": PROXY_NGINX, "2": PROXY_EXTERNAL, "3": PROXY_TRAEFIK}
    if choice not in proxies:
        raise ConfigurationError("Choose 1, 2, or 3.")
    return proxies[choice]


def argument_plan(args: argparse.Namespace, existing: Mapping[str, str]) -> InstallPlan:
    if existing:
        return plan_from_existing(existing)
    if not args.mode:
        raise ConfigurationError("--mode is required for a new non-interactive installation.")
    if args.mode == CLASSIC:
        return InstallPlan(CLASSIC)
    proxy = args.proxy or PROXY_NGINX
    domain = validate_domain(args.domain)
    email = valid_email(args.acme_email or f"admin@{domain}") if proxy in (PROXY_NGINX, PROXY_TRAEFIK) else ""
    if args.create_admin and args.mode != PANEL:
        raise ConfigurationError("--create-admin is valid only in panel mode.")
    if args.create_admin and not args.admin_email and not email:
        raise ConfigurationError("--admin-email is required with --create-admin behind an external proxy.")
    return InstallPlan(
        mode=args.mode,
        domain=domain,
        acme_email=email,
        proxy=proxy,
        create_admin=args.create_admin,
        admin_username=validate_username(args.admin_username),
        admin_email=valid_email(args.admin_email or email) if args.create_admin else "",
    )


def print_summary(plan: InstallPlan, values: Mapping[str, str], env_path: Path, generated: set[str]) -> None:
    print("\nInstallation summary")
    print(f"  Mode:          {MODE_LABELS[plan.mode]}")
    print(f"  Compose files: {values['COMPOSE_FILE']}")
    print(f"  Environment:   {env_path}")
    print("  VPN MTU:       1280")
    if plan.uses_keycloak:
        print(f"  Keycloak URL:  {values['VPN_KEYCLOAK_URL']}")
        print(f"  Proxy mode:    {PROXY_LABELS[plan.proxy]}")
    print(f"  Images:        {'built from source on this host' if plan.source_build else 'prebuilt, pulled from the registry'}")
    if plan.uses_panel:
        print(f"  Panel URL:     https://panel.{values['DOMAIN_NAME']}")
        print(f"  VPN endpoint:  {values['MAVI_PANEL_VPN_ENDPOINT']}")
        print(f"  Initial admin: {'yes' if plan.create_admin else 'no'}")
    if generated:
        print(f"  New secrets:   {', '.join(sorted(generated))} (values are not printed)")
    elif plan.existing:
        print("  New secrets:   none")


def print_report(report: PreflightReport) -> None:
    print("\nPreflight checks")
    for check in report.checks:
        print(f"  {check.status:<4} {check.name}: {check.detail}")


def run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def install_docker_with_consent(non_interactive: bool) -> bool:
    commands = docker_install_commands(read_os_release())
    if not commands:
        return False
    if shutil.which("systemctl"):
        commands.append(["systemctl", "enable", "--now", "docker"])
    try:
        commands = privileged_commands(commands)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return False
    print("Docker installation requires these system commands:")
    for command in commands:
        print(f"  {shlex.join(command)}")
    if non_interactive or not confirm("Run these Docker installation commands now?", False):
        return False
    for command in commands:
        run(command)
    return True


def install_nginx_with_consent(plan: InstallPlan, non_interactive: bool) -> bool:
    """Install and configure Nginx/Certbot after showing what will run."""
    packages = nginx_package_names()
    print("The host Nginx proxy requires these system packages:")
    print(f"  {', '.join(packages)}")
    if not non_interactive and not confirm("Install these packages now?", False):
        return False
    print("Configuring Nginx and requesting Let's Encrypt certificates...")
    try:
        NginxSetup().install_and_configure(
            plan.domain,
            plan.uses_panel,
            plan.acme_email,
            install_packages=True,
        )
    except (NginxSetupError, OSError) as exc:
        print(f"Error: Nginx setup failed: {exc}", file=sys.stderr)
        print("Certificates and the Mavi Nginx site were rolled back on failure.", file=sys.stderr)
        return False
    print("Nginx is configured and the certificate renewal timer is active.")
    return True


def prepare_private_paths(backend: Path, standalone: bool) -> None:
    data = backend / "data"
    if not data.exists():
        data.mkdir(mode=0o755)
        os.chmod(data, 0o755)
    if standalone:
        directory = backend / "letsencrypt"
        if not directory.exists():
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
        acme = directory / "acme.json"
        if not acme.exists():
            descriptor = os.open(acme, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        os.chmod(acme, 0o600)


def start_services(backend: Path, services: list[str], source_build: bool) -> None:
    """Start services from prebuilt images, or compile them when the plan asks for it."""
    if source_build:
        run(["docker", "compose", "up", "-d", "--build", *services], backend)
        return
    try:
        run(["docker", "compose", "pull", *services], backend)
    except subprocess.CalledProcessError:
        print(
            "Could not pull the container images. Check registry access, or rerun with "
            "--build-from-source to compile them on this host.",
            file=sys.stderr,
        )
        raise
    run(["docker", "compose", "up", "-d", *services], backend)


def start_stack(backend: Path, plan: InstallPlan, values: dict[str, str], generated: set[str]) -> int:
    services = ["vpn-server"]
    if plan.uses_keycloak:
        services += ["keycloak-db", "keycloak"]
        if plan.proxy == PROXY_TRAEFIK:
            services.append("traefik")
    start_services(backend, services, plan.source_build)
    if not plan.uses_panel:
        return 0
    result = bootstrap_panel(
        values,
        create_admin=plan.create_admin,
        admin_username=plan.admin_username,
        admin_email=plan.admin_email,
        adopt_existing_secret="MAVI_PANEL_CLIENT_SECRET" in generated,
    )
    if result.client_secret != values["MAVI_PANEL_CLIENT_SECRET"]:
        values["MAVI_PANEL_CLIENT_SECRET"] = result.client_secret
        save_env(backend / ".env", values)
    if result.admin and result.admin.created:
        print(f"Created panel administrator: {result.admin.username}")
        print(f"Temporary password: {result.admin.temporary_password}")
        print("The password must be changed at the first login; it is not stored by the installer.")
    elif result.admin:
        print(f"Panel administrator already exists; password and existing roles were preserved: {result.admin.username}")
    start_services(backend, ["panel"], plan.source_build)
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2], help=argparse.SUPPRESS)
    result.add_argument("--check", action="store_true", help="run preflight checks without changing files or installing packages")
    result.add_argument("--non-interactive", action="store_true")
    result.add_argument("--mode", choices=(PANEL, CLASSIC, KEYCLOAK))
    result.add_argument("--domain", default="")
    result.add_argument("--acme-email", default="")
    result.add_argument("--proxy", choices=(PROXY_NGINX, PROXY_TRAEFIK, PROXY_EXTERNAL), default="",
                        help="reverse proxy mode for Keycloak/panel subdomains (default: nginx)")
    result.add_argument("--create-admin", action="store_true")
    result.add_argument("--admin-username", default="panel-admin")
    result.add_argument("--admin-email", default="")
    result.add_argument("--yes", action="store_true", help="apply a non-interactive plan")
    result.add_argument("--no-start", action="store_true", help="write configuration without pulling or starting containers")
    result.add_argument("--build-from-source", action="store_true",
                        help="compile the server images on this host instead of pulling prebuilt images")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.project_root.resolve()
    backend = root / "backend"
    env_path = backend / ".env"
    try:
        if not (backend / "docker-compose.yml").is_file():
            raise ConfigurationError(f"Backend Compose file not found: {backend / 'docker-compose.yml'}")
        existing = load_env(env_path)
        plan = argument_plan(args, existing) if args.non_interactive else interactive_plan(existing)
        if args.build_from_source:
            plan = replace(plan, source_build=True)
        if plan.uses_panel and plan.source_build and not (root / "panel" / "Dockerfile").is_file():
            raise ConfigurationError(f"Panel Dockerfile not found: {root / 'panel' / 'Dockerfile'}")
        values, generated = build_configuration(plan, existing)
        print_summary(plan, values, env_path, generated)
        report = collect_preflight(plan, values, backend)
        print_report(report)
        if plan.proxy == PROXY_NGINX and plan.uses_keycloak:
            dns = preflight_dns(plan.domain, plan.uses_panel)
            for warning in dns.get("warnings", []):
                print(f"Warning: {warning}")
        if args.check:
            return 1 if report.failures else 0
        if report.failures:
            docker_failure = all(check.name in {"docker", "compose"} for check in report.failures)
            if not docker_failure or not install_docker_with_consent(args.non_interactive):
                print("Preflight failed. No configuration or containers were changed.", file=sys.stderr)
                return 1
            report = collect_preflight(plan, values, backend)
            print_report(report)
            if report.failures:
                print("Preflight still fails after Docker installation.", file=sys.stderr)
                return 1
        if args.non_interactive:
            if not args.yes:
                print("Non-interactive installation requires --yes after reviewing the plan.", file=sys.stderr)
                return 2
        elif not confirm("Apply this plan?", True):
            print("Installation cancelled; no configuration was written.")
            return 0
        result = save_env(env_path, values)
        os.chmod(env_path, 0o600)
        prepare_private_paths(backend, plan.proxy == PROXY_TRAEFIK and plan.uses_keycloak)
        if result.changed:
            print(f"Wrote {env_path} with mode 0600.")
            if result.backup:
                print(f"Backed up the previous environment to {result.backup.name} with mode 0600.")
        else:
            print(f"Existing {env_path} is unchanged.")
        if plan.proxy == PROXY_NGINX and plan.uses_keycloak:
            if not install_nginx_with_consent(plan, args.non_interactive):
                print("Nginx setup failed. The .env file was written; fix the problem and rerun the installer.", file=sys.stderr)
                return 1
        if not args.no_start:
            start_stack(backend, plan, values, generated)
        print("\nInstallation finished.")
        print("Restart later with: cd backend && docker compose up -d")
        if plan.source_build:
            print("Update later with:  git pull --ff-only && cd backend && docker compose up -d --build")
        else:
            print("Update later with:  git pull --ff-only && cd backend && docker compose pull && docker compose up -d")
        if plan.uses_panel and not args.no_start:
            print(f"Open https://panel.{values['DOMAIN_NAME']} after the panel becomes ready.")
        return 0
    except KeyboardInterrupt:
        print("\nInstallation cancelled.", file=sys.stderr)
        return 130
    except (ConfigurationError, KeycloakError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if env_path.exists():
            print("The protected .env file was preserved. Fix the error and rerun the installer; it is safe to repeat.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
