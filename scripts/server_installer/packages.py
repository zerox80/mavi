"""Detect missing proxy packages without reinstalling an existing web server."""
from __future__ import annotations

import shutil
import subprocess
import re


def _nginx_plugin_available(executable: str) -> bool:
    try:
        result = subprocess.run([executable, "plugins", "--non-interactive"],
                                capture_output=True, text=True, check=False, timeout=30)
        return result.returncode == 0 and bool(re.search(r"(?m)^\s*\* nginx\s*$", result.stdout))
    except (OSError, subprocess.SubprocessError):
        return False


def missing_nginx_packages() -> list[str]:
    missing = []
    certbot = shutil.which("certbot")
    has_plugin = bool(certbot and _nginx_plugin_available(certbot))
    for package, executable in (("nginx", "nginx"), ("certbot", "certbot"), ("python3-certbot-nginx", "")):
        if package in {"certbot", "python3-certbot-nginx"} and has_plugin:
            continue
        if executable and shutil.which(executable):
            continue
        if shutil.which("dpkg-query"):
            try:
                result = subprocess.run(["dpkg-query", "-W", "-f=${Status}", package],
                                        capture_output=True, text=True, check=False, timeout=10)
                if not result.returncode and result.stdout.strip() == "install ok installed":
                    continue
            except (OSError, subprocess.SubprocessError):
                pass
        missing.append(package)
    return missing
