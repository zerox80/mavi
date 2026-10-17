"""GUI removal must preserve networking owned by a retained VPN service."""

from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("gui_uninstall_lifecycle", ROOT / "uninstall_gui_windows.py")
UNINSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UNINSTALL)
SERVICE_QUERY = ["sc", "query", "MaviVPNService"]


class GuiUninstallLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.install_dir = self.root / "programfiles" / "MaviVPN"
        self.install_dir.mkdir(parents=True)
        self.gui_exe = self.install_dir / "mavi-vpn-gui.exe"
        self.gui_exe.touch()
        self.cli_script = self.root / "uninstall_cli_windows.py"
        self.cli_script.touch()
        self.nsis_exe = str(self.root / "uninstall.exe")

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(redirect_stdout(io.StringIO()))
        stack.enter_context(patch.object(sys, "platform", "win32"))
        stack.enter_context(patch.dict(os.environ, {
            "ProgramFiles": str(self.root / "programfiles"),
            "LOCALAPPDATA": str(self.root / "local"),
        }))
        stack.enter_context(patch.object(Path, "home", return_value=self.root))
        stack.enter_context(patch.object(UNINSTALL, "ROOT", self.root))
        stack.enter_context(patch("builtins.input", return_value=""))
        self.ask = stack.enter_context(patch.object(UNINSTALL, "ask", return_value=False))
        self.find_nsis = stack.enter_context(patch.object(UNINSTALL, "find_nsis_uninstaller", return_value=None))
        self.is_admin = stack.enter_context(patch.object(UNINSTALL, "is_admin", return_value=True))
        self.elevate = stack.enter_context(patch.object(UNINSTALL, "run_elevated", return_value=False))
        self.remove_path = stack.enter_context(patch.object(UNINSTALL, "remove_from_path"))
        # Never execute an OS command; only the service-presence query is needed
        # while retaining the service. Any extra command fails assertions below.
        self.run = stack.enter_context(patch.object(
            UNINSTALL.subprocess, "run",
            return_value=subprocess.CompletedProcess(SERVICE_QUERY, 0, stdout="RUNNING"),
        ))

    def assert_only_service_query(self):
        self.assertEqual([entry.args[0] for entry in self.run.call_args_list], [SERVICE_QUERY])

    def test_declining_all_actions_preserves_active_service(self):
        UNINSTALL.main()
        self.assertTrue(self.gui_exe.exists())
        self.remove_path.assert_not_called()
        self.assert_only_service_query()

    def test_gui_only_removal_preserves_active_service(self):
        shortcut = self.root / "Desktop" / "Mavi VPN.lnk"
        shortcut.parent.mkdir()
        shortcut.touch()
        self.ask.side_effect = lambda question, **_: question != "Also uninstall CLI and Windows Service?"

        UNINSTALL.main()

        self.assertFalse(self.gui_exe.exists())
        self.assertFalse(shortcut.exists())
        self.remove_path.assert_called_once_with(self.install_dir)
        self.assert_only_service_query()

    def test_interrupting_first_prompt_never_touches_network(self):
        self.ask.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            UNINSTALL.main()
        self.run.assert_not_called()
        self.assertTrue(self.gui_exe.exists())

    def test_declining_nsis_and_manual_removal_preserves_active_service(self):
        self.find_nsis.return_value = self.nsis_exe
        UNINSTALL.main()
        self.assertTrue(self.gui_exe.exists())
        self.elevate.assert_not_called()
        self.assert_only_service_query()

    def test_native_nsis_handles_service_lifecycle_and_cancellation(self):
        self.find_nsis.return_value = self.nsis_exe
        self.ask.side_effect = lambda question, **_: question.startswith("Run NSIS")
        for returncode in (0, 1, 2):
            with self.subTest(returncode=returncode):
                self.run.reset_mock()
                self.run.side_effect = lambda command, **_: subprocess.CompletedProcess(
                    command, returncode if command == [self.nsis_exe] else 0,
                )
                UNINSTALL.main()
                self.assertEqual(
                    [entry.args[0] for entry in self.run.call_args_list],
                    [[self.nsis_exe], SERVICE_QUERY],
                )
        self.assertTrue(self.gui_exe.exists())

    def test_nsis_uac_launch_or_cancellation_never_cleans_network(self):
        self.find_nsis.return_value = self.nsis_exe
        self.ask.side_effect = lambda question, **_: question.startswith("Run NSIS")
        self.is_admin.return_value = False
        for launched in (False, True):
            with self.subTest(launched=launched):
                self.run.reset_mock()
                self.elevate.reset_mock()
                self.elevate.return_value = launched
                UNINSTALL.main()
                self.elevate.assert_called_once_with(self.nsis_exe)
                self.assert_only_service_query()

    def test_explicit_service_removal_still_delegates_to_cli_uninstaller(self):
        self.ask.side_effect = lambda question, **_: question == "Also uninstall CLI and Windows Service?"
        UNINSTALL.main()
        self.assertEqual(self.run.call_args_list, [
            call(SERVICE_QUERY, capture_output=True),
            call([sys.executable, str(self.cli_script)]),
        ])
        self.assertTrue(self.gui_exe.exists())

    def test_missing_service_or_cli_uninstaller_skips_delegation(self):
        for cli_exists, query_status in ((True, 1060), (False, 0)):
            with self.subTest(cli_exists=cli_exists, query_status=query_status):
                if not cli_exists:
                    self.cli_script.unlink()
                self.run.reset_mock()
                self.ask.reset_mock()
                self.run.return_value = subprocess.CompletedProcess(SERVICE_QUERY, query_status)
                UNINSTALL.main()
                self.assert_only_service_query()
                self.assertNotIn(
                    call("Also uninstall CLI and Windows Service?", default="n"),
                    self.ask.call_args_list,
                )


if __name__ == "__main__":
    unittest.main()
