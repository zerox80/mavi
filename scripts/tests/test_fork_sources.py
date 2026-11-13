"""Guard the direct transport dependencies against accidental version pins."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("mavi_fork_sources", ROOT / "scripts/check_fork_sources.py")
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


class ForkSourceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = checker.load_toml(ROOT / "Cargo.toml")

    def errors_for(self, manifest):
        errors = []
        with patch.object(checker, "load_toml", return_value=manifest):
            checker.validate_manifest(errors)
        return errors

    def test_transport_dependencies_track_git_branches(self):
        self.assertEqual(self.errors_for(self.manifest), [])

    def test_rejects_quinn_version_constraints(self):
        for version in ("=0.12.0", "0.12", "*"):
            with self.subTest(version=version):
                manifest = copy.deepcopy(self.manifest)
                manifest["workspace"]["dependencies"]["quinn"]["version"] = version
                self.assertIn("Workspace quinn must never set version", self.errors_for(manifest))

    def test_rejects_direct_revision_pins(self):
        for field in ("rev", "tag"):
            with self.subTest(field=field):
                manifest = copy.deepcopy(self.manifest)
                manifest["workspace"]["dependencies"]["quinn"][field] = "fixed"
                self.assertIn(f"Workspace quinn must never set {field}", self.errors_for(manifest))

    def test_h3_tracks_master(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["workspace"]["dependencies"]["h3"]["branch"] = "main"
        self.assertTrue(any("Workspace h3 must track" in error for error in self.errors_for(manifest)))


if __name__ == "__main__":
    unittest.main()
