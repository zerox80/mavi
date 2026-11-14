"""Detect missing proxy packages without reinstalling an existing web server."""
from __future__ import annotations

import shutil
import subprocess


def missing_nginx_packages() -> list[str]:
    missing = []
    for package, executable in (("nginx", "nginx"), ("certbot", "certbot"), ("python3-certbot-nginx", "")):
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
