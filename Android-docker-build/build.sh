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
builder_dir=''
cleanup() {
    if [[ -n "$builder_dir" ]]; then
        rm -rf -- "$builder_dir"
    fi
}
trap cleanup EXIT
if [[ "${MAVI_ANDROID_SKIP_IMAGE_BUILD:-0}" != 1 ]]; then
    if [[ -z "${MAVI_ANDROID_BUILD_CONTEXT:-}" ]]; then
        builder_dir="$(mktemp -d -t mavi-android-build.XXXXXXXX)"
        git clone --depth 1 --branch main https://github.com/zerox80/mavi-android-build.git "$builder_dir"
        MAVI_ANDROID_BUILD_CONTEXT="$builder_dir"
        export MAVI_ANDROID_BUILD_CONTEXT
    fi
    "${compose[@]}" build --pull android
fi
"${compose[@]}" run --rm --no-deps -T android "$@"
printf '\nBuild erfolgreich. Ergebnisse: %s/output\n' "$build_dir"
