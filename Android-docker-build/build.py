#!/usr/bin/env python3
"""Run inside the Linux container; export only artifacts and the refreshed lock."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile


FORKS = ("h2", "quinn", "quinn-proto", "quinn-udp", "h3", "h3-quinn", "h3-datagram")
ABIS = ("armeabi-v7a", "arm64-v8a", "x86", "x86_64")
OUTPUT_DIRS = ("apk", "bundle", "reports", "test-results")
IGNORE = shutil.ignore_patterns(
    ".git", ".gradle", ".idea", ".kotlin", "build", "target", "node_modules",
    "jniLibs", "local.properties", ".env", ".env.*", "*.jks", "*.keystore",
    "*.pem", "*.key", "*.p12", "*.apk", "*.aab", "__pycache__",
)


def stage_sources(source: Path, workspace: Path) -> None:
    """Use a fresh Linux filesystem, independent of Windows SDK paths/builds."""
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ("Cargo.toml", "Cargo.lock", "README.md"):
        shutil.copyfile(source / name, workspace / name)
    (workspace / ".cargo").mkdir()
    shutil.copyfile(source / ".cargo/config.toml", workspace / ".cargo/config.toml")
    shutil.copytree(source / "android", workspace / "android", ignore=IGNORE)
    manifest = tomllib.loads((source / "Cargo.toml").read_text(encoding="utf-8"))
    for member in manifest["workspace"]["members"]:
        if member.startswith("android/"):
            continue
        destination = workspace / member
        destination.mkdir(parents=True, exist_ok=True)
        # Cargo resolves the complete workspace, even when only mavivpn is built.
        for name in ("Cargo.toml", "build.rs", "src", "tests", "benches", "examples"):
            item = source / member / name
            if item.is_dir():
                shutil.copytree(item, destination / name, ignore=IGNORE)
            elif item.is_file():
                shutil.copyfile(item, destination / name)
    (workspace / "scripts").mkdir()
    shutil.copyfile(
        source / "scripts/check_fork_sources.py",
        workspace / "scripts/check_fork_sources.py",
    )
    wrapper = workspace / "android/gradlew"
    wrapper.write_bytes(wrapper.read_bytes().replace(b"\r\n", b"\n"))


def verify_native_libraries(archive: Path) -> None:
    prefix = "base/lib" if archive.suffix == ".aab" else "lib"
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
    missing = [abi for abi in ABIS if f"{prefix}/{abi}/libmavivpn.so" not in names]
    if missing:
        raise RuntimeError(f"{archive.name}: missing libmavivpn.so for {', '.join(missing)}")


def export_results(build: Path, output: Path, success: bool) -> None:
    # Validate all packages before exporting any of them.
    if success:
        for extension in ("apk", "aab"):
            for archive in (build / "outputs").rglob(f"*.{extension}"):
                verify_native_libraries(archive)
    # Failed builds still provide test diagnostics, but no APK/AAB.
    for name in OUTPUT_DIRS:
        if name in ("apk", "bundle"):
            if not success:
                continue
            directory = build / "outputs" / name
        else:
            directory = build / name
        if directory.exists():
            shutil.copytree(directory, output / name)


def restore_output_owner(output: Path) -> None:
    uid = int(os.environ.get("LOCAL_UID", "0"))
    gid = int(os.environ.get("LOCAL_GID", "0"))
    for path in (output, *output.rglob("*")):
        os.chown(path, uid, gid)


def main(arguments: list[str]) -> int:
    workspace = Path("/workspace")
    output = Path("/output")
    output.mkdir(parents=True, exist_ok=True)
    # Never leave an old APK looking like the result of a failed new build.
    for name in OUTPUT_DIRS:
        if (output / name).exists():
            shutil.rmtree(output / name)
    try:
        stage_sources(Path("/source"), workspace)
        update = ["cargo", "update"]
        for package in FORKS:
            update.extend(("-p", package))
        subprocess.run(update, cwd=workspace, check=True)
        subprocess.run(
            ["python3", "scripts/check_fork_sources.py", "--check-remote"],
            cwd=workspace, check=True,
        )
        # Write through the dedicated file mount; preserve its host ownership.
        shutil.copyfile(workspace / "Cargo.lock", "/lock/Cargo.lock")
        result = subprocess.run(
            ["bash", "./gradlew", "--no-daemon", "--console=plain", *(
                arguments or ["assembleDebug"]
            )],
            cwd=workspace / "android", check=False,
        )
        export_results(workspace / "android/app/build", output, result.returncode == 0)
        return result.returncode
    finally:
        restore_output_owner(output)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"Android Docker build failed: {error}", file=sys.stderr)
        sys.exit(1)
