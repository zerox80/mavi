"""Test installer planning and file updates without Docker or host changes."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.server_installer.config import (
    CLASSIC,
    KEYCLOAK,
    PANEL,
    PROXY_EXTERNAL,
    PROXY_NGINX,
    PROXY_TRAEFIK,
    InstallPlan,
    build_configuration,
    compose_file_for_mode,
    generate_secret,
    load_env,
    mode_from_environment,
    parse_env,
    panel_vpn_endpoint,
    plan_from_existing,
    save_env,
    update_env_text,
)
from scripts.server_installer.keycloak_bootstrap import KeycloakAdmin, KeycloakError, PANEL_ROLES, bootstrap_panel
from scripts.server_installer import installer
from scripts.server_installer.preflight import PreflightReport

ROOT = Path(__file__).resolve().parents[2]


def temporary_directory():
    return tempfile.TemporaryDirectory(dir=Path(__file__).parent)


def secret_factory(name: str) -> str:
    return f"generated-{name}-abcdefghijklmnopqrstuvwxyz-0123456789"


def panel_plan(**changes) -> InstallPlan:
    values = {
        "mode": PANEL,
        "domain": "example.com",
        "acme_email": "admin@example.com",
        "proxy": PROXY_NGINX,
    }
    values.update(changes)
    return InstallPlan(**values)


class ModeTests(unittest.TestCase):
    def test_panel_implies_keycloak_regardless_of_old_flag(self):
        values, generated = build_configuration(
            panel_plan(),
            {"VPN_KEYCLOAK_ENABLED": "false", "VPN_AUTH_TOKEN": ""},
            secret_factory,
        )
        self.assertEqual(values["MAVI_WEB_PANEL_ENABLED"], "true")
        self.assertEqual(values["VPN_KEYCLOAK_ENABLED"], "true")
        self.assertEqual(values["VPN_AUTH_TOKEN"], "")
        self.assertNotIn("VPN_AUTH_TOKEN", generated)
        self.assertEqual(values["VPN_KEYCLOAK_REQUIRED_ROLE"], "vpn-user")

    def test_mode_detection_handles_old_compose_files(self):
        cases = [
            ({}, CLASSIC),
            ({"VPN_KEYCLOAK_ENABLED": "true"}, KEYCLOAK),
            ({"MAVI_WEB_PANEL_ENABLED": "true", "VPN_KEYCLOAK_ENABLED": "false"}, PANEL),
            ({"COMPOSE_FILE": "docker-compose.yml:panel/docker-compose.yml"}, PANEL),
            ({"COMPOSE_FILE": "docker-compose.yml:keycloak/docker-compose.yml"}, KEYCLOAK),
        ]
        for values, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(mode_from_environment(values), expected)

    def test_compose_file_selection_is_exact(self):
        self.assertEqual(compose_file_for_mode(CLASSIC), "docker-compose.yml")
        self.assertEqual(compose_file_for_mode(KEYCLOAK), "docker-compose.yml:keycloak/docker-compose.yml")
        self.assertEqual(
            compose_file_for_mode(PANEL),
            "docker-compose.yml:keycloak/docker-compose.yml:panel/docker-compose.yml",
        )

    def test_keycloak_only_keeps_manual_flow_without_panel_values(self):
        plan = InstallPlan(KEYCLOAK, "example.com", proxy=PROXY_EXTERNAL)
        values, generated = build_configuration(plan, {}, secret_factory)
        self.assertEqual(values["VPN_KEYCLOAK_ENABLED"], "true")
        self.assertEqual(values["MAVI_WEB_PANEL_ENABLED"], "false")
        self.assertEqual(values["VPN_KEYCLOAK_REQUIRED_ROLE"], "")
        self.assertNotIn("MAVI_PANEL_CLIENT_SECRET", values)
        self.assertNotIn("VPN_AUTH_TOKEN", generated)

    def test_classic_generates_token_and_uses_fixed_mtu(self):
        values, generated = build_configuration(InstallPlan(CLASSIC), {"VPN_MTU": "1360"}, secret_factory)
        self.assertEqual(values["VPN_KEYCLOAK_ENABLED"], "false")
        self.assertEqual(values["VPN_MTU"], "1280")
        self.assertEqual(values["COMPOSE_PROFILES"], "")
        self.assertIn("VPN_AUTH_TOKEN", generated)

    def test_panel_transport_and_endpoint_match_server_settings(self):
        for cr, http2, expected, port in [
            ("true", "", "http3_censorship_resistant", 12443),
            ("false", "", "quic", 12443),
            ("false", "[::]:8443", "http2", 8443),
        ]:
            with self.subTest(expected=expected):
                existing = {
                    "VPN_CENSORSHIP_RESISTANT": cr,
                    "VPN_HTTP2_BIND_ADDR": http2,
                    "VPN_PUBLIC_HOST": "vpn.example.net",
                    "VPN_PORT": "12443",
                }
                values, _ = build_configuration(panel_plan(), existing, secret_factory)
                self.assertEqual(values["MAVI_PANEL_TRANSPORT"], expected)
                self.assertEqual(panel_vpn_endpoint(values), f"vpn.example.net:{port}")
                self.assertNotIn("MAVI_PANEL_VPN_ENDPOINT", values)

    def test_panel_preserves_explicit_public_endpoint(self):
        existing = {
            "VPN_HTTP2_BIND_ADDR": "0.0.0.0:8443",
            "MAVI_PANEL_VPN_ENDPOINT": "vpn.example.net:443",
        }
        values, _ = build_configuration(panel_plan(), existing, secret_factory)
        self.assertEqual(values["MAVI_PANEL_VPN_ENDPOINT"], "vpn.example.net:443")
        self.assertEqual(values["MAVI_PANEL_TRANSPORT"], "http2")


class EnvironmentTests(unittest.TestCase):
    def test_existing_secrets_are_never_regenerated(self):
        existing = {
            "VPN_AUTH_TOKEN": "keep-token",
            "KEYCLOAK_DB_PASSWORD": "keep-database",
            "KEYCLOAK_ADMIN_PASS": "keep-admin",
            "MAVI_PANEL_DB_PASSWORD": "keep-panel-database",
            "MAVI_PANEL_CLIENT_SECRET": "keep-panel-client",
            "MAVI_PANEL_PROXY_TOKEN": "keep-panel-proxy-token",
            "CUSTOM_SETTING": "keep-custom",
        }

        def fail_if_generated(name):
            raise AssertionError(f"attempted to regenerate {name}")

        values, generated = build_configuration(panel_plan(), existing, fail_if_generated)
        self.assertEqual(generated, set())
        for key, value in existing.items():
            self.assertEqual(values[key], value)

    def test_save_is_atomic_private_and_idempotent(self):
        with temporary_directory() as directory:
            path = Path(directory) / ".env"
            original = "# local settings\nCUSTOM_SETTING=untouched\nVPN_AUTH_TOKEN=keep-token\n"
            path.write_text(original, encoding="utf-8")
            existing = parse_env(original)
            desired, _ = build_configuration(InstallPlan(CLASSIC), existing, secret_factory)
            first = save_env(path, desired)
            self.assertTrue(first.changed)
            self.assertTrue(first.backup.is_file())
            self.assertEqual(first.backup.read_text(encoding="utf-8"), original)
            self.assertIn("CUSTOM_SETTING=untouched", path.read_text(encoding="utf-8"))
            self.assertEqual(load_env(path)["VPN_AUTH_TOKEN"], "keep-token")
            content = path.read_bytes()
            mtime = path.stat().st_mtime_ns
            second = save_env(path, desired)
            self.assertFalse(second.changed)
            self.assertIsNone(second.backup)
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mtime_ns, mtime)
            backups = list((Path(directory) / "data" / "env-backups").iterdir())
            self.assertEqual(backups, [first.backup])
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(first.backup.stat().st_mode & 0o777, 0o600)
                self.assertEqual(first.backup.parent.stat().st_mode & 0o777, 0o700)

    def test_second_planning_pass_generates_no_new_secrets(self):
        with temporary_directory() as directory:
            path = Path(directory) / ".env"
            plan = panel_plan()
            desired, generated = build_configuration(plan, {}, secret_factory)
            save_env(path, desired)
            stored = load_env(path)
            second, regenerated = build_configuration(plan_from_existing(stored), stored, secret_factory)
            self.assertEqual(regenerated, set())
            self.assertFalse(save_env(path, second).changed)
            for key in generated:
                self.assertEqual(stored[key], desired[key])

    def test_env_parser_and_updater_preserve_comments_and_reject_injection(self):
        original = "export TOKEN='value with spaces' # comment\nEMPTY=\n"
        self.assertEqual(parse_env(original)["TOKEN"], "value with spaces")
        self.assertEqual(update_env_text(original, parse_env(original)), original)
        with self.assertRaisesRegex(ValueError, "newlines"):
            update_env_text("", {"VPN_AUTH_TOKEN": "safe\nMALICIOUS=true"})

    def test_generated_secrets_have_high_entropy_encoding(self):
        first = generate_secret("first")
        second = generate_secret("second")
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 40)
        self.assertRegex(first, r"^[A-Za-z0-9_-]+$")


class ComposeContractTests(unittest.TestCase):
    def test_panel_service_has_required_environment_and_hardening(self):
        content = (ROOT / "backend" / "panel" / "docker-compose.yml").read_text(encoding="utf-8")
        required = [
            "image: ${MAVI_PANEL_IMAGE:-ghcr.io/zerox80/mavi-vpn:panel-latest}",
            "MAVI_PANEL_DATABASE_URL: postgresql://mavi_panel:${MAVI_PANEL_DB_PASSWORD}@panel-db:5432/mavi_panel",
            "MAVI_PANEL_PUBLIC_URL: https://panel.${DOMAIN_NAME}",
            "MAVI_PANEL_KEYCLOAK_URL: ${VPN_KEYCLOAK_URL}",
            "MAVI_PANEL_CLIENT_ID: mavi-panel",
            "MAVI_PANEL_CLIENT_SECRET: ${MAVI_PANEL_CLIENT_SECRET:-}",
            "MAVI_PANEL_VPN_ENDPOINT: ${MAVI_PANEL_VPN_ENDPOINT:-}",
            "MAVI_PANEL_CERT_PIN_FILE: /server-data/cert_pin.txt",
            "MAVI_PANEL_ECH_CONFIG_FILE: /server-data/ech_config_hex.txt",
            "MAVI_PANEL_BIND: 0.0.0.0:8081",
            "./data:/server-data:ro",
            "condition: service_healthy",
            "Host(`panel.${DOMAIN_NAME}`)",
            "VPN_KEYCLOAK_ENABLED: \"true\"",
            "VPN_KEYCLOAK_REQUIRED_ROLE: ${VPN_KEYCLOAK_REQUIRED_ROLE:-vpn-user}",
        ]
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, content)
        self.assertNotIn("/var/run/docker.sock", content)
        self.assertNotIn("network_mode:", content)
        self.assertNotIn("/dev/net/tun", content)
        # The panel publishes a loopback-only HTTP port for the host Nginx
        # proxy; it must never bind a public interface.
        self.assertIn("127.0.0.1:18081:8081", content)
        self.assertNotIn("0.0.0.0:18081", content)
        self.assertNotIn("POSTGRES_PASSWORD", content.split("healthcheck:", 1)[1].split("security_opt:", 1)[0])

    def test_proxy_defaults_keep_external_mode_and_allow_standalone_https(self):
        base = (ROOT / "backend" / "docker-compose.yml").read_text(encoding="utf-8")
        keycloak = (ROOT / "backend" / "keycloak" / "docker-compose.yml").read_text(encoding="utf-8")
        panel = (ROOT / "backend" / "panel" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("${TRAEFIK_BIND_ADDR:-127.0.0.1}", base)
        self.assertIn("${TRAEFIK_FORWARDED_HEADERS_INSECURE:-true}", base)
        self.assertNotIn("forwardedHeaders.insecure=true\"", base)
        for filename in (keycloak, panel):
            self.assertIn("${TRAEFIK_ROUTER_ENTRYPOINTS:-web}", filename)
            self.assertIn("${TRAEFIK_TLS_ENABLED:-false}", filename)
            self.assertIn("${TRAEFIK_ACME_RESOLVER:-myresolver}", filename)
        # Host Nginx mode relies on loopback HTTP ports for Keycloak and the
        # panel; Certbot terminates HTTPS and forwards to these addresses.
        self.assertIn("127.0.0.1:18080:8080", keycloak)
        self.assertIn("127.0.0.1:18081:8081", panel)

    def test_existing_plan_classification_preserves_proxy_modes(self):
        cases = [
            ({"TRAEFIK_BIND_ADDR": "0.0.0.0", "VPN_KEYCLOAK_ENABLED": "true"}, PROXY_TRAEFIK),
            ({"TRAEFIK_BIND_ADDR": "127.0.0.1", "VPN_KEYCLOAK_ENABLED": "true"}, PROXY_EXTERNAL),
            ({"VPN_KEYCLOAK_ENABLED": "true", "ACME_EMAIL": "admin@example.com"}, PROXY_NGINX),
            ({"COMPOSE_PROFILES": "traefik,keycloak", "ACME_EMAIL": "admin@example.com"}, PROXY_EXTERNAL),
            ({"VPN_KEYCLOAK_ENABLED": "true"}, PROXY_EXTERNAL),
        ]
        for values, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(plan_from_existing(values).proxy, expected)

    def test_new_external_proxy_stays_external_on_rerun(self):
        plan = panel_plan(proxy=PROXY_EXTERNAL, acme_email="")
        values, _ = build_configuration(plan, {}, secret_factory)
        self.assertEqual(plan_from_existing(values).proxy, PROXY_EXTERNAL)

    def test_no_start_writes_configuration_without_running_nginx_or_compose(self):
        with temporary_directory() as directory:
            root = Path(directory)
            backend = root / "backend"
            backend.mkdir()
            (backend / "docker-compose.yml").touch()
            with (
                mock.patch.object(installer, "collect_preflight", return_value=PreflightReport([])),
                mock.patch.object(installer, "install_nginx_with_consent") as nginx,
                mock.patch.object(installer, "start_stack") as stack,
            ):
                result = installer.main([
                    "--project-root", str(root), "--non-interactive", "--mode", "keycloak",
                    "--domain", "example.com", "--yes", "--no-start",
                ])
            self.assertEqual(result, 0)
            self.assertEqual(load_env(backend / ".env")["MAVI_PROXY_MODE"], PROXY_NGINX)
            nginx.assert_not_called()
            stack.assert_not_called()

    def test_new_nginx_install_writes_no_traefik_settings_and_no_profile(self):
        values, _ = build_configuration(panel_plan(), {}, secret_factory)
        self.assertEqual(values["COMPOSE_PROFILES"], "")
        for key in (
            "TRAEFIK_BIND_ADDR",
            "TRAEFIK_HTTP_PORT",
            "TRAEFIK_HTTPS_PORT",
            "TRAEFIK_ROUTER_ENTRYPOINTS",
            "TRAEFIK_TLS_ENABLED",
            "TRAEFIK_ACME_RESOLVER",
            "TRAEFIK_FORWARDED_HEADERS_INSECURE",
        ):
            self.assertNotIn(key, values)

    def test_legacy_traefik_install_keeps_profile_and_acme_settings(self):
        plan = panel_plan(proxy=PROXY_TRAEFIK)
        values, _ = build_configuration(plan, {}, secret_factory)
        self.assertEqual(values["COMPOSE_PROFILES"], "traefik")
        self.assertEqual(values["TRAEFIK_BIND_ADDR"], "0.0.0.0")
        self.assertEqual(values["TRAEFIK_TLS_ENABLED"], "true")
        self.assertEqual(values["TRAEFIK_ACME_RESOLVER"], "myresolver")

    def test_existing_install_preserves_compose_profile_and_traefik_values(self):
        existing = {
            "COMPOSE_PROFILES": "traefik",
            "TRAEFIK_BIND_ADDR": "0.0.0.0",
            "TRAEFIK_HTTPS_PORT": "443",
        }
        values, _ = build_configuration(plan_from_existing(existing), existing, secret_factory)
        self.assertEqual(values["COMPOSE_PROFILES"], "traefik")
        self.assertEqual(values["TRAEFIK_BIND_ADDR"], "0.0.0.0")
        self.assertEqual(values["TRAEFIK_HTTPS_PORT"], "443")

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML is not installed")
    def test_compose_files_parse_as_yaml(self):
        import yaml

        for path in [
            ROOT / "backend" / "docker-compose.yml",
            ROOT / "backend" / "keycloak" / "docker-compose.yml",
            ROOT / "backend" / "panel" / "docker-compose.yml",
        ]:
            with self.subTest(path=path):
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertIsInstance(document["services"], dict)

    def test_source_syntax_and_realm_json(self):
        for path in (ROOT / "scripts" / "server_installer").glob("*.py"):
            with self.subTest(path=path):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 500)
        json.loads((ROOT / "backend" / "keycloak" / "mavi-vpn-realm.json").read_text(encoding="utf-8"))
        launcher = (ROOT / "install-server.sh").read_text(encoding="utf-8")
        self.assertTrue(launcher.startswith("#!/bin/sh\n"))
        self.assertNotIn("curl", launcher)


class KeycloakBootstrapTests(unittest.TestCase):
    def api(self):
        return KeycloakAdmin("https://auth.example.com", "mavi-vpn", "admin", "not-sent")

    def test_interrupted_bootstrap_can_still_adopt_the_authoritative_secret(self):
        values, _ = build_configuration(panel_plan(), {}, secret_factory)
        self.assertNotIn("MAVI_PANEL_CLIENT_SECRET", values)
        with temporary_directory() as directory:
            path = Path(directory) / ".env"
            save_env(path, values)
            with mock.patch("scripts.server_installer.keycloak_bootstrap.KeycloakAdmin") as admin_class:
                admin = admin_class.return_value
                admin.ensure_client.return_value = ("client-id", "secret-from-keycloak")
                admin.ensure_default_roles.side_effect = KeycloakError("interrupted after creating client")
                with self.assertRaises(KeycloakError):
                    bootstrap_panel(values, path)
                values = load_env(path)
                self.assertNotIn("MAVI_PANEL_CLIENT_SECRET", values)
                admin.ensure_default_roles.side_effect = None
                bootstrap_panel(values, path)
                self.assertTrue(admin.ensure_client.call_args.kwargs["adopt_existing_secret"])
                self.assertEqual(load_env(path)["MAVI_PANEL_CLIENT_SECRET"], "secret-from-keycloak")

    def test_existing_client_secret_is_not_reset_when_adopted(self):
        api = self.api()
        existing = {
            "id": "client-id", "clientId": "mavi-panel",
            "attributes": {"existing": "kept"}, "secret": "existing-secret",
        }
        requests = []

        def request(method, path, payload=None, **kwargs):
            requests.append((method, path, payload))
            if method == "GET" and path.endswith("/clients?clientId=mavi-panel"):
                return [existing]
            if method == "GET" and path == "/admin/realms/mavi-vpn/clients/client-id/client-secret":
                return {"value": "existing-secret"}
            if method == "GET" and path.endswith("/protocol-mappers/models"):
                return [{"id": "mapper-id", "name": "mavi-panel-realm-roles"}]
            return {}

        api._request = request
        _, effective = api.ensure_client("https://panel.example.com", "new-unused-secret", True)
        self.assertEqual(effective, "existing-secret")
        update = next(item for item in requests if item[0] == "PUT" and item[1].endswith("/client-id"))
        self.assertNotIn("secret", update[2])
        self.assertEqual(update[2]["redirectUris"], ["https://panel.example.com/auth/callback"])
        self.assertEqual(update[2]["attributes"]["existing"], "kept")
        self.assertEqual(update[2]["attributes"]["pkce.code.challenge.method"], "S256")

    def test_mismatched_existing_secret_fails_without_client_update(self):
        api = self.api()
        existing = {"id": "client-id", "clientId": "mavi-panel"}

        def request(method, path, **kwargs):
            if method == "GET" and "clients?clientId=" in path:
                return [existing]
            if method == "GET" and path == "/admin/realms/mavi-vpn/clients/client-id/client-secret":
                return {"value": "existing-secret"}
            return {}

        api._request = request
        with self.assertRaisesRegex(KeycloakError, "No secret was changed"):
            api.ensure_client("https://panel.example.com", "different-secret")

    def test_missing_or_unusable_existing_secret_never_updates_the_client(self):
        existing = {"id": "client-id", "clientId": "mavi-panel"}
        for response in (None, {}, {"value": ""}, {"value": 123}, KeycloakError("HTTP 404")):
            with self.subTest(response=response):
                api = self.api()
                api._request = mock.Mock(side_effect=[[existing], response])
                with self.assertRaises(KeycloakError):
                    api.ensure_client("https://panel.example.com", "new-secret", adopt_existing_secret=True)
                self.assertEqual(api._request.call_args_list, [
                    mock.call("GET", "/admin/realms/mavi-vpn/clients?clientId=mavi-panel"),
                    mock.call("GET", "/admin/realms/mavi-vpn/clients/client-id/client-secret"),
                ])

    def test_existing_admin_password_and_roles_are_preserved(self):
        api = self.api()
        api.ensure_role = lambda name: {"name": name, "id": name}
        requests = []

        def request(method, path, payload=None, **kwargs):
            requests.append((method, path, payload))
            if method == "GET" and "/users?" in path:
                return [{"id": "user-id", "username": "panel-admin"}]
            if method == "GET" and path.endswith("/role-mappings/realm"):
                return [{"name": "custom-role"}, {"name": "vpn-user"}]
            return {}

        api._request = request
        result = api.ensure_admin("panel-admin", "admin@example.com", lambda: "must-not-be-used")
        self.assertFalse(result.created)
        self.assertEqual(result.temporary_password, "")
        self.assertFalse(any("reset-password" in path for _, path, _ in requests))
        assigned = next(payload for method, path, payload in requests if method == "POST" and path.endswith("/role-mappings/realm"))
        self.assertEqual({role["name"] for role in assigned}, set(PANEL_ROLES) - {"vpn-user"})
        self.assertNotIn("custom-role", {role["name"] for role in assigned})


if __name__ == "__main__":
    unittest.main()
