"""Run the rolling lockfile commit step against a disposable local Git remote."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
NAME = "zerox80"
EMAIL = "115537871+zerox80@users.noreply.github.com"


class ForkUpdateWorkflowTests(unittest.TestCase):
    def test_new_and_amended_commits_use_public_identity_and_guarded_push(self):
        workflow = (ROOT / ".github/workflows/update-forks.yml").read_text()
        step = workflow.split("      - name: Commit and push refreshed lockfile\n", 1)[1]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        env = {**os.environ,
               "GIT_AUTHOR_NAME": NAME, "GIT_AUTHOR_EMAIL": EMAIL,
               "GIT_COMMITTER_NAME": NAME, "GIT_COMMITTER_EMAIL": EMAIL,
               "FORK_UPDATE_COMMIT_MESSAGE": "test rolling fork refresh"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / "remote.git"
            repo = root / "checkout"
            repo.mkdir()

            def git(*args, cwd=repo):
                return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                                      capture_output=True, text=True).stdout.strip()

            git("init", "--bare", str(remote))
            git("init", "-b", "main")
            git("config", "commit.gpgsign", "false")
            (repo / "Cargo.lock").write_text("initial\n")
            git("add", "Cargo.lock")
            git("commit", "-m", "initial")
            initial = git("rev-parse", "HEAD")
            git("remote", "add", "origin", str(remote))
            git("push", "origin", "main")

            for contents in ["first refresh\n", "second refresh\n"]:
                (repo / "Cargo.lock").write_text(contents)
                result = subprocess.run(["bash", "-e", "-c", script], cwd=repo, env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(git("log", "-1", "--format=%an|%ae|%cn|%ce"),
                                 f"{NAME}|{EMAIL}|{NAME}|{EMAIL}")
                self.assertEqual(git("rev-list", "--count", "main"), "2")
                self.assertEqual(git("rev-parse", "HEAD"),
                                 git("rev-parse", "main", cwd=remote))
                self.assertEqual(git("show", "main:Cargo.lock", cwd=remote), contents.strip())

            # Returning to the original tree removes only the rolling tip.
            (repo / "Cargo.lock").write_text("initial\n")
            result = subprocess.run(["bash", "-e", "-c", script], cwd=repo, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(git("rev-parse", "main", cwd=remote), initial)


if __name__ == "__main__":
    unittest.main()
