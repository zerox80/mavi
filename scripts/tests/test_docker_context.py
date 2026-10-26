"""Exercise Docker/Podman's real context filtering with harmless sentinel files."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DockerContextTests(unittest.TestCase):
    def test_build_contexts_exclude_secrets_and_preserve_sources(self):
        engine = shutil.which("docker") or shutil.which("podman")
        if not engine:
            self.skipTest("Docker or Podman is required to check context filtering")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = [engine]
            if Path(engine).name == "podman":
                command += ["--root", str(root / "storage"), "--runroot", str(root / "run"), "--storage-driver", "vfs"]
            ready = subprocess.run(command + ["info"], capture_output=True, text=True)
            if ready.returncode:
                self.skipTest("Container engine unavailable: " + ready.stderr.strip())
            contexts = [
                ("root", ROOT / ".dockerignore"),
                ("backend", ROOT / "backend/.dockerignore"),
                ("server", ROOT / "docker/server/Dockerfile.dockerignore"),
            ]
            for context, ignore in contexts:
                with self.subTest(context=context):
                    source = root / context
                    source.mkdir()
                    shutil.copyfile(ignore, source / ".dockerignore")
                    excluded = [".env", ".env.production", "auth_token.txt", "mavi-vpn.json", ".git/config", "data/private.pem", "letsencrypt/account.key", "backend/data/tls.key", "backend/letsencrypt/privkey.pem", "target/debug/cache", "node_modules/cache", "src/secret.key", "backend/src/secret.key"]
                    excluded += ["docker/server/data/private.pem", "docker/server/.env", "Android-docker-build/output/apk/debug/app-debug.apk"]
                    included = ["Cargo.toml", "backend/Cargo.toml", "shared/src/lib.rs", "backend/src/main.rs", "backend/entrypoint.sh"] if context != "backend" else ["Cargo.toml", "src/main.rs", "entrypoint.sh"]
                    if context == "server":
                        included.append("LICENSE")
                    for name in excluded + included:
                        path = source / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("harmless sentinel\n")
                    recipe = root / "Containerfile"
                    if context == "server":
                        recipe = source / "docker/server/Dockerfile"
                        recipe.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(ignore, recipe.with_name("Dockerfile.dockerignore"))
                        # Prove Docker uses the recipe-specific ignore rules.
                        (source / ".dockerignore").write_text("**\n")
                    recipe.write_text("FROM scratch\nCOPY . /context/\n")
                    output = root / ("out-" + context)
                    result = subprocess.run(command + ["build", "--network=none", "--output", f"type=local,dest={output}", "-f", str(recipe), str(source)], capture_output=True, text=True, env={**os.environ, "DOCKER_BUILDKIT": "1"})
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    for name in excluded:
                        self.assertFalse((output / "context" / name).exists(), name)
                    for name in included:
                        self.assertTrue((output / "context" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
