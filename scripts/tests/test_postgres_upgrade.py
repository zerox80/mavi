"""PostgreSQL major upgrades keep the old data until the new cluster is verified."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.server_installer import installer, postgres_upgrade as upgrade_module
from scripts.server_installer.postgres_upgrade import (
    ACTIVATE, DATA_ROOT, EXTRA_ROLES, LIST_DATABASES, MOVE_LEGACY, PROBE, REMOVE_STAGING, ROW_COUNTS,
    Layout, PostgresService, Upgrade, UpgradeError, compose_services, parse_layout, plan_upgrade, repository,
)

ROOT = Path(__file__).resolve().parents[2]
PANEL_SECRET = "panel-db-secret-value"
KEYCLOAK_SECRET = "keycloak-db-secret-value"
VOLUME = "backend_panel-db-data"


def graph(bind_source: str) -> dict:
    def database(image, user, secret, mount):
        return {"image": image, "volumes": [mount],
                "environment": {"POSTGRES_DB": user, "POSTGRES_USER": user, "POSTGRES_PASSWORD": secret}}
    return {
        "services": {
            "panel-db": database("postgres:19.0-trixie", "mavi_panel", PANEL_SECRET,
                                 {"type": "volume", "source": "panel-db-data", "target": DATA_ROOT}),
            "panel": {"image": "example/panel"},
            "keycloak-db": database("postgres:18.7-trixie", "keycloak", KEYCLOAK_SECRET,
                                    {"type": "bind", "source": bind_source, "target": DATA_ROOT}),
            "keycloak": {"image": "example/keycloak"},
        },
        "volumes": {"panel-db-data": {"name": VOLUME}},
    }


def done(command, stdout=""):
    return subprocess.CompletedProcess(command, 0, stdout, "" if isinstance(stdout, str) else b"")


class FakeDocker:
    """Answers the docker commands the upgrade issues and records them in order."""

    def __init__(self, bind_source, layouts, restored_rows="3", mounted_by=None):
        self.graph, self.layouts, self.restored_rows = graph(bind_source), layouts, restored_rows
        self.mounted_by = mounted_by
        self.calls, self.environments, self.restored = [], [], []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        self.environments.append(kwargs.get("env"))
        arguments = command[1:]
        if arguments[0] == "compose":
            return done(command, json.dumps(self.graph) if "config" in arguments else "")
        if arguments[:2] == ["image", "inspect"]:
            major = re.match(r"\d+", arguments[-1].rsplit(":", 1)[-1]).group()
            return done(command, json.dumps(["PATH=/usr/bin", f"PG_MAJOR={major}"]))
        if arguments[0] == "run" and "--entrypoint" in arguments:
            source = arguments[arguments.index("-v") + 1].split(":/pgroot")[0]
            script = arguments[arguments.index("-c") + 1]
            return done(command, self.layouts[source] if script == PROBE else "")
        if arguments[0] == "ps":
            return done(command, "abc\n" if self.mounted_by else "")
        if arguments[0] == "inspect" and "{{.State.Running}}" in arguments:
            return done(command, "true\n")
        if arguments[0] == "inspect":
            return done(command, f"/other [{json.dumps({'Name': self.mounted_by})}]\n")
        if arguments[0] == "exec":
            return self.execute(command, arguments, kwargs)
        return done(command)

    def execute(self, command, arguments, kwargs):
        if "pg_dump" in arguments:
            kwargs["stdout"].write(b"PGDMP-" + arguments[arguments.index("--dbname") + 1].encode())
            return done(command, b"")
        if "pg_restore" in arguments:
            self.restored.append(kwargs["stdin"].read())
            return done(command, b"")
        if "psql" not in arguments:
            return done(command)
        query = arguments[-1]
        if query == EXTRA_ROLES:
            return done(command)
        if query == LIST_DATABASES:
            return done(command, "mavi_panel\npostgres\n")
        if query == ROW_COUNTS and arguments[arguments.index("--dbname") + 1] == "mavi_panel":
            rows = self.restored_rows if arguments[1].endswith("-new") else "3"
            return done(command, f"public.panel_devices={rows}\npublic.\"odd=name\"=7\n")
        return done(command)

    def index(self, predicate):
        return next(position for position, command in enumerate(self.calls) if predicate(command))

    def helper_scripts(self):
        return [command[command.index("-c") + 1] for command in self.calls if "--entrypoint" in command]


class PostgresUpgradeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.backend = Path(directory.name) / "backend"
        self.bind = self.backend / "data" / "postgres"
        self.bind.mkdir(parents=True)
        self.values = {"COMPOSE_FILE": "docker-compose.yml:keycloak/docker-compose.yml:panel/docker-compose.yml"}

    def run_fake(self, fake, action):
        with mock.patch.object(upgrade_module.subprocess, "run", side_effect=fake), redirect_stdout(io.StringIO()):
            return action()

    def pending(self, fake):
        return self.run_fake(fake, lambda: upgrade_module.pending_upgrades(self.backend, self.values))

    def test_plan_matches_every_data_layout(self):
        target = PostgresService("panel-db", "postgres:19.0", "vol", True, DATA_ROOT, "u", "u", "p")
        self.assertIsNone(plan_upgrade(target, 19, Layout()))
        self.assertIsNone(plan_upgrade(target, 19, Layout(None, {"16": 16, "19": 19})))
        self.assertEqual(plan_upgrade(target, 19, Layout(16)), Upgrade(target, 16, 19, None))
        self.assertEqual(plan_upgrade(target, 19, Layout(None, {"17": 17, "18": 18})), Upgrade(target, 18, 19, "18"))
        self.assertEqual(plan_upgrade(target, 19, Layout(16, {"19": 19})), Upgrade(target, 16, 19, None, finish_only=True))
        for layout in (Layout(20), Layout(None, {"19": 18}), Layout(16, {"18": 18})):
            with self.subTest(layout=layout), self.assertRaises(UpgradeError):
                plan_upgrade(target, 19, layout)
        with self.assertRaises(UpgradeError):
            parse_layout("root 9.6\n")

    def test_old_image_keeps_the_registry_of_the_compose_image(self):
        self.assertEqual(repository("postgres:19.0-trixie"), "postgres")
        self.assertEqual(repository("localhost:5000/postgres:19"), "localhost:5000/postgres")
        self.assertEqual(repository("mirror.gcr.io/postgres@sha256:" + "a" * 64), "mirror.gcr.io/postgres")

    def test_compose_services_resolve_volumes_binds_and_dependents(self):
        services = {target.service: target for target in compose_services(graph(str(self.bind)))}
        self.assertEqual((services["panel-db"].source, services["panel-db"].volume), (VOLUME, True))
        self.assertEqual((services["keycloak-db"].source, services["keycloak-db"].volume), (str(self.bind), False))
        self.assertEqual(services["panel-db"].dependents, ("panel",))
        self.assertNotIn(PANEL_SECRET, repr(services["panel-db"]))

    def test_check_reports_both_databases_without_changing_anything(self):
        fake = FakeDocker(str(self.bind), {VOLUME: "root 16\n", str(self.bind): "root 15\n"})
        upgrades = self.pending(fake)
        self.assertEqual([upgrade.describe().split(" (")[0] for upgrade in upgrades],
                         ["keycloak-db: PostgreSQL 15 -> 18", "panel-db: PostgreSQL 16 -> 19"])
        self.assertFalse(any(word in command for command in fake.calls for word in ("stop", "--detach", "exec")))
        self.assertTrue(all(":ro" in command[command.index("-v") + 1] for command in fake.calls if "--entrypoint" in command))

    def test_fresh_install_needs_no_probe(self):
        self.bind.rmdir()
        fake = FakeDocker(str(self.bind), {})
        fake.graph["services"].pop("panel-db")
        self.assertEqual(self.pending(fake), [])
        self.assertFalse(any("--entrypoint" in command for command in fake.calls))

    def upgrade_panel(self, fake):
        upgrades = [upgrade for upgrade in self.pending(fake) if upgrade.target.service == "panel-db"]
        self.run_fake(fake, lambda: upgrade_module.apply_upgrades(self.backend, self.values, upgrades))

    def test_upgrade_dumps_restores_verifies_and_only_then_activates(self):
        fake = FakeDocker(str(self.bind), {VOLUME: "root 16\n", str(self.bind): ""})
        self.upgrade_panel(fake)
        old_start = fake.index(lambda c: "--detach" in c and c[-1] == "postgres:16")
        new_start = fake.index(lambda c: "--detach" in c and c[-1] == "postgres:19.0-trixie")
        self.assertLess(fake.index(lambda c: "stop" in c and "panel-db" in c), old_start)
        self.assertLess(old_start, fake.index(lambda c: "pg_dump" in c))
        self.assertLess(fake.index(lambda c: "pg_dump" in c), new_start)
        self.assertIn(f"{VOLUME}:/var/lib/postgresql/data", fake.calls[old_start])
        self.assertIn(f"PGDATA={DATA_ROOT}/19/migrating", fake.calls[new_start])
        self.assertEqual(fake.environments[new_start]["POSTGRES_PASSWORD"], PANEL_SECRET)
        self.assertEqual(fake.helper_scripts()[-2:], [ACTIVATE, MOVE_LEGACY])
        move = fake.calls[fake.index(lambda c: "--entrypoint" in c and MOVE_LEGACY in c)]
        self.assertEqual(move[-2:], ["16", "19"])
        self.assertIn("rm", fake.calls[-1])
        self.assertEqual(sorted(fake.restored), [b"PGDMP-mavi_panel", b"PGDMP-postgres"])
        self.assertFalse(any(PANEL_SECRET in argument for command in fake.calls for argument in command))
        backups = self.backend / "backups" / "postgres"
        if os.name != "nt":
            self.assertEqual(backups.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in backups.iterdir()))
        self.assertEqual(len(list(backups.glob("panel-db-pg16-*.dump"))), 2)

    def test_row_count_mismatch_discards_new_cluster_and_keeps_old_data(self):
        fake = FakeDocker(str(self.bind), {VOLUME: "root 16\n", str(self.bind): ""}, restored_rows="2")
        with self.assertRaisesRegex(UpgradeError, "row counts"):
            self.upgrade_panel(fake)
        scripts = fake.helper_scripts()
        self.assertEqual(scripts[-1], REMOVE_STAGING)
        self.assertNotIn(ACTIVATE, scripts)
        self.assertNotIn(MOVE_LEGACY, scripts)

    def test_versioned_cluster_is_dumped_in_place_and_not_moved(self):
        fake = FakeDocker(str(self.bind), {VOLUME: "cluster 16 16\ncluster 18 18\n", str(self.bind): ""})
        self.upgrade_panel(fake)
        old_start = fake.calls[fake.index(lambda c: "--detach" in c and c[-1] == "postgres:18")]
        self.assertIn(f"{VOLUME}:{DATA_ROOT}", old_start)
        self.assertIn(f"PGDATA={DATA_ROOT}/18/docker", old_start)
        self.assertNotIn(MOVE_LEGACY, fake.helper_scripts())

    def test_interrupted_move_is_finished_without_a_new_dump(self):
        fake = FakeDocker(str(self.bind), {VOLUME: "root 16\ncluster 19 19\n", str(self.bind): ""})
        self.upgrade_panel(fake)
        self.assertFalse(any("pg_dump" in command for command in fake.calls))
        self.assertEqual(fake.helper_scripts()[-1], MOVE_LEGACY)

    def test_running_container_on_the_data_aborts_before_any_change(self):
        fake = FakeDocker(str(self.bind), {VOLUME: "root 16\n", str(self.bind): ""}, mounted_by=VOLUME)
        with self.assertRaisesRegex(UpgradeError, "still uses"):
            self.upgrade_panel(fake)
        self.assertFalse(any("--detach" in command for command in fake.calls))

    def test_legacy_mount_with_a_versioned_image_is_rejected(self):
        fake = FakeDocker(str(self.bind), {VOLUME: "root 16\n", str(self.bind): ""})
        fake.graph["services"]["panel-db"]["volumes"][0]["target"] = DATA_ROOT + "/data"
        with self.assertRaisesRegex(UpgradeError, "mounted at /var/lib/postgresql"):
            self.pending(fake)


class ShippedComposeTests(unittest.TestCase):
    def test_databases_pin_exact_versions_and_use_the_versioned_layout(self):
        for path, service, highest in (("backend/panel/docker-compose.yml", "panel-db", 19),
                                       ("backend/keycloak/docker-compose.yml", "keycloak-db", 18)):
            block = (ROOT / path).read_text(encoding="utf-8").split(f"  {service}:\n", 1)[1].split("\n\n", 1)[0]
            image = re.search(r"^\s+image: postgres:(\d+)\.\d+-trixie$", block, re.MULTILINE)
            with self.subTest(service=service):
                self.assertIsNotNone(image, "pin minor version and Debian release")
                self.assertTrue(18 <= int(image.group(1)) <= highest)
                self.assertRegex(block, re.compile(r"^\s+- \S+:/var/lib/postgresql$", re.MULTILINE))
                self.assertNotIn("/var/lib/postgresql/data", block)


class InstallerUpgradeTests(unittest.TestCase):
    upgrade = Upgrade(PostgresService("panel-db", "postgres:19.0-trixie", "vol", True, DATA_ROOT, "u", "u", "p"), 16, 19, None)

    def test_declined_upgrade_stops_before_containers(self):
        with mock.patch.object(installer, "pending_upgrades", return_value=[self.upgrade]), \
             mock.patch.object(installer, "confirm", return_value=False), \
             mock.patch.object(installer, "apply_upgrades") as apply, redirect_stdout(io.StringIO()):
            self.assertFalse(installer.upgrade_databases(Path("/srv/backend"), {}, non_interactive=False))
        apply.assert_not_called()

    def test_non_interactive_run_upgrades_after_plan_approval(self):
        with mock.patch.object(installer, "pending_upgrades", return_value=[self.upgrade]), \
             mock.patch.object(installer, "confirm") as confirm, \
             mock.patch.object(installer, "apply_upgrades") as apply, redirect_stdout(io.StringIO()):
            self.assertTrue(installer.upgrade_databases(Path("/srv/backend"), {}, non_interactive=True))
        confirm.assert_not_called()
        apply.assert_called_once()

    def test_up_to_date_data_needs_no_question(self):
        with mock.patch.object(installer, "pending_upgrades", return_value=[]), \
             mock.patch.object(installer, "confirm") as confirm:
            self.assertTrue(installer.upgrade_databases(Path("/srv/backend"), {}, non_interactive=False))
        confirm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
