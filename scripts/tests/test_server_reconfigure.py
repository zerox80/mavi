"""Re-enter setup safely without stale derived settings or lost credentials."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.server_installer import installer
from scripts.server_installer.config import (
    CLASSIC, KEYCLOAK, PANEL, PROXY_EXTERNAL, PROXY_NGINX, PROXY_TRAEFIK,
    RECONFIGURE_KEYS, ConfigurationError, InstallPlan, build_configuration,
    load_env, save_env,
)
from scripts.server_installer.preflight import PreflightReport


def saved_settings():
    return {
        "DOMAIN_NAME": "wrong.example.com", "ACME_EMAIL": "wrong@wrong.example.com",
        "MAVI_PROXY_MODE": PROXY_TRAEFIK, "MAVI_WEB_PANEL_ENABLED": "true",
        "VPN_KEYCLOAK_ENABLED": "true", "VPN_KEYCLOAK_URL": "https://auth.wrong.example.com",
        "MAVI_PANEL_VPN_ENDPOINT": "wrong.example.com:10443", "MAVI_PANEL_TRANSPORT": "quic",
        "COMPOSE_FILE": "docker-compose.yml:keycloak/docker-compose.yml:panel/docker-compose.yml",
        "COMPOSE_PROFILES": "traefik", "TRAEFIK_BIND_ADDR": "0.0.0.0",
        "TRAEFIK_HTTP_PORT": "18088", "TRAEFIK_HTTPS_PORT": "18488",
        "TRAEFIK_ROUTER_ENTRYPOINTS": "websecure", "TRAEFIK_TLS_ENABLED": "true",
        "TRAEFIK_ACME_RESOLVER": "oldresolver", "TRAEFIK_FORWARDED_HEADERS_INSECURE": "false",
        "VPN_AUTH_TOKEN": "keep-token", "KEYCLOAK_DB_PASSWORD": "keep-database-password",
        "KEYCLOAK_ADMIN_PASS": "keep-admin-password", "MAVI_PANEL_DB_PASSWORD": "keep-panel-db-password",
        "MAVI_PANEL_CLIENT_SECRET": "keep-client-secret", "MAVI_PANEL_PROXY_TOKEN": "keep-proxy-token",
        "VPN_PORT": "11443", "VPN_BIND_ADDR": "0.0.0.0:11443", "VPN_DNS": "9.9.9.9",
        "VPN_CENSORSHIP_RESISTANT": "true", "VPN_KEYCLOAK_REALM": "keep-realm",
        "CUSTOM_SETTING": "untouched",
    }


class SetupQuestionsTests(unittest.TestCase):
    def plan(self, answers, values=None, force=False):
        output = io.StringIO()
        with redirect_stdout(output), mock.patch("builtins.input", side_effect=answers):
            result = installer.interactive_plan(saved_settings() if values is None else values, force)
        self.assertNotIn("keep-admin-password", output.getvalue())
        return result, output.getvalue()

    def test_continue_keeps_existing_plan(self):
        plan, output = self.plan([""])
        self.assertTrue(plan.existing)
        self.assertFalse(plan.reconfigure)
        self.assertEqual(plan.domain, "wrong.example.com")
        self.assertIn("Start setup questions again", output)

    def test_restart_reasks_domain_mode_and_proxy(self):
        plan, output = self.plan(["2", "1", "new.example.com", "2", "n"])
        self.assertEqual((plan.mode, plan.domain, plan.proxy), (PANEL, "new.example.com", PROXY_EXTERNAL))
        self.assertTrue(plan.reconfigure)
        self.assertFalse(plan.existing)
        self.assertIn("auth.new.example.com", output)
        self.assertIn("panel.new.example.com", output)

    def test_restart_recovers_from_invalid_saved_proxy(self):
        values = saved_settings(); values["MAVI_PROXY_MODE"] = "wrong-value"
        plan, _ = self.plan(["2", "2"], values)
        self.assertEqual(plan.mode, CLASSIC)
        self.assertTrue(plan.reconfigure)

    def test_invalid_input_can_be_corrected_without_exiting(self):
        plan, output = self.plan([
            "bad", "2", "9", "1", "https://wrong.example.com", "new.example.com",
            "bad", "1", "bad-email", "admin@new.example.com", "y",
            "bad/name", "panel-admin", "bad-email", "person@new.example.com",
        ])
        self.assertEqual(plan.domain, "new.example.com")
        self.assertEqual(plan.admin_email, "person@new.example.com")
        self.assertTrue(plan.create_admin)
        self.assertIn("Invalid input", output)

    def test_cancel_does_not_build_a_plan(self):
        plan, _ = self.plan(["3"])
        self.assertIsNone(plan)

    def test_incomplete_panel_setup_can_resume_initial_admin_creation(self):
        values = saved_settings(); values.pop("MAVI_PANEL_CLIENT_SECRET")
        plan, _ = self.plan(["1", "y", "", "person@example.com"], values)
        self.assertTrue(plan.create_admin)
        self.assertEqual(plan.admin_email, "person@example.com")

    def test_noninteractive_existing_panel_honors_explicit_create_admin(self):
        args = installer.parser().parse_args(["--non-interactive", "--create-admin", "--admin-email", "person@example.com"])
        plan = installer.argument_plan(args, saved_settings())
        self.assertTrue(plan.create_admin)
        self.assertEqual(plan.admin_email, "person@example.com")

    def test_flag_skips_saved_settings_menu(self):
        plan, output = self.plan(["3", "new.example.com", "2"], force=True)
        self.assertEqual(plan.mode, KEYCLOAK)
        self.assertTrue(plan.reconfigure)
        self.assertNotIn("Continue with saved settings", output)

    def test_noninteractive_requires_new_mode_when_reconfiguring(self):
        args = installer.parser().parse_args(["--non-interactive", "--reconfigure"])
        with self.assertRaisesRegex(ConfigurationError, "--mode"):
            installer.argument_plan(args, saved_settings())

    def test_noninteractive_reconfigure_uses_new_answers(self):
        args = installer.parser().parse_args([
            "--non-interactive", "--reconfigure", "--mode", "keycloak",
            "--domain", "new.example.com", "--proxy", "external",
        ])
        plan = installer.argument_plan(args, saved_settings())
        self.assertEqual((plan.mode, plan.domain, plan.proxy), (KEYCLOAK, "new.example.com", PROXY_EXTERNAL))
        self.assertTrue(plan.reconfigure)


class ReconfigurationTests(unittest.TestCase):
    def configuration(self, plan):
        def no_new_secret(name): raise AssertionError(f"Regenerated credential: {name}")
        original = saved_settings()
        result, generated = build_configuration(plan, original, no_new_secret)
        self.assertEqual(original, saved_settings())
        self.assertFalse(generated)
        for key in ("VPN_AUTH_TOKEN", "KEYCLOAK_DB_PASSWORD", "KEYCLOAK_ADMIN_PASS", "MAVI_PANEL_DB_PASSWORD",
                    "MAVI_PANEL_CLIENT_SECRET", "MAVI_PANEL_PROXY_TOKEN", "CUSTOM_SETTING", "VPN_PORT", "VPN_DNS"):
            self.assertEqual(result[key], original[key])
        return result

    def test_new_domain_recomputes_derived_urls_and_removes_traefik(self):
        values = self.configuration(InstallPlan(PANEL, "new.example.com", "admin@new.example.com", PROXY_NGINX, reconfigure=True))
        self.assertEqual(values["DOMAIN_NAME"], "new.example.com")
        self.assertEqual(values["VPN_KEYCLOAK_URL"], "https://auth.new.example.com")
        self.assertEqual(values["MAVI_PANEL_VPN_ENDPOINT"], "new.example.com:11443")
        self.assertEqual(values["MAVI_PANEL_TRANSPORT"], "http3_censorship_resistant")
        self.assertEqual(values["COMPOSE_PROFILES"], "")
        self.assertEqual(values["VPN_KEYCLOAK_REALM"], "keep-realm")
        self.assertFalse(any(key.startswith("TRAEFIK_") for key in values))

    def test_new_proxy_uses_its_own_settings(self):
        values = self.configuration(InstallPlan(PANEL, "new.example.com", proxy=PROXY_TRAEFIK, reconfigure=True))
        self.assertEqual(values["TRAEFIK_HTTP_PORT"], "80")
        self.assertEqual(values["TRAEFIK_ACME_RESOLVER"], "myresolver")
        self.assertEqual(values["COMPOSE_PROFILES"], "traefik")

    def test_custom_role_is_retained_while_default_follows_selected_mode(self):
        for current, expected in [("", "vpn-user"), ("vpn-user", "vpn-user"), ("restricted-role", "restricted-role")]:
            with self.subTest(current=current):
                original = saved_settings(); original["VPN_KEYCLOAK_REQUIRED_ROLE"] = current
                values, _ = build_configuration(InstallPlan(PANEL, "new.example.com", reconfigure=True), original)
                self.assertEqual(values["VPN_KEYCLOAK_REQUIRED_ROLE"], expected)

    def test_changing_to_keycloak_does_not_clear_an_existing_role_restriction(self):
        original = saved_settings(); original["VPN_KEYCLOAK_REQUIRED_ROLE"] = "vpn-user"
        values, _ = build_configuration(InstallPlan(KEYCLOAK, "new.example.com", reconfigure=True), original)
        self.assertEqual(values["VPN_KEYCLOAK_REQUIRED_ROLE"], "vpn-user")

    def test_custom_compose_profiles_survive_proxy_reconfiguration(self):
        original = saved_settings(); original["COMPOSE_PROFILES"] = "traefik,metrics"
        values, _ = build_configuration(InstallPlan(KEYCLOAK, "new.example.com", proxy=PROXY_NGINX, reconfigure=True), original)
        self.assertEqual(values["COMPOSE_PROFILES"], "metrics")

    def test_atomic_update_backs_up_original_and_deletes_only_obsolete_managed_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            save_env(env, saved_settings())
            env.write_text("# Operator comment\n" + env.read_text())
            original = env.read_bytes()
            values = self.configuration(InstallPlan(CLASSIC, reconfigure=True))
            result = save_env(env, values, remove_keys=RECONFIGURE_KEYS)
            self.assertEqual(result.backup.read_bytes(), original)
            stored = load_env(env)
            self.assertEqual(stored, values)
            self.assertNotIn("DOMAIN_NAME", stored)
            self.assertNotIn("VPN_KEYCLOAK_URL", stored)
            self.assertIn("# Operator comment", env.read_text())
            self.assertFalse(any(key.startswith("TRAEFIK_") for key in stored))
            self.assertFalse(save_env(env, values, remove_keys=RECONFIGURE_KEYS).changed)

    def run_main(self, arguments, answers=()):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); backend = root / "backend"; backend.mkdir()
            (backend / "docker-compose.yml").touch()
            (root / "panel").mkdir(); (root / "panel/Dockerfile").touch()
            env = backend / ".env"; save_env(env, saved_settings()); original = env.read_bytes()
            cert = backend / "data/cert.pem"; cert.parent.mkdir(); cert.write_text("existing certificate")
            output = io.StringIO()
            with redirect_stdout(output), \
                 mock.patch.object(installer, "collect_preflight", return_value=PreflightReport([])) as preflight, \
                 mock.patch.object(installer, "start_stack") as start, \
                 mock.patch.object(installer, "install_nginx_with_consent") as nginx, \
                 mock.patch("builtins.input", side_effect=answers):
                result = installer.main(["--project-root", str(root), *arguments])
            return result, env.read_bytes(), original, list((backend / "data").glob("env-backups/*")), \
                cert.read_text(), start.call_count, nginx.call_count, preflight.call_count

    def test_check_does_not_write_reconfigured_values(self):
        result = self.run_main(["--non-interactive", "--reconfigure", "--mode", "panel", "--domain", "new.example.com", "--proxy", "external", "--check"])
        self.assertEqual(result[0], 0); self.assertEqual(result[1], result[2])
        self.assertEqual(result[3], []); self.assertEqual(result[5:7], (0, 0))

    def test_cancel_keeps_env_and_does_not_run_preflight(self):
        result = self.run_main([], ["3"])
        self.assertEqual(result[0], 0); self.assertEqual(result[1], result[2])
        self.assertEqual(result[3], []); self.assertEqual(result[5:], (0, 0, 0))

    def test_reconfigured_no_start_saves_backup_without_touching_services_or_certificates(self):
        result = self.run_main(["--non-interactive", "--reconfigure", "--mode", "classic", "--yes", "--no-start"])
        self.assertEqual(result[0], 0); self.assertNotEqual(result[1], result[2])
        self.assertEqual(len(result[3]), 1); self.assertEqual(result[4], "existing certificate")
        self.assertEqual(result[5:7], (0, 0))

    def test_unapproved_plan_keeps_previous_file(self):
        result = self.run_main(["--reconfigure"], ["2", "n"])
        self.assertEqual(result[0], 0); self.assertEqual(result[1], result[2])
        self.assertEqual(result[3], []); self.assertEqual(result[5:7], (0, 0))


if __name__ == "__main__":
    unittest.main()
