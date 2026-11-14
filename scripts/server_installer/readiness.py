"""Explain recoverable DNS and proxy-port prerequisites without changing the host."""

from __future__ import annotations

import re

try:
    from .config import InstallPlan
    from .preflight import Check, PreflightReport
except ImportError:
    from config import InstallPlan
    from preflight import Check, PreflightReport


def network_failures(report: PreflightReport) -> list[Check]:
    result = []
    for check in report.failures:
        port = re.fullmatch(r"port:(\d+)/(tcp|udp)", check.name)
        if check.name.startswith("dns:") or port and 1 <= int(port.group(1)) <= 65535:
            result.append(check)
    return result


def explain_network(report: PreflightReport) -> None:
    missing = [check.name.removeprefix("dns:") for check in network_failures(report) if check.name.startswith("dns:")]
    if missing:
        print("\nDNS changes needed at your DNS provider:")
        print("  Type  Hostname                         Value")
        for hostname in missing:
            if report.public_ipv4:
                for address in report.public_ipv4:
                    print(f"  A     {hostname:<32} {address}")
            elif report.public_ipv6:
                for address in report.public_ipv6:
                    print(f"  AAAA  {hostname:<32} {address}")
            else:
                print(f"  A/AAAA {hostname:<31} your server's reachable PUBLIC IP")
        print("Use an address reachable from the Internet. Private/VPN/container addresses are not DNS targets.")
        print("If an AAAA record exists, it must also reach this server; an incorrect AAAA record can break Let's Encrypt.")
        print("After saving the DNS record, retry the checks. This installer cannot edit your DNS provider automatically.")
        print("A local /etc/hosts entry is not a substitute for public DNS.")
    if any(check.name.startswith("port:") for check in network_failures(report)):
        print("\nThe selected HTTPS proxy cannot use an occupied port owned by another service.")
        print("Choose external reverse proxy mode to keep that proxy, or free the port yourself and retry.")
        print("The installer does not stop that service or change its ports.")
    print("DNS checks and local listeners do not verify your provider firewall or public ACME reachability.")


def network_action(report: PreflightReport, plan: InstallPlan, non_interactive: bool) -> str:
    if non_interactive:
        return "cancel"
    options = [("retry", "Retry checks after fixing DNS/ports"), ("edit", "Enter setup answers again")]
    if plan.uses_keycloak and any(check.name.startswith("port:") for check in network_failures(report)):
        options.append(("external", "Use the existing external reverse proxy (configure its HTTPS routes yourself)"))
    options.extend([("save", "Save configuration only; do not install or start services"), ("cancel", "Cancel")])
    for number, (_, label) in enumerate(options, 1):
        print(f"  {number}) {label}")
    while True:
        value = input("Selection [1]: ").strip() or "1"
        if value.isdigit() and 1 <= int(value) <= len(options):
            return options[int(value) - 1][0]
        print(f"Choose a number from 1 to {len(options)}.")
