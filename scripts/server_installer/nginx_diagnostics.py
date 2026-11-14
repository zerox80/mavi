"""Bounded command diagnostics and exact-host collisions in Nginx's config dump."""
from __future__ import annotations

from pathlib import Path
import re
import shlex
import subprocess


def command_details(error: OSError | subprocess.CalledProcessError, secrets: tuple[str, ...] = ()) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        output = error.stderr or ""
        # nginx -T can dump private proxy headers. Never publish its stdout.
        if not output and list(error.cmd[:2]) != ["nginx", "-T"]:
            output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output)
        for secret in secrets:
            if secret:
                output = output.replace(secret, "<redacted>")
        lines = output.strip().splitlines()[-18:]
        return "\n".join(lines)[-3000:] or f"Command exited with status {error.returncode}."
    return str(error)


def foreign_hosts(dump: str, hostnames: list[str], managed: tuple[Path, ...]) -> dict[str, str]:
    """Find exact existing virtual hosts; wildcard/default sites can coexist."""
    own_files = {path.resolve(strict=False) for path in managed}
    sections = re.split(r"(?m)^# configuration file (.+):\s*$", dump)
    found: dict[str, str] = {}
    for index in range(1, len(sections), 2):
        filename = sections[index]
        if Path(filename).resolve(strict=False) in own_files:
            continue
        lexer = shlex.shlex(sections[index + 1], posix=True, punctuation_chars="{};")
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            continue  # nginx -T already validates syntax; unusual regex quoting is left to Nginx.
        for offset, token in enumerate(tokens):
            if token != "server_name" or offset and tokens[offset - 1].strip("{};"):
                continue
            for name in tokens[offset + 1:]:
                if ";" in name:
                    break
                if name.lower() in hostnames:
                    found[name.lower()] = filename
    return found
