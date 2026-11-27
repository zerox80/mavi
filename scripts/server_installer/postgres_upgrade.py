"""Upgrade bundled PostgreSQL data to the major version named in the Compose files.

PostgreSQL data files only work with the major version that wrote them, and the
official 18+ images keep each cluster in /var/lib/postgresql/<major>/docker
instead of /var/lib/postgresql/data. For every database service whose data is
older than its Compose image this module:

1. stops the database and the applications that use it,
2. dumps every database with the old server into backend/backups/postgres/,
3. restores the dumps into a new cluster created by the Compose image,
4. compares the exact row count of every table, and only then
5. activates the new cluster and moves pre-18 files to <old major>/docker.

Nothing is deleted. The old cluster stays next to the new one as a rollback copy,
and a failed run leaves the previous data exactly as it was.

From the repository root: sudo python3 -m scripts.server_installer.postgres_upgrade
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import IO, Any, Mapping

try:
    from .config import ConfigurationError, load_env
    from .images import compose_command, compose_environment
except ImportError:
    from config import ConfigurationError, load_env
    from images import compose_command, compose_environment

# Database service -> application services that must not write during the upgrade.
DATABASE_SERVICES = {"keycloak-db": ("keycloak",), "panel-db": ("panel",)}
DATA_ROOT = "/var/lib/postgresql"
LEGACY_DATA = f"{DATA_ROOT}/data"
FIRST_VERSIONED_LAYOUT = 18
READY_TIMEOUT = 180.0

# Every script below receives paths and version numbers as positional arguments.
PROBE = """cd /pgroot || exit 1
if [ -e PG_VERSION ]; then echo "root $(cat PG_VERSION)"; fi
for f in */docker/PG_VERSION; do
  if [ -s "$f" ]; then echo "cluster ${f%%/*} $(cat "$f")"; fi
done"""
REMOVE_STAGING = 'rm -rf -- "/pgroot/$1/migrating"'
ACTIVATE = """set -eu
cd "/pgroot/$1"
if [ -e docker ]; then echo "$1/docker already exists" >&2; exit 1; fi
mv migrating docker"""
MOVE_LEGACY = """set -eu
cd /pgroot
mkdir -p "$1/docker"
chown "$(stat -c %u:%g .)" "$1" "$1/docker"
chmod 700 "$1/docker"
for entry in * .[!.]* ..?*; do
  if [ ! -e "$entry" ] && [ ! -L "$entry" ]; then continue; fi
  case "$entry" in PG_VERSION|"$1"|"$2") continue ;; esac
  mv -- "$entry" "$1/docker/"
done
mv -- PG_VERSION "$1/docker/"
"""
LIST_DATABASES = "SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate ORDER BY 1"
EXTRA_ROLES = "SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_' AND rolname <> current_user ORDER BY 1"
ROW_COUNTS = (
    "SELECT format('%I.%I', n.nspname, c.relname) || '=' || (xpath('/row/c/text()', query_to_xml("
    "format('SELECT count(*) AS c FROM ONLY %I.%I', n.nspname, c.relname), false, true, '')))[1]::text "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND n.nspname NOT LIKE 'pg\\_toast%' ORDER BY 1"
)


class UpgradeError(ConfigurationError):
    """The PostgreSQL data cannot be upgraded safely."""


@dataclass(frozen=True)
class PostgresService:
    service: str
    image: str
    source: str  # Docker volume name or absolute host directory
    volume: bool
    mount_target: str
    user: str
    database: str
    password: str = field(repr=False)
    dependents: tuple[str, ...] = ()


@dataclass(frozen=True)
class Layout:
    legacy: int | None = None  # pre-18 cluster at the mount root
    clusters: Mapping[str, int] = field(default_factory=dict)  # "<dir>/docker" -> major version


@dataclass(frozen=True)
class Upgrade:
    target: PostgresService
    source_major: int
    target_major: int
    cluster: str | None  # directory of an 18+ source cluster; None for pre-18 data at the root
    finish_only: bool = False  # new cluster already active; pre-18 files still need moving

    def describe(self) -> str:
        where = f"volume {self.target.source}" if self.target.volume else self.target.source
        if self.finish_only:
            return f"{self.target.service}: move PostgreSQL {self.source_major} files aside ({where})"
        return f"{self.target.service}: PostgreSQL {self.source_major} -> {self.target_major} ({where})"


def docker(*arguments: str, env: Mapping[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["docker", *arguments], env=env, capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise UpgradeError(_failure(arguments, result.stderr))
    return result


def docker_stream(*arguments: str, stdin: IO[bytes] | None = None, stdout: IO[bytes] | None = None) -> None:
    result = subprocess.run(["docker", *arguments], stdin=stdin, stdout=stdout or subprocess.DEVNULL,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise UpgradeError(_failure(arguments, result.stderr.decode("utf-8", "replace")))


def _failure(arguments: tuple[str, ...], stderr: str) -> str:
    # Secrets only travel through the environment, so the leading arguments are safe to show.
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return f"docker {' '.join(arguments[:3])} failed" + (f": {lines[-1]}" if lines else "")


def compose(backend: Path, values: Mapping[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(compose_command(backend, dict(values), *arguments), cwd=backend,
                            env=compose_environment(dict(values)), capture_output=True, text=True, check=False)
    if result.returncode:
        # Compose output may contain resolved credentials. Do not print it.
        raise UpgradeError(f"docker compose {arguments[0]} failed with exit code {result.returncode}.")
    return result


def compose_services(graph: Mapping[str, Any]) -> list[PostgresService]:
    services = graph.get("services") or {}
    volumes = graph.get("volumes") or {}
    found = []
    for service, dependents in DATABASE_SERVICES.items():
        config = services.get(service)
        if config is None:
            continue
        mounts = [mount for mount in config.get("volumes") or [] if mount.get("target") in (DATA_ROOT, LEGACY_DATA)]
        if len(mounts) != 1 or mounts[0].get("type") not in ("volume", "bind"):
            raise UpgradeError(f"{service} needs exactly one volume or directory mounted at {DATA_ROOT}.")
        mount = mounts[0]
        volume = mount["type"] == "volume"
        source = (volumes.get(mount["source"]) or {}).get("name", mount["source"]) if volume else mount["source"]
        environment = config.get("environment") or {}
        user = environment.get("POSTGRES_USER") or "postgres"
        found.append(PostgresService(
            service, config["image"], source, volume, mount["target"], user,
            environment.get("POSTGRES_DB") or user, environment.get("POSTGRES_PASSWORD") or "",
            tuple(name for name in dependents if name in services),
        ))
    return found


def parse_layout(output: str) -> Layout:
    legacy, clusters = None, {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "root":
            if not parts[1].isdigit():
                raise UpgradeError(f"Unsupported PostgreSQL data version {parts[1]}; upgrade it manually.")
            legacy = int(parts[1])
        elif len(parts) == 3 and parts[0] == "cluster" and parts[1].isdigit() and parts[2].isdigit():
            clusters[parts[1]] = int(parts[2])
    return Layout(legacy, clusters)


def plan_upgrade(target: PostgresService, target_major: int, layout: Layout) -> Upgrade | None:
    name = target.service
    current = layout.clusters.get(str(target_major))
    if current is not None and current != target_major:
        raise UpgradeError(f"{name}: {target_major}/docker contains PostgreSQL {current} data.")
    newest = max([*layout.clusters.values(), layout.legacy or 0])
    if newest > target_major:
        raise UpgradeError(f"{name}: found PostgreSQL {newest} data, newer than the Compose image "
                           f"(PostgreSQL {target_major}). Downgrades are not supported.")
    if current is not None:
        return None if layout.legacy is None else Upgrade(target, layout.legacy, target_major, None, finish_only=True)
    if layout.legacy is not None:
        if layout.clusters:
            raise UpgradeError(f"{name}: found both pre-18 data and {sorted(layout.clusters)} clusters; upgrade manually.")
        return Upgrade(target, layout.legacy, target_major, None)
    if layout.clusters:
        directory, version = max(layout.clusters.items(), key=lambda item: item[1])
        return Upgrade(target, version, target_major, directory)
    return None


def postgres_major(image: str) -> int:
    inspect = ("image", "inspect", "--format", "{{json .Config.Env}}", image)
    result = docker(*inspect, check=False)
    if result.returncode:
        print(f"Downloading {image} ...", flush=True)
        if subprocess.run(["docker", "pull", image], check=False).returncode:
            raise UpgradeError(f"Could not pull {image}.")
        result = docker(*inspect)
    for item in json.loads(result.stdout or "null") or []:
        key, _, value = item.partition("=")
        if key == "PG_MAJOR" and value.isdigit():
            return int(value)
    raise UpgradeError(f"{image} does not declare PG_MAJOR; only official postgres images are supported.")


def repository(image: str) -> str:
    name = image.split("@", 1)[0]
    return name.rsplit(":", 1)[0] if ":" in name.rsplit("/", 1)[-1] else name


def has_data(target: PostgresService) -> bool:
    if target.volume:
        return docker("volume", "inspect", target.source, check=False).returncode == 0
    return Path(target.source).is_dir()


def helper(target: PostgresService, script: str, *arguments: str, read_only: bool = False, check: bool = True) -> str:
    mount = f"{target.source}:/pgroot" + (":ro" if read_only else "")
    return docker("run", "--rm", "--network", "none", "--entrypoint", "sh", "-v", mount, target.image,
                  "-c", script, "sh", *arguments, check=check).stdout


def pending_upgrades(backend: Path, values: Mapping[str, str]) -> list[Upgrade]:
    """Return the upgrades the Compose files require, without changing anything."""
    try:
        graph = json.loads(compose(backend, values, "config", "--format", "json").stdout)
    except ValueError as exc:
        raise UpgradeError("Compose returned an invalid configuration.") from exc
    upgrades = []
    for target in compose_services(graph):
        if not has_data(target):
            continue  # a fresh install; the image initializes it
        major = postgres_major(target.image)
        expected = DATA_ROOT if major >= FIRST_VERSIONED_LAYOUT else LEGACY_DATA
        if target.mount_target != expected:
            raise UpgradeError(f"{target.service}: PostgreSQL {major} images need the data mounted at {expected}.")
        if major < FIRST_VERSIONED_LAYOUT:
            continue
        upgrade = plan_upgrade(target, major, parse_layout(helper(target, PROBE, read_only=True)))
        if upgrade:
            upgrades.append(upgrade)
    return upgrades


def ensure_unused(target: PostgresService) -> None:
    ids = docker("ps", "--quiet").stdout.split()
    if not ids:
        return
    for line in docker("inspect", "--format", "{{.Name}} {{json .Mounts}}", *ids).stdout.splitlines():
        name, _, mounts = line.partition(" ")
        for mount in json.loads(mounts or "null") or []:
            key = "Name" if target.volume else "Source"
            if mount.get(key) == target.source:
                raise UpgradeError(f"Container {name.lstrip('/')} still uses the {target.service} data; stop it first.")


def wait_ready(name: str, timeout: float = READY_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if docker("inspect", "--format", "{{.State.Running}}", name).stdout.strip() != "true":
            logs = docker("logs", "--tail", "15", name, check=False)
            raise UpgradeError(f"{name} stopped while starting PostgreSQL:\n{(logs.stdout + logs.stderr).strip()}")
        # The entrypoint's init-time server listens on no TCP address; the final server does.
        if docker("exec", name, "pg_isready", "--quiet", "--host", "127.0.0.1", check=False).returncode == 0:
            return
        if time.monotonic() >= deadline:
            raise UpgradeError(f"{name} did not accept connections within {int(timeout)} seconds.")
        time.sleep(1)


def sql(container: str, user: str, database: str, query: str) -> list[str]:
    return docker("exec", container, "psql", "--no-psqlrc", "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1",
                  "--username", user, "--dbname", database, "--command", query).stdout.splitlines()


def row_counts(container: str, user: str, database: str) -> dict[str, int]:
    return {table: int(count) for table, _, count in
            (line.rpartition("=") for line in sql(container, user, database, ROW_COUNTS))}


def private_file(path: Path) -> IO[bytes]:
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def dump_old(upgrade: Upgrade, container: str, backups: Path) -> list[tuple[str, Path, dict[str, int]]]:
    target = upgrade.target
    image = f"{repository(target.image)}:{upgrade.source_major}"
    if postgres_major(image) != upgrade.source_major:
        raise UpgradeError(f"{image} is not PostgreSQL {upgrade.source_major}.")
    if upgrade.cluster is None:
        mount, pgdata = LEGACY_DATA, LEGACY_DATA
    else:
        mount, pgdata = DATA_ROOT, f"{DATA_ROOT}/{upgrade.cluster}/docker"
    docker("run", "--detach", "--name", container, "--network", "none", "-v", f"{target.source}:{mount}",
           "-e", f"PGDATA={pgdata}", image)
    wait_ready(container)
    extra = sql(container, target.user, "postgres", EXTRA_ROLES)
    if extra:
        raise UpgradeError(f"{target.service}: additional roles {', '.join(extra)} are not migrated automatically.")
    for directory in (backups.parent, backups):
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dumps = []
    for database in sql(container, target.user, "postgres", LIST_DATABASES):
        path = backups / f"{target.service}-pg{upgrade.source_major}-{re.sub(r'[^A-Za-z0-9_.-]', '_', database)}-{stamp}.dump"
        with private_file(path) as stream:
            docker_stream("exec", container, "pg_dump", "--username", target.user, "--dbname", database,
                          "--format=custom", stdout=stream)
        dumps.append((database, path, row_counts(container, target.user, database)))
        print(f"  Dumped {database} to {path}", flush=True)
    docker("stop", "--time", "60", container)
    return dumps


def restore_new(upgrade: Upgrade, container: str, dumps: list[tuple[str, Path, dict[str, int]]]) -> None:
    target = upgrade.target
    if not target.password:
        raise UpgradeError(f"{target.service} has no POSTGRES_PASSWORD in the Compose configuration.")
    # Passed by name only so the password never appears in a process list.
    secrets = {**os.environ, "POSTGRES_USER": target.user, "POSTGRES_PASSWORD": target.password,
               "POSTGRES_DB": target.database}
    # A non-default PGDATA lets the image initialize next to the old files it would otherwise refuse.
    docker("run", "--detach", "--name", container, "--network", "none", "-v", f"{target.source}:{DATA_ROOT}",
           "-e", f"PGDATA={DATA_ROOT}/{upgrade.target_major}/migrating", "-e", "POSTGRES_USER",
           "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_DB", target.image, env=secrets)
    wait_ready(container)
    existing = set(sql(container, target.user, "postgres", LIST_DATABASES))
    for database, path, expected in dumps:
        if database not in existing:
            docker("exec", container, "createdb", "--username", target.user, "--owner", target.user, "--", database)
        with path.open("rb") as stream:
            docker_stream("exec", "--interactive", container, "pg_restore", "--username", target.user,
                          "--dbname", database, "--exit-on-error", "--single-transaction", stdin=stream)
        if row_counts(container, target.user, database) != expected:
            raise UpgradeError(f"{target.service}: row counts in {database} differ after the restore.")
        print(f"  Restored {database} ({sum(expected.values())} rows in {len(expected)} tables)", flush=True)
    docker("exec", container, "vacuumdb", "--username", target.user, "--all", "--analyze-only", "--quiet")
    docker("stop", "--time", "60", container)


def run_upgrade(upgrade: Upgrade, backups: Path) -> None:
    target = upgrade.target
    old, new = (f"mavi-pg-upgrade-{target.service}-{suffix}" for suffix in ("old", "new"))
    major = str(upgrade.target_major)
    docker("rm", "--force", old, new, check=False)
    try:
        if not upgrade.finish_only:
            helper(target, REMOVE_STAGING, major)
            try:
                restore_new(upgrade, new, dump_old(upgrade, old, backups))
            except BaseException:
                # Best effort: never hide the original error behind a cleanup failure.
                docker("rm", "--force", new, check=False)
                helper(target, REMOVE_STAGING, major, check=False)
                raise
            helper(target, ACTIVATE, major)
        if upgrade.cluster is None:
            helper(target, MOVE_LEGACY, str(upgrade.source_major), major)
    finally:
        docker("rm", "--force", old, new, check=False)


def apply_upgrades(backend: Path, values: Mapping[str, str], upgrades: list[Upgrade], *, restart: bool = False) -> None:
    """Stop the affected services and upgrade each database; old data is kept as a rollback copy."""
    services = list(dict.fromkeys(name for upgrade in upgrades
                                  for name in (*upgrade.target.dependents, upgrade.target.service)))
    print(f"Stopping {', '.join(services)} ...", flush=True)
    compose(backend, values, "stop", *services)
    for upgrade in upgrades:
        print(f"Upgrading {upgrade.describe()}", flush=True)
        ensure_unused(upgrade.target)
        run_upgrade(upgrade, backend / "backups" / "postgres")
        print(f"  Kept the PostgreSQL {upgrade.source_major} cluster as {upgrade.source_major}/docker "
              f"in {upgrade.target.source} for rollback.")
    if restart:
        compose(backend, values, "up", "--detach", "--no-build", *services)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upgrade the bundled PostgreSQL data to the Compose image versions.")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--check", action="store_true", help="only report pending upgrades; exit 1 if any")
    parser.add_argument("--yes", action="store_true", help="upgrade without asking")
    parser.add_argument("--no-restart", action="store_true", help="leave the stopped services stopped")
    args = parser.parse_args(argv)
    backend = args.project_root.resolve() / "backend"
    try:
        values = load_env(backend / ".env")
        if not values.get("COMPOSE_FILE"):
            raise UpgradeError(f"{backend / '.env'} has no COMPOSE_FILE; run ./install-server.sh first.")
        upgrades = pending_upgrades(backend, values)
        if not upgrades:
            print("PostgreSQL data already matches the Compose images; nothing to do.")
            return 0
        print("Pending PostgreSQL upgrades:")
        for upgrade in upgrades:
            print(f"  {upgrade.describe()}")
        if args.check:
            return 1
        if not args.yes and input("Stop the affected services and upgrade now? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled; nothing was changed.")
            return 0
        apply_upgrades(backend, values, upgrades, restart=not args.no_restart)
        print(f"Upgrade finished. Dumps are in {backend / 'backups' / 'postgres'} (mode 0600).")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. Old PostgreSQL data was not removed; rerun to continue.", file=sys.stderr)
        return 130
    except (ConfigurationError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
