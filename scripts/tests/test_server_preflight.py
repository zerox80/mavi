"""Read-only DNS, listener and guided-retry behavior, including the reported VPS case."""
from contextlib import redirect_stdout
import errno
import io
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.server_installer import installer, preflight, readiness
from scripts.server_installer.config import InstallPlan, PANEL, PROXY_NGINX
from scripts.server_installer.preflight import Check, PreflightReport


class NetworkChecksTests(unittest.TestCase):
    def port(self, error, owner="nginx", host_nginx=True, valid=True, **options):
        report = PreflightReport([])
        def runner(args, **kwargs):
            output = f'LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("{owner}",pid=123,fd=4))' if args[0] == "ss" else ""
            return subprocess.CompletedProcess(args, 0 if valid or args[0] != "nginx" else 1, output, "")
        with mock.patch.object(preflight.socket, "socket") as sockets, \
             mock.patch.object(preflight.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
             mock.patch.object(preflight, "_host_nginx", return_value=host_nginx):
            sockets.return_value.__enter__.return_value.bind.side_effect = error
            preflight._port_check(report, 80, "tcp", required=True, runner=runner, reuse_nginx=True, **options)
        return report.checks[0]

    def test_existing_host_nginx_is_reused_after_validation(self):
        result = self.port(OSError(errno.EADDRINUSE, "busy"))
        self.assertEqual(result.status, "PASS")
        self.assertIn("reused", result.detail)

    def test_foreign_proxy_is_blocked_without_being_stopped(self):
        result = self.port(OSError(errno.EADDRINUSE, "busy"), owner="caddy", host_nginx=False)
        self.assertEqual(result.status, "FAIL")
        self.assertIn("caddy", result.detail)
        self.assertIn("external reverse proxy", result.detail)

    def test_container_nginx_is_not_mistaken_for_host_nginx(self):
        result = self.port(OSError(errno.EADDRINUSE, "busy"), host_nginx=False)
        self.assertEqual(result.status, "FAIL")

    def test_invalid_nginx_config_blocks_setup(self):
        result = self.port(OSError(errno.EADDRINUSE, "busy"), valid=False)
        self.assertEqual(result.status, "FAIL")
        self.assertIn("invalid configuration", result.detail)

    def test_permission_error_does_not_claim_port_is_busy(self):
        result = self.port(PermissionError(errno.EACCES, "denied"))
        self.assertEqual(result.status, "FAIL")
        self.assertIn("does not prove", result.detail)

    def test_host_nginx_requires_matching_host_root_and_executable(self):
        with mock.patch.object(preflight.shutil, "which", return_value="/usr/sbin/nginx"), \
             mock.patch.object(Path, "samefile", side_effect=[True, True]):
            self.assertTrue(preflight._host_nginx([("nginx", 123)]))
        with mock.patch.object(preflight.shutil, "which", return_value="/usr/sbin/nginx"), \
             mock.patch.object(Path, "samefile", return_value=False):
            self.assertFalse(preflight._host_nginx([("nginx", 123)]))

    def test_docker_nat_mapping_is_found_even_when_local_bind_would_succeed(self):
        mapping = json.dumps({"8080/tcp": [{"HostPort": "80", "HostIp": "0.0.0.0"}]})
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "abc\n" if args[1] == "ps" else f'"/other-proxy"\t{{}}\t{mapping}\n', "")
        report = PreflightReport([])
        with mock.patch.object(preflight.shutil, "which", return_value="/usr/bin/docker"), \
             mock.patch.object(preflight.socket, "socket") as sockets:
            preflight._port_check(report, 80, "tcp", True, runner, inspect_docker=True)
        sockets.assert_not_called()
        self.assertEqual(report.checks[0].status, "FAIL")
        self.assertIn("other-proxy", report.checks[0].detail)

    def test_only_matching_compose_service_is_reused(self):
        backend = Path("/srv/mavi/backend")
        labels = {"com.docker.compose.service": "traefik", "com.docker.compose.project.working_dir": str(backend)}
        owners = [("traefik", labels)]
        self.assertTrue(preflight._same_compose_service(owners, backend, "traefik"))
        self.assertFalse(preflight._same_compose_service(owners, Path("/srv/other/backend"), "traefik"))
        self.assertFalse(preflight._same_compose_service(owners, backend, "vpn-server"))

    def test_missing_dns_blocks_managed_https_but_not_config_only(self):
        for required, status in [(True, "FAIL"), (False, "WARN")]:
            report = PreflightReport([])
            with mock.patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("missing")):
                preflight._dns_check(report, "auth.example.com", required=required)
            self.assertEqual(report.checks[0].status, status)

    def test_dns_record_guidance_uses_public_addresses_only(self):
        report = PreflightReport([Check("dns:auth.example.com", "FAIL", "missing")], ["57.128.198.195"])
        output = io.StringIO()
        with redirect_stdout(output): readiness.explain_network(report)
        self.assertIn("auth.example.com", output.getvalue())
        self.assertIn("57.128.198.195", output.getvalue())
        self.assertNotIn("172.17.0.1", output.getvalue())

    def test_private_container_and_vpn_addresses_are_not_public_candidates(self):
        def runner(args, **kwargs):
            addresses = ["57.128.198.195/32", "172.17.0.1/16", "100.119.7.23/32"] if "-4" in args else ["2001:41d0:601:1100::9fc5/128", "fd7a:115c:a1e0::1/128"]
            return subprocess.CompletedProcess(args, 0, "\n".join(f"2: eth0 inet {ip} scope global" for ip in addresses), "")
        report = PreflightReport([])
        with mock.patch.object(preflight.shutil, "which", return_value="/usr/bin/ip"):
            preflight._address_checks(report, runner)
        self.assertEqual(report.public_ipv4, ["57.128.198.195"])
        self.assertEqual(report.public_ipv6, ["2001:41d0:601:1100::9fc5"])

    def test_reported_missing_auth_dns_and_existing_nginx_case(self):
        report = PreflightReport([], ["57.128.198.195"])
        def resolve(name, *args, **kwargs):
            if name.startswith("auth."): raise socket.gaierror("missing")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("57.128.198.195", 443))]
        def bind(address):
            if address[1] == 80: raise OSError(errno.EADDRINUSE, "busy")
        def runner(args, **kwargs):
            output = 'LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=123,fd=4))' if args[0] == "ss" else ""
            return subprocess.CompletedProcess(args, 0, output, "")
        with mock.patch.object(preflight.socket, "socket") as sockets, \
             mock.patch.object(socket, "getaddrinfo", side_effect=resolve), \
             mock.patch.object(preflight.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
             mock.patch.object(preflight, "_host_nginx", return_value=True):
            sockets.return_value.__enter__.return_value.bind.side_effect = bind
            preflight._network_checks(report, InstallPlan(PANEL, "mavi-vpn.eu", proxy=PROXY_NGINX), {}, runner)
        self.assertEqual([c.name for c in report.failures], ["dns:auth.mavi-vpn.eu"])
        self.assertTrue(any(c.name == "port:80/tcp" and c.status == "PASS" for c in report.checks))


class RetryFlowTests(unittest.TestCase):
    def run_main(self, action, checks_only=False, noninteractive=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "backend").mkdir(); (root / "backend/docker-compose.yml").touch()
            report = PreflightReport([Check("dns:auth.example.com", "FAIL", "missing")], ["57.128.198.195"])
            plan = InstallPlan("keycloak", "example.com", "admin@example.com", PROXY_NGINX)
            args = ["--project-root", str(root)] + (["--check"] if checks_only else [])
            if noninteractive: args += ["--non-interactive", "--mode", "keycloak", "--domain", "example.com", "--yes"]
            output = io.StringIO()
            with redirect_stdout(output), mock.patch.object(installer, "interactive_plan", return_value=plan), \
                 mock.patch.object(installer, "collect_preflight", side_effect=[report, PreflightReport([])]) as collect, \
                 mock.patch.object(installer, "network_action", return_value=action) as choose, \
                 mock.patch.object(installer, "confirm", return_value=True), \
                 mock.patch.object(installer, "prepare_images") as pull, \
                 mock.patch.object(installer, "install_nginx_with_consent", return_value=True) as nginx, \
                 mock.patch.object(installer, "start_stack") as start:
                code = installer.main(args)
            return code, (root / "backend/.env").exists(), collect.call_count, choose.call_count, pull.call_count, nginx.call_count, start.call_count

    def test_retry_rechecks_before_applying(self):
        self.assertEqual(self.run_main("retry"), (0, True, 2, 1, 1, 1, 1))

    def test_cancel_keeps_configuration_untouched(self):
        self.assertEqual(self.run_main("cancel"), (0, False, 1, 1, 0, 0, 0))

    def test_check_never_opens_menu_or_changes_files(self):
        self.assertEqual(self.run_main("retry", checks_only=True), (1, False, 1, 0, 0, 0, 0))

    def test_save_only_does_not_pull_install_or_start_services(self):
        self.assertEqual(self.run_main("save"), (0, True, 2, 1, 0, 0, 0))

    def test_noninteractive_failure_leaves_no_config(self):
        self.assertEqual(self.run_main("cancel", noninteractive=True), (1, False, 1, 1, 0, 0, 0))


if __name__ == "__main__": unittest.main()
