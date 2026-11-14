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
from typing import Callable, Mapping

try:
    from .config import (
        CLASSIC,
        KEYCLOAK,
        PANEL,
        PROXY_EXTERNAL,
        PROXY_NGINX,
        PROXY_TRAEFIK,
        RECONFIGURE_KEYS,
        ConfigurationError,
        InstallPlan,
        build_configuration,
        load_env,
        mode_from_environment,
        plan_from_existing,
        save_env,
        validate_domain,
        validate_username,
    )
    from .keycloak_bootstrap import KeycloakError, bootstrap_panel
    from .preflight import PreflightReport, collect_preflight, docker_install_commands, privileged_commands, read_os_release
    from .nginx_setup import NginxSetup, NginxSetupError
    from .packages import missing_nginx_packages
    from .readiness import explain_network, network_action, network_failures
    from .images import compose_command, compose_environment, prepare_images, stack_services
    from .keycloak_diagnostics import check_startup
except ImportError:  # Direct execution from the repository checkout.
    from config import (
        CLASSIC,
        KEYCLOAK,
        PANEL,
        PROXY_EXTERNAL,
        PROXY_NGINX,
        PROXY_TRAEFIK,
        RECONFIGURE_KEYS,
        ConfigurationError,
        InstallPlan,
        build_configuration,
        load_env,
        mode_from_environment,
        plan_from_existing,
        save_env,
        validate_domain,
        validate_username,
    )
    from keycloak_bootstrap import KeycloakError, bootstrap_panel
    from preflight import PreflightReport, collect_preflight, docker_install_commands, privileged_commands, read_os_release
    from nginx_setup import NginxSetup, NginxSetupError
    from packages import missing_nginx_packages
    from readiness import explain_network, network_action, network_failures
    from images import compose_command, compose_environment, prepare_images, stack_services
    from keycloak_diagnostics import check_startup

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


def ask_valid(question: str, validator: Callable[[str], str], default: str = "") -> str:
    while True:
        try:
            return validator(ask(question, default))
        except ConfigurationError as exc:
            print(f"Invalid input: {exc}")


def ask_selection(options: Mapping[str, str], default: str = "1") -> str:
    while True:
        choice = ask("Selection", default)
        if choice in options:
            return options[choice]
        print(f"Choose {', '.join(options)}.")


def interactive_plan(existing: Mapping[str, str], reconfigure: bool = False) -> InstallPlan | None:
    if existing and not reconfigure:
        print(f"Existing backend/.env detected: {MODE_LABELS[mode_from_environment(existing)]}.")
        print(f"Saved base domain: {existing.get('DOMAIN_NAME') or '(not set)'}")
        print("  1) Continue with saved settings")
        print("  2) Start setup questions again (keep credentials and data)")
        print("  3) Cancel")
        action = ask_selection({"1": "continue", "2": "restart", "3": "cancel"})
        if action == "continue":
            plan = plan_from_existing(existing)
            if plan.uses_panel and not existing.get("MAVI_PANEL_CLIENT_SECRET"):
                if confirm("Panel setup is incomplete. Create or verify the initial panel administrator?", True):
                    plan = replace(plan, create_admin=True,
                                   admin_username=ask_valid("Panel admin username", validate_username, "panel-admin"),
                                   admin_email=ask_valid("Panel admin email", valid_email, plan.acme_email or f"admin@{plan.domain}"))
            return plan
        if action == "cancel":
            return None
        reconfigure = True
    if reconfigure:
        print("Domain, proxy and derived URLs will be configured again. Existing credentials, data and certificates are kept.")
        print("The current .env is backed up and replaced only after you approve the new plan.")
    print("Select an installation mode:")
    print("  1) Web panel + Keycloak (Recommended)")
    print("  2) Classic token setup")
    print("  3) Advanced: Keycloak only")
    modes = {"1": PANEL, "2": CLASSIC, "3": KEYCLOAK}
    mode = ask_selection(modes)
    if mode == CLASSIC:
        return InstallPlan(mode, reconfigure=reconfigure)
    print("Enter the base domain, e.g. example.com; this creates auth.example.com and panel.example.com.")
    domain = ask_valid("Base domain for auth and panel subdomains", validate_domain)
    print(f"Keycloak hostname: auth.{domain}")
    if mode == PANEL:
        print(f"Panel hostname: panel.{domain}")
    proxy = ask_proxy()
    email = ""
    if proxy in (PROXY_NGINX, PROXY_TRAEFIK):
        email = ask_valid("Let's Encrypt notification email", valid_email, f"admin@{domain}")
    create_admin = mode == PANEL and confirm("Create the initial panel administrator?", True)
    username = "panel-admin"
    admin_email = email or f"admin@{domain}"
    if create_admin:
        username = ask_valid("Panel admin username", validate_username, username)
        admin_email = ask_valid("Panel admin email", valid_email, admin_email)
    return InstallPlan(mode, domain, email, proxy, create_admin, username, admin_email, reconfigure=reconfigure)


def ask_proxy() -> str:
    print("Select how auth and panel subdomains are served:")
    print("  1) Host Nginx with automatic Let's Encrypt certificates (Recommended)")
    print("  2) External reverse proxy (advanced: you terminate HTTPS yourself)")
    print("  3) Traefik containers (legacy standalone mode)")
    proxies = {"1": PROXY_NGINX, "2": PROXY_EXTERNAL, "3": PROXY_TRAEFIK}
    return ask_selection(proxies)


def argument_plan(args: argparse.Namespace, existing: Mapping[str, str]) -> InstallPlan:
    if existing and not args.reconfigure:
        plan = plan_from_existing(existing)
        if args.create_admin:
            if not plan.uses_panel:
                raise ConfigurationError("--create-admin is valid only in panel mode.")
            plan = replace(plan, create_admin=True, admin_username=validate_username(args.admin_username),
                           admin_email=valid_email(args.admin_email or plan.acme_email or f"admin@{plan.domain}"))
        return plan
    if not args.mode:
        raise ConfigurationError("--mode is required for a new non-interactive installation.")
    if args.mode == CLASSIC:
        return InstallPlan(CLASSIC, reconfigure=args.reconfigure)
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
        reconfigure=args.reconfigure,
    )


def print_keycloak_login(values: Mapping[str, str]) -> None:
    base_url = values.get("VPN_KEYCLOAK_URL") or f"https://auth.{values['DOMAIN_NAME']}"
    print(f"  Keycloak admin: {base_url.rstrip('/')}/admin/master/console/")
    print(f"  Admin account:  {values.get('KEYCLOAK_ADMIN_USER') or 'admin'} (master realm)")
    print("  Initial password: KEYCLOAK_ADMIN_PASS in backend/.env; the value is not printed.")


def print_summary(plan: InstallPlan, values: Mapping[str, str], env_path: Path, generated: set[str]) -> None:
    print("\nInstallation summary")
    print(f"  Mode:          {MODE_LABELS[plan.mode]}")
    print(f"  Compose files: {values['COMPOSE_FILE']}")
    print(f"  Environment:   {env_path}")
    print("  VPN MTU:       1280")
    print(f"  Software:      {'local Rust source build (higher RAM usage)' if plan.build_from_source else 'prebuilt Docker images; no local compilation'}")
    if plan.uses_keycloak:
        print(f"  Keycloak URL:  {values['VPN_KEYCLOAK_URL']}")
        print_keycloak_login(values)
        print(f"  Proxy mode:    {PROXY_LABELS[plan.proxy]}")
    if plan.uses_panel:
        print(f"  Panel URL:     https://panel.{values['DOMAIN_NAME']}")
        print(f"  VPN endpoint:  {values['MAVI_PANEL_VPN_ENDPOINT']}")
        print(f"  Initial admin: {'yes' if plan.create_admin else 'no'}")
    if plan.reconfigure:
        print("  Reconfigure:   setup answers and derived URLs; existing credentials and data are retained")
    if generated:
        print(f"  New secrets:   {', '.join(sorted(generated))} (values are not printed)")
    elif plan.existing:
        print("  New secrets:   none")


def print_report(report: PreflightReport) -> None:
    print("\nPreflight checks")
    for check in report.checks:
        print(f"  {check.status:<4} {check.name}: {check.detail}")


def run(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True, **({"env": env} if env is not None else {}))


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


def install_nginx_with_consent(plan: InstallPlan, non_interactive: bool, proxy_token: str = "") -> bool | None:
    """Install and configure Nginx/Certbot after showing what will run."""
    packages = missing_nginx_packages()
    commands = privileged_commands([["apt-get", "update"],
                                   ["apt-get", "install", "-y", "--no-install-recommends", *packages]]) if packages else []
    if packages:
        print("Missing Nginx/Certbot packages:")
        for command in commands:
            print(f"  {shlex.join(command)}")
        if not non_interactive and not confirm("Install the missing packages now?", True):
            print("Package installation declined. The saved configuration is kept; no Mavi containers were started.")
            return None
    else:
        print("Nginx and Certbot are already installed; reusing them.")
    try:
        for command in commands:
            run(command)
        print("Configuring the Mavi Nginx site and requesting Let's Encrypt certificates...")
        NginxSetup().install_and_configure(
            plan.domain,
            plan.uses_panel,
            plan.acme_email,
            install_packages=False,
            proxy_token=proxy_token,
        )
    except (NginxSetupError, OSError, subprocess.CalledProcessError) as exc:
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


def start_stack(backend: Path, plan: InstallPlan, values: dict[str, str]) -> int:
    flags = ["--build"] if plan.build_from_source else ["--no-build", "--pull", "never"]
    run(compose_command(backend, values, "up", "-d", *flags, *stack_services(plan, values, include_panel=False)),
        backend, env=compose_environment(values))
    if not plan.uses_panel:
        return 0
    admin = bootstrap_panel(
        values,
        backend / ".env",
        create_admin=plan.create_admin,
        admin_username=plan.admin_username,
        admin_email=plan.admin_email,
        startup_check=lambda: check_startup(backend, values),
    )
    if admin and admin.created:
        print(f"Created panel administrator: {admin.username}")
        print(f"Temporary password: {admin.temporary_password}")
        print("The password must be changed at the first login; it is not stored by the installer.")
    elif admin:
        print(f"Panel administrator already exists; password and existing roles were preserved: {admin.username}")
    run(compose_command(backend, values, "up", "-d", *flags, "panel"), backend, env=compose_environment(values))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2], help=argparse.SUPPRESS)
    result.add_argument("--check", action="store_true", help="run preflight checks without changing files or installing packages")
    result.add_argument("--reconfigure", action="store_true", help="ask setup questions again while preserving existing credentials and data")
    result.add_argument("--build-from-source", action="store_true", help="compile VPN/panel locally instead of downloading prebuilt images (advanced; uses more RAM)")
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
    result.add_argument("--no-start", action="store_true", help="write configuration without building or starting containers")
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
        plan = argument_plan(args, existing) if args.non_interactive else interactive_plan(existing, args.reconfigure)
        if plan is None:
            print("Installation cancelled; no configuration was written.")
            return 0
        if args.build_from_source:
            plan = replace(plan, build_from_source=True)
        if plan.build_from_source and plan.uses_panel and not (root / "panel" / "Dockerfile").is_file():
            raise ConfigurationError(f"Panel Dockerfile not found: {root / 'panel' / 'Dockerfile'}")
        docker_attempted = False
        while True:
            if args.build_from_source:
                plan = replace(plan, build_from_source=True)
            values, generated = build_configuration(plan, existing)
            print_summary(plan, values, env_path, generated)
            report = collect_preflight(plan, values, backend, network_required=not args.no_start, include_docker=not args.no_start)
            print_report(report)
            blockers = network_failures(report)
            if blockers:
                explain_network(report)
            if args.check:
                return 1 if report.failures else 0
            if blockers:
                action = network_action(report, plan, args.non_interactive)
                if action == "retry":
                    continue
                if action == "edit":
                    plan = interactive_plan(existing, reconfigure=True)
                    if plan is None:
                        return 0
                elif action == "external":
                    plan = replace(plan, proxy=PROXY_EXTERNAL, acme_email="", existing=False, reconfigure=True)
                elif action == "save":
                    args.no_start = True
                else:
                    print("Setup stopped before writing configuration or changing containers.")
                    return 1 if args.non_interactive else 0
                continue
            if not report.failures:
                break
            docker_failure = all(check.name in {"docker", "compose"} for check in report.failures)
            if args.no_start or docker_attempted or not docker_failure or not install_docker_with_consent(args.non_interactive):
                print("Preflight failed. No configuration or containers were changed.", file=sys.stderr)
                return 1
            docker_attempted = True
        if args.non_interactive:
            if not args.yes:
                print("Non-interactive installation requires --yes after reviewing the plan.", file=sys.stderr)
                return 2
        elif not confirm("Apply this plan?", True):
            print("Installation cancelled; no configuration was written.")
            return 0
        if not args.no_start:
            prepare_images(backend, plan, values)
        result = save_env(env_path, values, remove_keys=RECONFIGURE_KEYS if plan.reconfigure else frozenset())
        os.chmod(env_path, 0o600)
        prepare_private_paths(backend, plan.proxy == PROXY_TRAEFIK and plan.uses_keycloak)
        if result.changed:
            print(f"Wrote {env_path} with mode 0600.")
            if result.backup:
                print(f"Backed up the previous environment to {result.backup.name} with mode 0600.")
        else:
            print(f"Existing {env_path} is unchanged.")
        if not args.no_start:
            if plan.proxy == PROXY_NGINX and plan.uses_keycloak:
                nginx_ready = install_nginx_with_consent(plan, args.non_interactive, values.get("MAVI_PANEL_PROXY_TOKEN", ""))
                if nginx_ready is None:
                    return 0
                if not nginx_ready:
                    print("Nginx setup failed. The .env file was written; fix the problem and rerun the installer.", file=sys.stderr)
                    return 1
            start_stack(backend, plan, values)
        print("\nInstallation finished.")
        if plan.uses_keycloak and not args.no_start:
            print_keycloak_login(values)
        files = " ".join(f"-f {shlex.quote(file)}" for file in values["COMPOSE_FILE"].split(":"))
        print(f"Restart later with: cd backend && sudo docker compose --env-file .env {files} up -d --no-build")
        if plan.uses_panel and not args.no_start:
            print(f"Open https://panel.{values['DOMAIN_NAME']} after the panel becomes ready.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nInstallation cancelled.", file=sys.stderr)
        return 130
    except (ConfigurationError, KeycloakError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if env_path.exists():
            print("The protected .env file was preserved. Fix the error and rerun the installer; it is safe to repeat.", file=sys.stderr)
            print("To correct saved setup answers, rerun with --reconfigure.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
