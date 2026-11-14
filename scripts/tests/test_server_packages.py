"""Reuse installed proxy components and distinguish cancellation from failures."""
from contextlib import redirect_stdout
import io
import subprocess
import unittest
from unittest import mock

from scripts.server_installer import installer, packages
from scripts.server_installer.config import InstallPlan, PANEL


class ProxyPackageTests(unittest.TestCase):
    def test_installed_binaries_and_plugin_require_no_packages(self):
        with mock.patch.object(packages.shutil, "which", side_effect=lambda name: "/usr/bin/" + name), \
             mock.patch.object(packages.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "install ok installed", "")):
            self.assertEqual(packages.missing_nginx_packages(), [])

    def test_only_missing_certbot_components_are_requested(self):
        with mock.patch.object(packages.shutil, "which", side_effect=lambda name: "/usr/bin/" + name if name in ("nginx", "dpkg-query") else None), \
             mock.patch.object(packages.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertEqual(packages.missing_nginx_packages(), ["certbot", "python3-certbot-nginx"])

    def test_reuse_does_not_prompt_or_run_apt(self):
        with mock.patch.object(installer, "missing_nginx_packages", return_value=[]), \
             mock.patch.object(installer, "confirm") as confirm, mock.patch.object(installer, "run") as run, \
             mock.patch.object(installer, "NginxSetup") as nginx, redirect_stdout(io.StringIO()):
            self.assertTrue(installer.install_nginx_with_consent(InstallPlan(PANEL, "example.com"), False))
        confirm.assert_not_called(); run.assert_not_called()
        self.assertFalse(nginx.return_value.install_and_configure.call_args.kwargs["install_packages"])

    def test_enter_accepts_missing_packages_and_does_not_reinstall_nginx(self):
        with mock.patch.object(installer, "missing_nginx_packages", return_value=["certbot", "python3-certbot-nginx"]), \
             mock.patch.object(installer, "privileged_commands", side_effect=lambda commands: commands), \
             mock.patch("builtins.input", return_value="") as input_, mock.patch.object(installer, "run") as run, \
             mock.patch.object(installer, "NginxSetup"), redirect_stdout(io.StringIO()):
            self.assertTrue(installer.install_nginx_with_consent(InstallPlan(PANEL, "example.com"), False))
        self.assertIn("[Y/n]", input_.call_args.args[0])
        self.assertNotIn("nginx", run.call_args_list[-1].args[0])

    def test_decline_is_a_clean_cancellation_without_nginx_changes(self):
        with mock.patch.object(installer, "missing_nginx_packages", return_value=["certbot"]), \
             mock.patch.object(installer, "privileged_commands", side_effect=lambda commands: commands), \
             mock.patch("builtins.input", return_value="n"), mock.patch.object(installer, "run") as run, \
             mock.patch.object(installer, "NginxSetup") as nginx, redirect_stdout(io.StringIO()):
            self.assertIsNone(installer.install_nginx_with_consent(InstallPlan(PANEL, "example.com"), False))
        run.assert_not_called(); nginx.assert_not_called()


if __name__ == "__main__": unittest.main()
