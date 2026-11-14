"""Prebuilt deployment never compiles locally or changes .env on a failed pull."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.server_installer import images, installer
from scripts.server_installer.config import CLASSIC, PANEL, KEYCLOAK, InstallPlan, ConfigurationError, build_configuration, plan_from_existing

ROOT = Path(__file__).resolve().parents[2]


class PrebuiltImagesTests(unittest.TestCase):
    def values(self, mode=PANEL, source=False):
        plan = InstallPlan(mode, "example.com", "admin@example.com", build_from_source=source)
        values, _ = build_configuration(plan, {}, lambda name: "placeholder-" + name)
        return plan, values

    def prepare(self, fail=False, invalid=False):
        plan, values = self.values()
        graph = {"services": {name: {"image": "example/" + name} for name in images.stack_services(plan, values)}}
        if invalid: graph["services"]["panel"]["build"] = {"context": ".."}
        commands = []
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory); env = backend / ".env"; env.write_text("KEEP=original\n")
            def runner(command, **kwargs):
                commands.append(command)
                if "config" in command:
                    temporary = Path(command[command.index("--env-file") + 1])
                    self.assertTrue(temporary.is_file())
                    if os.name != "nt": self.assertEqual(temporary.stat().st_mode & 0o777, 0o600)
                    self.assertIn(str(backend / "keycloak/docker-compose.yml"), command)
                    return subprocess.CompletedProcess(command, 0, json.dumps(graph), "")
                return subprocess.CompletedProcess(command, 1 if fail else 0, "", "")
            with mock.patch.object(images.subprocess, "run", side_effect=runner), redirect_stdout(io.StringIO()):
                if fail or invalid:
                    with self.assertRaises(ConfigurationError): images.prepare_images(backend, plan, values)
                else: images.prepare_images(backend, plan, values)
            self.assertEqual(env.read_text(), "KEEP=original\n")
            self.assertEqual(list(backend.glob(".mavi-image-check-*")), [])
        self.assertFalse(any("up" in command or "build" in command for command in commands))
        return commands

    def test_default_downloads_all_selected_runtime_dependencies(self):
        commands = self.prepare()
        self.assertEqual(len([command for command in commands if command[1] == "pull"]), 5)

    def test_failed_pull_preserves_config_and_does_not_fall_back_to_build(self): self.prepare(fail=True)

    def test_unexpected_source_build_in_prebuilt_graph_is_rejected(self):
        commands = self.prepare(invalid=True)
        self.assertFalse(any(command[1] == "pull" for command in commands))

    def test_source_build_is_explicit_and_persisted(self):
        plan, values = self.values(source=True)
        self.assertTrue(plan_from_existing(values).build_from_source)
        self.assertIn("panel/docker-compose.build.yml", values["COMPOSE_FILE"])
        with mock.patch.object(images.subprocess, "run") as run, redirect_stdout(io.StringIO()):
            images.prepare_images(Path("/unused"), plan, values)
        run.assert_not_called()

    def test_classic_source_build_does_not_add_a_bare_panel_service(self):
        _, values = self.values(CLASSIC, source=True)
        self.assertIn(":docker-compose.build.yml", values["COMPOSE_FILE"])
        self.assertNotIn("panel/", values["COMPOSE_FILE"])

    def test_default_start_uses_no_build_and_explicit_files(self):
        plan, values = self.values(CLASSIC)
        with mock.patch.object(installer, "run") as run:
            installer.start_stack(Path("/srv/mavi/backend"), plan, values)
        command = run.call_args.args[0]
        self.assertIn("--no-build", command); self.assertNotIn("--build", command)
        self.assertIn("--env-file", command); self.assertIn("-f", command)
        self.assertIn("COMPOSE_FILE", run.call_args.kwargs["env"])

    def test_panel_start_uses_images_for_both_start_phases(self):
        plan, values = self.values()
        with mock.patch.object(installer, "run") as run, mock.patch.object(installer, "bootstrap_panel", return_value=None):
            installer.start_stack(Path("/srv/mavi/backend"), plan, values)
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertIn("--no-build", call.args[0]); self.assertNotIn("--build", call.args[0])
            self.assertIn("/srv/mavi/backend/panel/docker-compose.yml", call.args[0])

    def test_planned_values_override_stale_shell_settings(self):
        with mock.patch.dict(os.environ, {"COMPOSE_FILE": "wrong.yml", "DOMAIN_NAME": "wrong.example"}):
            env = images.compose_environment({"COMPOSE_FILE": "docker-compose.yml", "DOMAIN_NAME": "example.com"})
        self.assertEqual(env["DOMAIN_NAME"], "example.com")
        self.assertEqual(env["COMPOSE_FILE"], "docker-compose.yml")

    @unittest.skipUnless(os.environ.get("COMPOSE_TEST_BINARY"), "Set COMPOSE_TEST_BINARY for real Compose rendering")
    def test_all_modes_render_with_images_and_source_builds_only_when_requested(self):
        executable = os.environ["COMPOSE_TEST_BINARY"]
        for mode in (CLASSIC, KEYCLOAK, PANEL):
            for source in (False, True):
                with self.subTest(mode=mode, source=source):
                    plan, values = self.values(mode, source)
                    result = subprocess.run([executable, "config", "--format", "json"], cwd=ROOT / "backend",
                                            env=images.compose_environment(values), capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    services = json.loads(result.stdout)["services"]
                    for name in images.stack_services(plan, values):
                        self.assertTrue(services[name].get("image"), name)
                    self.assertEqual(bool(services["vpn-server"].get("build")), source)
                    if mode == PANEL: self.assertEqual(bool(services["panel"].get("build")), source)
                    else: self.assertNotIn("panel", services)


if __name__ == "__main__": unittest.main()
