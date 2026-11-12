#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Error: Python 3 is required. Install it with your distribution package manager.' >&2
    exit 1
fi
cd "$ROOT"
exec python3 -m scripts.server_installer.installer "$@"
