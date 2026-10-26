#!/usr/bin/env bash
set -euo pipefail

build_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v docker >/dev/null 2>&1; then
    echo 'Docker fehlt in WSL/Linux. Docker Desktop WSL-Integration aktivieren oder Docker Engine installieren.' >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo 'Das Docker Compose Plugin wird in WSL/Linux benoetigt.' >&2
    exit 1
fi
if [[ "$(docker info --format '{{.OSType}}')" != linux ]]; then
    echo 'Eine laufende Linux Docker Engine wird benoetigt.' >&2
    exit 1
fi

mkdir -p "$build_dir/output"
LOCAL_UID="$(id -u)"
LOCAL_GID="$(id -g)"
export LOCAL_UID LOCAL_GID
compose=(docker compose --file "$build_dir/compose.yaml")
"${compose[@]}" build --pull android
"${compose[@]}" run --rm --no-deps -T android "$@"
printf '\nBuild erfolgreich. Ergebnisse: %s/output\n' "$build_dir"
