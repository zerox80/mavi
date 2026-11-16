"""Guard the single-source toolchain versions against silent drift."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("mavi_toolchain_versions", ROOT / "scripts/check_toolchain_versions.py")
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def metadata(*packages, members=("app 1.0.0",)):
    """Build the slice of `cargo metadata` output the checker reads."""
    rows = [{"id": member, "name": member.split()[0], "version": "1.0.0", "rust_version": "1.95"} for member in members]
    for name, version, rust_version in packages:
        rows.append({"id": f"{name} {version}", "name": name, "version": version, "rust_version": rust_version})
    return {"workspace_members": list(members), "packages": rows}


class RustFloorTests(unittest.TestCase):
    def errors_for(self, msrv, data):
        errors = []
        checker.check_msrv(msrv, data, errors)
        return errors

    def test_accepts_dependencies_at_or_below_the_floor(self):
        data = metadata(("a", "1.0.0", "1.95"), ("b", "2.0.0", "1.95.0"), ("c", "3.0.0", "1.66.0"))
        self.assertEqual(self.errors_for("1.95", data), [])

    def test_rejects_dependency_above_the_floor_and_names_it(self):
        data = metadata(("a", "1.0.0", "1.96"), ("b", "2.0.0", "1.95"))
        (error,) = self.errors_for("1.95", data)
        self.assertIn("a 1.0.0 (needs 1.96)", error)
        self.assertNotIn("b 2.0.0", error)
        self.assertIn("Raise workspace.package.rust-version to 1.96", error)

    def test_patch_level_requirement_counts(self):
        (error,) = self.errors_for("1.95", metadata(("a", "1.0.0", "1.95.1")))
        self.assertIn("to 1.95.1", error)

    def test_reports_the_highest_requirement_first(self):
        data = metadata(("low", "1.0.0", "1.96"), ("high", "1.0.0", "1.98"))
        (error,) = self.errors_for("1.95", data)
        self.assertIn("to 1.98", error)

    def test_workspace_members_do_not_raise_their_own_floor(self):
        data = metadata(members=("app 1.0.0", "lib 1.0.0"))
        self.assertEqual(self.errors_for("1.90", data), [])
        self.assertEqual(checker.dependency_floor(data), [])

    def test_packages_without_rust_version_are_ignored(self):
        data = metadata()
        data["packages"].append({"id": "x 1.0.0", "name": "x", "version": "1.0.0", "rust_version": None})
        self.assertEqual(self.errors_for("1.95", data), [])

    def test_rejects_malformed_versions(self):
        for text in ("1", "stable", "1.95-beta", ""):
            with self.subTest(text=text), self.assertRaises(ValueError):
                checker.parse_version(text)


class DocMentionTests(unittest.TestCase):
    def errors_for(self, texts, patterns, expected, label):
        errors = []
        checker.check_doc_mentions(texts, patterns, expected, label, errors)
        return errors

    def test_rust_mentions_match_the_workspace_version(self):
        texts = {
            "README.md": "badge Rust-1.95+-orange and Source builds require Rust 1.95 or newer.",
            "AGENTS.md": "Use Rust 1.95+; run cargo",
        }
        self.assertEqual(self.errors_for(texts, checker.RUST_DOC_PATTERNS, "1.95", "Rust"), [])

    def test_flags_a_stale_mention_in_one_file(self):
        texts = {
            "README.md": "badge Rust-1.96+-orange and Source builds require Rust 1.96 or newer.",
            "AGENTS.md": "Use Rust 1.95+; run cargo",
        }
        errors = self.errors_for(texts, checker.RUST_DOC_PATTERNS, "1.96", "Rust")
        self.assertEqual(len(errors), 1)
        self.assertIn("AGENTS.md: states Rust 1.95", errors[0])

    def test_flags_a_mention_that_disappeared(self):
        texts = {"README.md": "no versions here", "AGENTS.md": "Use Rust 1.95+"}
        errors = self.errors_for(texts, checker.RUST_DOC_PATTERNS, "1.95", "Rust")
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("README.md: no mention" in error for error in errors))

    def test_node_major_is_compared_with_the_docs(self):
        texts = {"AGENTS.md": "GUI CI uses Node.js 22 here"}
        (error,) = self.errors_for(texts, checker.NODE_DOC_PATTERNS, "24", "Node.js")
        self.assertIn("states Node.js 22", error)


class NodeVersionFileTests(unittest.TestCase):
    def test_extracts_the_major_version(self):
        for text, major in (("24\n", "24"), ("24.15.0", "24"), (" 22.1 ", "22")):
            with self.subTest(text=text):
                errors = []
                self.assertEqual(checker.check_node_file(text, errors), major)
                self.assertEqual(errors, [])

    def test_rejects_aliases_and_ranges(self):
        for text in ("lts/*", "v24", ">=24", "", "24.x"):
            with self.subTest(text=text):
                errors = []
                self.assertEqual(checker.check_node_file(text, errors), "")
                self.assertEqual(len(errors), 1)


class RepositoryStateTests(unittest.TestCase):
    """The checked-in files must agree, without needing cargo or the network."""

    def test_docs_and_node_file_match_their_sources(self):
        errors = []
        docs = checker.read_docs(set(checker.RUST_DOC_PATTERNS) | set(checker.NODE_DOC_PATTERNS))
        msrv = checker.tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))["workspace"]["package"]["rust-version"]
        major, minor, _ = checker.parse_version(msrv)
        checker.check_doc_mentions(docs, checker.RUST_DOC_PATTERNS, f"{major}.{minor}", "Rust", errors)
        node_major = checker.check_node_file((ROOT / ".node-version").read_text(encoding="utf-8"), errors)
        checker.check_doc_mentions(docs, checker.NODE_DOC_PATTERNS, node_major, "Node.js", errors)
        self.assertEqual(errors, [])

    def test_workflows_read_node_from_the_single_source(self):
        for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                self.assertNotIn("node-version:", text)
                if "actions/setup-node" in text:
                    self.assertIn("node-version-file: .node-version", text)


if __name__ == "__main__":
    unittest.main()
