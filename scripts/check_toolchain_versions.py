#!/usr/bin/env python3
"""Keep the declared toolchain versions consistent and honest.

Each version has exactly one source of truth; everything else is verified
against it so a bump can never be forgotten silently:

* ``Cargo.toml`` ``workspace.package.rust-version`` is the minimum supported
  Rust version. CI builds on ``stable``, so cargo itself never notices when a
  dependency update raises the floor above it. This script compares it with
  the ``rust-version`` declared by every locked dependency, and checks that
  README.md and AGENTS.md quote the same version.
* ``.node-version`` is the only place workflows read the Node.js version from.
  AGENTS.md must quote its major version.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?\Z")
NODE_VERSION = re.compile(r"(\d+)(?:\.\d+){0,2}\Z")
SOURCE_HINT = "update scripts/check_toolchain_versions.py if the wording changed"

RUST_DOC_PATTERNS = {
    "README.md": (r"Rust-(\d+\.\d+)\+", r"Rust (\d+\.\d+) or newer"),
    "AGENTS.md": (r"Rust (\d+\.\d+)\+",),
}
NODE_DOC_PATTERNS = {"AGENTS.md": (r"Node\.js (\d+)\b",)}


def parse_version(text: str) -> tuple[int, int, int]:
    match = VERSION.match(text.strip())
    if not match:
        raise ValueError(f"not a Rust version: {text!r}")
    major, minor, patch = match.groups(default="0")
    return int(major), int(minor), int(patch)


def dependency_floor(metadata: dict) -> list[tuple[tuple[int, int, int], str, str, str]]:
    """Declared rust-version of every locked non-workspace package, highest first."""
    members = set(metadata["workspace_members"])
    found = [
        (parse_version(package["rust_version"]), package["name"], package["version"], package["rust_version"])
        for package in metadata["packages"]
        if package.get("rust_version") and package["id"] not in members
    ]
    return sorted(found, key=lambda item: item[0], reverse=True)


def check_msrv(msrv: str, metadata: dict, errors: list[str]) -> str:
    floor = dependency_floor(metadata)
    required = [item for item in floor if item[0] > parse_version(msrv)]
    if required:
        listed = ", ".join(f"{name} {version} (needs {declared})" for _, name, version, declared in required[:5])
        errors.append(
            f'Cargo.toml rust-version = "{msrv}" is lower than locked dependencies require: {listed}. '
            f'Raise workspace.package.rust-version to {required[0][3]}.'
        )
    if not floor:
        return "no dependency declares a rust-version"
    _, name, version, declared = floor[0]
    return f"highest dependency floor {declared} ({name} {version})"


def check_doc_mentions(texts: dict[str, str], patterns: dict[str, tuple[str, ...]], expected: str, label: str, errors: list[str]) -> None:
    for filename, file_patterns in patterns.items():
        for pattern in file_patterns:
            mentions = re.findall(pattern, texts.get(filename, ""))
            if not mentions:
                errors.append(f"{filename}: no mention matching /{pattern}/ found; {SOURCE_HINT}.")
            for mention in mentions:
                if mention != expected:
                    errors.append(f"{filename}: states {label} {mention}, but the source of truth says {expected}.")


def check_node_file(text: str, errors: list[str]) -> str:
    version = text.strip()
    if not NODE_VERSION.match(version):
        errors.append(f".node-version must hold a plain version such as 24 or 24.15.0, found {version!r}.")
        return ""
    return version.split(".")[0]


def load_metadata(path: Path | None, errors: list[str]) -> dict | None:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    result = subprocess.run(
        ["cargo", "metadata", "--locked", "--format-version", "1"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        errors.append("cargo metadata --locked failed:\n" + result.stderr.strip()[-2000:])
        return None
    return json.loads(result.stdout)


def read_docs(filenames) -> dict[str, str]:
    return {name: (ROOT / name).read_text(encoding="utf-8") for name in filenames}


def run(metadata_path: Path | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    docs = read_docs(set(RUST_DOC_PATTERNS) | set(NODE_DOC_PATTERNS))

    manifest = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
    msrv = manifest["workspace"]["package"]["rust-version"]
    major, minor, _ = parse_version(msrv)
    check_doc_mentions(docs, RUST_DOC_PATTERNS, f"{major}.{minor}", "Rust", errors)
    metadata = load_metadata(metadata_path, errors)
    if metadata is not None:
        notes.append(f"Rust {msrv} covers all locked dependencies: {check_msrv(msrv, metadata, errors)}")

    node_major = check_node_file((ROOT / ".node-version").read_text(encoding="utf-8"), errors)
    if node_major:
        check_doc_mentions(docs, NODE_DOC_PATTERNS, node_major, "Node.js", errors)
        notes.append(f"Node.js major {node_major} matches the docs")
    return errors, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--metadata", type=Path, help="read saved `cargo metadata` JSON instead of running cargo")
    errors, notes = run(parser.parse_args(argv).metadata)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        return 1
    for note in notes:
        print(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
