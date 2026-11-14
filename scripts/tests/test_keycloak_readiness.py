"""Permanent startup failures stop early; transient startup still waits."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from scripts.server_installer.keycloak_bootstrap import KeycloakAdmin, KeycloakError, KeycloakHttpError
from scripts.server_installer import keycloak_diagnostics


class KeycloakReadinessTests(unittest.TestCase):
    def test_invalid_realm_failure_stops_before_authentication_retry(self):
        api = KeycloakAdmin("https://auth.example.com", "mavi-vpn", "admin", "private")
        with mock.patch.object(api, "authenticate") as authenticate:
            with self.assertRaisesRegex(KeycloakError, "unsupported field"):
                api.wait_until_ready(startup_check=mock.Mock(side_effect=KeycloakError("unsupported field")))
        authenticate.assert_not_called()

    def test_local_ready_with_broken_proxy_stops_after_three_attempts(self):
        elapsed = [0.0]
        api = KeycloakAdmin("https://auth.example.com", "mavi-vpn", "admin", "private",
                            sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds), monotonic=lambda: elapsed[0])
        with mock.patch.object(api, "authenticate", side_effect=KeycloakHttpError("HTTP 502", 502)) as authenticate:
            with self.assertRaisesRegex(KeycloakError, "localhost:18080"):
                api.wait_until_ready(startup_check=lambda: True)
        self.assertEqual(authenticate.call_count, 3)
        self.assertLess(elapsed[0], 600)

    def test_bad_existing_admin_credentials_do_not_wait_ten_minutes(self):
        api = KeycloakAdmin("https://auth.example.com", "mavi-vpn", "admin", "private")
        with mock.patch.object(api, "authenticate", side_effect=KeycloakHttpError("HTTP 401", 401)):
            with self.assertRaisesRegex(KeycloakError, "no password was reset"): api.wait_until_ready()

    def test_current_container_logs_are_scoped_to_latest_start(self):
        state = {"Status": "running", "OOMKilled": False, "StartedAt": "2026-11-14T09:50:00Z"}
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            text = 'abc\n' if command[1] == 'compose' else json.dumps(state) if command[1] == 'inspect' else 'Unrecognized field "accessTokenSignatureAlgorithm"'
            return subprocess.CompletedProcess(command, 0, text, "")
        with mock.patch.object(keycloak_diagnostics.subprocess, "run", side_effect=runner):
            with self.assertRaisesRegex(KeycloakError, "accessTokenSignatureAlgorithm"):
                keycloak_diagnostics.check_startup(Path("/srv/mavi/backend"), {"COMPOSE_FILE": "docker-compose.yml"})
        self.assertIn(state["StartedAt"], commands[-1])
        self.assertIn("--since", commands[-1])

    def test_oom_is_reported_without_resetting_anything(self):
        outputs = ['abc\n', json.dumps({"OOMKilled": True})]
        with mock.patch.object(keycloak_diagnostics.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0, text, "") for text in outputs]):
            with self.assertRaisesRegex(KeycloakError, "available memory"):
                keycloak_diagnostics.check_startup(Path("/srv/mavi/backend"), {"COMPOSE_FILE": "docker-compose.yml"})


if __name__ == "__main__": unittest.main()
