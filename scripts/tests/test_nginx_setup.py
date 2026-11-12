"""Unit tests for the isolated Mavi Nginx setup module."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.server_installer.nginx_setup import (
    NginxSetup,
    NginxSetupError,
    package_names,
    preflight_dns,
    render_http_site,
)


class RecordingRunner:
    def __init__(self, callback=None):
        self.commands = []
        self.kwargs = []
        self.callback = callback

    def __call__(self, command, **kwargs):
        command = list(command)
        self.commands.append(command)
        self.kwargs.append(kwargs)
        if self.callback is not None:
            return self.callback(command)
        return subprocess.CompletedProcess(command, 0)


class WindowsNginxSetup(NginxSetup):
    """Represent symlinks with placeholder files when Windows lacks privileges."""

    def __init__(self, *args, links, **kwargs):
        super().__init__(*args, **kwargs)
        self.links = links

    def _is_symlink(self, path):
        return path in self.links and os.path.lexists(path)

    def _read_symlink(self, path):
        return self.links[path]

    def _make_symlink(self, path, target):
        path.write_text("symlink placeholder", encoding="utf-8")
        self.links[path] = os.fspath(target)


def dns_answer(address):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))


class RenderingTests(unittest.TestCase):
    def test_package_names_returns_an_independent_list(self):
        first = package_names()
        self.assertEqual(first, ["nginx", "certbot", "python3-certbot-nginx"])
        first.append("docker")
        self.assertNotIn("docker", package_names())

    def test_auth_only_site_uses_loopback_and_secure_proxy_headers(self):
        config = render_http_site("Example.COM", False)
        self.assertIn("server_name auth.example.com;", config)
        self.assertIn("proxy_pass http://127.0.0.1:18080;", config)
        self.assertNotIn("server_name panel.", config)
        self.assertNotIn("18081", config)
        self.assertIn("listen 80;", config)
        self.assertIn("listen [::]:80;", config)
        self.assertIn("location ^~ /.well-known/acme-challenge/", config)
        self.assertIn("root /var/www/html;", config)
        self.assertIn("proxy_set_header Host $host;", config)
        self.assertIn("proxy_set_header X-Real-IP $remote_addr;", config)
        self.assertIn("proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;", config)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme;", config)
        self.assertIn("proxy_set_header Upgrade $http_upgrade;", config)
        self.assertIn("limit_req zone=mavi_auth_limit burst=20 nodelay;", config)
        self.assertIn("client_max_body_size 1m;", config)
        self.assertNotIn("server_name _", config)
        self.assertNotIn("*.", config)

    def test_panel_site_has_a_distinct_host_port_and_limit(self):
        config = render_http_site("example.org", True, 19080, 19081)
        self.assertEqual(config.count("server {"), 2)
        self.assertIn("server_name auth.example.org;", config)
        self.assertIn("server_name panel.example.org;", config)
        self.assertIn("proxy_pass http://127.0.0.1:19080;", config)
        self.assertIn("proxy_pass http://127.0.0.1:19081;", config)
        self.assertIn("zone=mavi_panel_limit", config)
        self.assertEqual(config.count("map $http_upgrade $mavi_connection_upgrade"), 1)

    def test_invalid_domains_are_rejected(self):
        invalid = [
            "",
            "localhost",
            "example",
            "https://example.com",
            "example.com/path",
            "*.example.com",
            "bad_label.example.com",
            "-bad.example.com",
            "bad-.example.com",
            "example.com ",
            "192.0.2.1",
            "example.com.",
            "a..example.com",
        ]
        for domain in invalid:
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                render_http_site(domain, False)
        long_base = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 57])
        self.assertEqual(len(long_base), 249)
        with self.assertRaisesRegex(ValueError, "too long"):
            render_http_site(long_base, False)

    def test_invalid_ports_are_rejected(self):
        for port in [0, -1, 65536, 1.5, "18080", True, None]:
            with self.subTest(port=port), self.assertRaises(ValueError):
                render_http_site("example.com", False, keycloak_port=port)
        with self.assertRaises(ValueError):
            render_http_site("example.com", True, 18080, 18080)


class DNSPreflightTests(unittest.TestCase):
    def test_resolution_is_structured_and_uses_only_the_injected_resolver(self):
        calls = []

        def resolver(host, port, **kwargs):
            calls.append((host, port, kwargs))
            return [dns_answer("192.0.2.10"), dns_answer("192.0.2.10")]

        result = preflight_dns("example.com", True, resolver=resolver)
        self.assertTrue(result["ok"])
        self.assertEqual(result["hosts"]["auth.example.com"]["addresses"], ["192.0.2.10"])
        self.assertEqual(result["hosts"]["panel.example.com"]["addresses"], ["192.0.2.10"])
        self.assertEqual([call[0] for call in calls], ["auth.example.com", "panel.example.com"])
        self.assertTrue(all(call[1] == 443 for call in calls))
        self.assertTrue(any("firewall/NAT" in warning for warning in result["warnings"]))

    def test_mismatch_and_resolution_failure_are_warnings_not_ping(self):
        def mismatch(host, port, **kwargs):
            return [dns_answer("192.0.2.10" if host.startswith("auth") else "192.0.2.11")]

        result = preflight_dns("example.com", True, resolver=mismatch)
        self.assertTrue(result["ok"])
        self.assertTrue(any("differ" in warning for warning in result["warnings"]))

        def failure(host, port, **kwargs):
            raise socket.gaierror(socket.EAI_NONAME, "not known")

        result = preflight_dns("example.com", False, resolver=failure)
        self.assertFalse(result["ok"])
        self.assertIsNotNone(result["hosts"]["auth.example.com"]["error"])
        self.assertTrue(any("DNS resolution failed" in warning for warning in result["warnings"]))


class NginxSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "etc" / "nginx"
        self.available = self.root / "sites-available"
        self.enabled = self.root / "sites-enabled"
        self.available.mkdir(parents=True)
        self.enabled.mkdir()
        self.config = self.available / "mavi-vpn.conf"
        self.backup = self.available / "mavi-vpn.conf.mavi-backup"
        self.link = self.enabled / "mavi-vpn.conf"
        self.links = {}
        self.runner = RecordingRunner()

    def setup(self, callback=None, which=None, runner=None):
        self.runner = runner or RecordingRunner(callback)
        implementation = WindowsNginxSetup if os.name == "nt" else NginxSetup
        kwargs = {"links": self.links} if os.name == "nt" else {}
        return implementation(
            self.runner,
            nginx_root=self.root,
            which=which or (lambda name: "/bin/systemctl"),
            **kwargs,
        )

    def make_link(self, target):
        if os.name == "nt":
            self.link.write_text("symlink placeholder", encoding="utf-8")
            self.links[self.link] = os.fspath(target)
        else:
            self.link.symlink_to(target)

    def read_link(self):
        return self.links[self.link] if os.name == "nt" else os.readlink(self.link)

    def assert_mavi_link(self):
        self.assertEqual(Path(self.read_link()), self.config)
        if os.name == "nt":
            self.assertTrue(self.link.is_file())
        else:
            self.assertTrue(self.link.is_symlink())

    def make_existing_site(self, content=b"old config\n"):
        self.config.write_bytes(content)
        self.make_link(self.config)

    def test_install_configures_and_verifies_with_exact_certbot_arguments(self):
        setup = self.setup()
        setup.install_and_configure("Example.COM", True, "admin@example.com")
        expected_certbot = [
            "certbot",
            "--nginx",
            "--non-interactive",
            "--agree-tos",
            "--email",
            "admin@example.com",
            "--redirect",
            "-d",
            "auth.example.com",
            "-d",
            "panel.example.com",
        ]
        self.assertEqual(
            self.runner.commands,
            [
                ["nginx", "-t"],
                expected_certbot,
                ["nginx", "-t"],
                ["systemctl", "reload", "nginx"],
                ["systemctl", "enable", "--now", "certbot.timer"],
                ["systemctl", "is-enabled", "--quiet", "certbot.timer"],
                ["systemctl", "is-active", "--quiet", "certbot.timer"],
            ],
        )
        self.assertTrue(self.config.is_file())
        self.assert_mavi_link()
        self.assertFalse(any(command[0] == "apt-get" for command in self.runner.commands))
        for kwargs in self.runner.kwargs:
            self.assertEqual(kwargs["check"], True)
            self.assertNotIn("shell", kwargs)

    def test_packages_are_installed_only_when_explicitly_requested(self):
        setup = self.setup()
        setup.install_and_configure("example.com", False, "admin@example.com", install_packages=True)
        self.assertEqual(self.runner.commands[0], ["apt-get", "update"])
        self.assertEqual(
            self.runner.commands[1],
            ["apt-get", "install", "-y", "--no-install-recommends", *package_names()],
        )

    def test_missing_systemctl_skips_only_timer_management(self):
        setup = self.setup(which=lambda name: None)
        setup.install_and_configure("example.com", False, "admin@example.com")
        self.assertIn(["systemctl", "reload", "nginx"], self.runner.commands)
        self.assertFalse(any("certbot.timer" in command for command in self.runner.commands))

    def test_backup_and_repeated_configuration_are_idempotent(self):
        self.make_existing_site()
        setup = self.setup()
        setup.install_and_configure("example.com", False, "admin@example.com")
        self.assertEqual(self.backup.read_bytes(), b"old config\n")
        first_content = self.config.read_bytes()
        first_mtime = self.config.stat().st_mtime_ns
        setup.install_and_configure("example.com", False, "admin@example.com")
        self.assertEqual(self.config.read_bytes(), first_content)
        self.assertEqual(self.config.stat().st_mtime_ns, first_mtime)
        self.assertEqual(self.backup.read_bytes(), b"old config\n")
        if os.name != "nt":
            self.assertEqual(self.config.stat().st_mode & 0o777, 0o644)
            self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)

    def test_failed_post_certbot_check_restores_old_file_and_link(self):
        self.make_existing_site(b"known good\n")
        test_calls = 0

        def callback(command):
            nonlocal test_calls
            if command[0] == "certbot":
                self.config.write_text("partial certbot edit", encoding="utf-8")
            if command[:2] == ["nginx", "-t"]:
                test_calls += 1
                if test_calls == 2:
                    raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)

        setup = self.setup(callback)
        with self.assertRaises(NginxSetupError):
            setup.install_and_configure("example.com", False, "admin@example.com")
        self.assertEqual(self.config.read_bytes(), b"known good\n")
        self.assert_mavi_link()
        self.assertGreaterEqual(test_calls, 3)

    def test_failed_certbot_removes_new_file_and_symlink(self):
        def callback(command):
            if command[0] == "certbot":
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)

        setup = self.setup(callback)
        with self.assertRaises(NginxSetupError):
            setup.install_and_configure("example.com", True, "admin@example.com")
        self.assertFalse(os.path.lexists(self.config))
        self.assertFalse(os.path.lexists(self.link))
        self.assertFalse(os.path.lexists(self.backup))

    def test_default_and_other_sites_are_never_modified(self):
        default = self.enabled / "default"
        default.write_text("foreign default", encoding="utf-8")
        other = self.available / "other.conf"
        other.write_text("foreign other", encoding="utf-8")
        setup = self.setup()
        setup.install_and_configure("example.com", False, "admin@example.com")
        self.assertEqual(default.read_text(encoding="utf-8"), "foreign default")
        self.assertEqual(other.read_text(encoding="utf-8"), "foreign other")

    def test_foreign_mavi_symlink_is_rejected_without_changes(self):
        other = self.available / "other.conf"
        other.write_text("foreign", encoding="utf-8")
        self.make_link(other)
        setup = self.setup()
        with self.assertRaisesRegex(NginxSetupError, "foreign enabled site"):
            setup.install_and_configure("example.com", False, "admin@example.com")
        self.assertFalse(self.config.exists())
        self.assertEqual(Path(self.read_link()), other)
        self.assertEqual(self.runner.commands, [])

    def test_missing_sites_available_has_a_clear_error(self):
        missing_root = Path(self.temporary.name) / "missing-nginx"
        setup = NginxSetup(self.runner, nginx_root=missing_root)
        with self.assertRaisesRegex(NginxSetupError, "refusing to modify nginx.conf"):
            setup.install_and_configure("example.com", False, "admin@example.com")
        self.assertEqual(self.runner.commands, [])

    def test_invalid_email_is_rejected_before_commands_or_writes(self):
        invalid = ["", "plain", "a@@example.com", "a b@example.com", ".a@example.com", "a@localhost"]
        setup = self.setup()
        for email in invalid:
            with self.subTest(email=email), self.assertRaises(ValueError):
                setup.install_and_configure("example.com", False, email)
        self.assertEqual(self.runner.commands, [])
        self.assertFalse(self.config.exists())

    def test_runner_output_is_captured_and_no_shell_is_used(self):
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
        setup = self.setup(which=lambda name: None, runner=runner)
        setup.install_and_configure("example.com", False, "admin@example.com")
        for call in runner.call_args_list:
            self.assertIsInstance(call.args[0], list)
            self.assertTrue(call.kwargs["capture_output"])
            self.assertTrue(call.kwargs["text"])
            self.assertNotIn("shell", call.kwargs)


if __name__ == "__main__":
    unittest.main()
