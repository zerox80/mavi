"""Test that the installer deploys prebuilt images and builds only on request."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock

from scripts.server_installer import installer
from scripts.server_installer.config import (
    CLASSIC,
    KEYCLOAK,
    PANEL,
    PROXY_NGINX,
    PROXY_TRAEFIK,
    InstallPlan,
    build_configuration,
    compose_file_for_mode,
    load_env,
    mode_from_environment,
    plan_from_existing,
)
from scripts.server_installer.preflight import PreflightReport

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SOURCE_PANEL = (
    "docker-compose.yml:keycloak/docker-compose.yml:panel/docker-compose.yml"
    ":docker-compose.build.yml:panel/docker-compose.build.yml"
)


def secret_factory(name: str) -> str:
    return f"generated-{name}-abcdefghijklmnopqrstuvwxyz-0123456789"


def recorded_start(plan: InstallPlan, fail_on: str = "") -> list[list[str]]:
    """Run start_stack with Docker and Keycloak replaced by recorders."""
    commands: list[list[str]] = []

    def run(command, cwd=None):
        commands.append(command)
        if fail_on and fail_on in command:
            raise subprocess.CalledProcessError(1, command)

    bootstrap = mock.Mock(return_value=mock.Mock(client_secret="secret", admin=None))
    with mock.patch.object(installer, "run", run), mock.patch.object(installer, "bootstrap_panel", bootstrap):
        installer.start_stack(Path("backend"), plan, {"MAVI_PANEL_CLIENT_SECRET": "secret"}, set())
    return commands


class ComposeSelectionTests(unittest.TestCase):
    def test_source_build_appends_overrides_after_the_files_they_replace(self):
        self.assertEqual(compose_file_for_mode(CLASSIC, True), "docker-compose.yml:docker-compose.build.yml")
        self.assertEqual(
            compose_file_for_mode(KEYCLOAK, True),
            "docker-compose.yml:keycloak/docker-compose.yml:docker-compose.build.yml",
        )
        self.assertEqual(compose_file_for_mode(PANEL, True), SOURCE_PANEL)

    def test_existing_source_build_is_detected_and_preserved(self):
        existing = {"COMPOSE_FILE": SOURCE_PANEL, "DOMAIN_NAME": "example.com"}
        plan = plan_from_existing(existing)
        self.assertEqual(plan.mode, PANEL)
        self.assertTrue(plan.source_build)
        values, _ = build_configuration(plan, existing, secret_factory)
        self.assertEqual(values["COMPOSE_FILE"], SOURCE_PANEL)

    def test_prebuilt_installation_is_not_mistaken_for_a_source_build(self):
        for mode in (CLASSIC, KEYCLOAK, PANEL):
            with self.subTest(mode=mode):
                existing = {"COMPOSE_FILE": compose_file_for_mode(mode), "DOMAIN_NAME": "example.com"}
                plan = plan_from_existing(existing)
                self.assertFalse(plan.source_build)
                self.assertEqual(mode_from_environment(existing), mode)


class StartCommandTests(unittest.TestCase):
    def test_default_start_pulls_images_and_never_builds(self):
        commands = recorded_start(InstallPlan(PANEL, "example.com", proxy=PROXY_NGINX))
        self.assertEqual(
            commands,
            [
                ["docker", "compose", "pull", "vpn-server", "keycloak-db", "keycloak"],
                ["docker", "compose", "up", "-d", "vpn-server", "keycloak-db", "keycloak"],
                ["docker", "compose", "pull", "panel"],
                ["docker", "compose", "up", "-d", "panel"],
            ],
        )

    def test_classic_and_traefik_modes_select_their_services(self):
        self.assertEqual(
            recorded_start(InstallPlan(CLASSIC)),
            [["docker", "compose", "pull", "vpn-server"], ["docker", "compose", "up", "-d", "vpn-server"]],
        )
        traefik = recorded_start(InstallPlan(KEYCLOAK, "example.com", proxy=PROXY_TRAEFIK))
        self.assertEqual(traefik[0], ["docker", "compose", "pull", "vpn-server", "keycloak-db", "keycloak", "traefik"])

    def test_source_build_compiles_without_pulling(self):
        commands = recorded_start(InstallPlan(PANEL, "example.com", source_build=True))
        self.assertEqual(
            commands,
            [
                ["docker", "compose", "up", "-d", "--build", "vpn-server", "keycloak-db", "keycloak"],
                ["docker", "compose", "up", "-d", "--build", "panel"],
            ],
        )

    def test_failed_pull_stops_before_start_and_names_the_source_build(self):
        errors = io.StringIO()
        commands: list[list[str]] = []

        def run(command, cwd=None):
            commands.append(command)
            raise subprocess.CalledProcessError(1, command)

        with mock.patch.object(installer, "run", run), contextlib.redirect_stderr(errors):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.start_services(Path("backend"), ["vpn-server"], False)
        self.assertEqual(commands, [["docker", "compose", "pull", "vpn-server"]])
        self.assertIn("--build-from-source", errors.getvalue())


class InstallerFlagTests(unittest.TestCase):
    def install(self, *extra: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "backend").mkdir()
            (root / "backend" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            arguments = ["--project-root", str(root), "--non-interactive", "--mode", "classic", "--yes", "--no-start"]
            report = mock.Mock(return_value=PreflightReport([]))
            with mock.patch.object(installer, "collect_preflight", report), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(installer.main([*arguments, *extra]), 0)
            return load_env(root / "backend" / ".env")

    def test_new_installation_uses_prebuilt_images_by_default(self):
        self.assertEqual(self.install()["COMPOSE_FILE"], "docker-compose.yml")

    def test_build_from_source_flag_selects_the_build_override(self):
        self.assertEqual(
            self.install("--build-from-source")["COMPOSE_FILE"],
            "docker-compose.yml:docker-compose.build.yml",
        )


class ImageContractTests(unittest.TestCase):
    def test_default_compose_files_reference_published_images_only(self):
        base = (BACKEND / "docker-compose.yml").read_text(encoding="utf-8")
        panel = (BACKEND / "panel" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("image: ${MAVI_IMAGE:-ghcr.io/zerox80/mavi-vpn:latest}", base)
        self.assertIn("image: ${MAVI_PANEL_IMAGE:-ghcr.io/zerox80/mavi-panel:latest}", panel)
        for content in (base, panel):
            self.assertNotRegex(content, r"(?m)^\s+build:")

    def test_build_overrides_use_the_repository_root_and_local_tags(self):
        cases = [
            (BACKEND / "docker-compose.build.yml", "image: mavi-vpn:source", "Dockerfile"),
            (BACKEND / "panel" / "docker-compose.build.yml", "image: mavi-panel:source", "panel/Dockerfile"),
        ]
        for path, image, dockerfile in cases:
            with self.subTest(path=path.name):
                content = path.read_text(encoding="utf-8")
                self.assertIn(image, content)
                # Relative paths resolve against backend/, the project directory.
                self.assertIn("context: ..\n", content)
                self.assertIn(f"dockerfile: {dockerfile}\n", content)
                self.assertTrue((ROOT / dockerfile).is_file())
                self.assertNotIn("ghcr.io", content)

    def test_workflow_publishes_the_images_the_compose_files_pull(self):
        workflow = (ROOT / ".github" / "workflows" / "publish-server.yml").read_text(encoding="utf-8")
        self.assertIn("target: [vpn, panel]", workflow)
        self.assertEqual(workflow.count("IMAGE: ghcr.io/zerox80/mavi-${{ matrix.target }}"), 2)
        self.assertIn("pattern: digest-${{ matrix.target }}-*", workflow)
        for dockerfile in re.findall(r"dockerfile: (\S+)", workflow):
            with self.subTest(dockerfile=dockerfile):
                self.assertTrue((ROOT / dockerfile).is_file())
        self.assertIn("dockerfile: panel/Dockerfile", workflow)
        self.assertIn("- panel/src/**", workflow)

    def test_image_build_contexts_contain_every_workspace_member(self):
        members = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))["workspace"]["members"]
        for ignore in (ROOT / ".dockerignore", ROOT / "docker" / "server" / "Dockerfile.dockerignore"):
            rules = ignore.read_text(encoding="utf-8").splitlines()
            for member in members:
                with self.subTest(ignore=ignore.name, member=member):
                    self.assertIn(f"!{member}/Cargo.toml", rules)
                    self.assertIn(f"!{member}/src/**", rules)


if __name__ == "__main__":
    unittest.main()
