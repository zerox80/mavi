"""The Android build keeps its caches in host directories when asked to."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = ROOT / "Android-docker-build"
CACHE_TARGETS = {
    "/cache/gradle": "gradle",
    "/opt/cargo/registry": "cargo-registry",
    "/opt/cargo/git": "cargo-git",
    "/cache/target": "rust-target",
}
FAKE_DOCKER = """#!/bin/sh
case "$*" in
    "compose version") exit 0 ;;
    info*) echo linux; exit 0 ;;
esac
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
"""


def has_docker_compose():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0


@unittest.skipUnless(os.name != "nt" and shutil.which("bash"), "requires bash")
class BuildScriptCacheDirectoryTests(unittest.TestCase):
    def run_build(self, cache_dir=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_dir = root / "Android-docker-build"
            shutil.copytree(BUILD_DIR, build_dir, ignore=shutil.ignore_patterns("output"))
            fake = root / "bin/docker"
            fake.parent.mkdir()
            fake.write_text(FAKE_DOCKER)
            fake.chmod(0o755)
            log = root / "docker.log"
            env = {**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}",
                   "FAKE_DOCKER_LOG": str(log), "MAVI_ANDROID_SKIP_IMAGE_BUILD": "1"}
            env.pop("MAVI_ANDROID_CACHE_DIR", None)
            if cache_dir is not None:
                env["MAVI_ANDROID_CACHE_DIR"] = cache_dir
            result = subprocess.run(["bash", str(build_dir / "build.sh"), "assembleDebug"],
                                    env=env, capture_output=True, text=True)
            calls = log.read_text().splitlines() if log.exists() else []
            return result, calls, build_dir

    def test_default_build_uses_docker_volumes_only(self):
        result, calls, build_dir = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [
            f"compose --file {build_dir}/compose.yaml run --rm --no-deps -T android assembleDebug"])

    def test_cache_directory_adds_the_host_cache_override(self):
        result, calls, build_dir = self.run_build("/var/cache/mavi-android")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [
            f"compose --file {build_dir}/compose.yaml --file {build_dir}/compose.host-cache.yaml"
            " run --rm --no-deps -T android assembleDebug"])

    def test_relative_cache_directory_is_rejected_before_docker_runs(self):
        result, calls, _ = self.run_build("cache")
        self.assertEqual(result.returncode, 1)
        self.assertIn("MAVI_ANDROID_CACHE_DIR", result.stderr)
        self.assertEqual(calls, [])


@unittest.skipUnless(has_docker_compose(), "requires the Docker Compose plugin")
class HostCacheOverrideTests(unittest.TestCase):
    def mounts(self, *files, cache_dir=None):
        command = ["docker", "compose"]
        for name in files:
            command += ["--file", str(BUILD_DIR / name)]
        env = {**os.environ}
        env.pop("MAVI_ANDROID_CACHE_DIR", None)
        if cache_dir is not None:
            env["MAVI_ANDROID_CACHE_DIR"] = cache_dir
        result = subprocess.run(command + ["config", "--format", "json"], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        volumes = json.loads(result.stdout)["services"]["android"]["volumes"]
        return {volume["target"]: (volume["type"], volume.get("source")) for volume in volumes}

    def test_every_cache_volume_moves_to_the_host_directory(self):
        base = self.mounts("compose.yaml")
        named = {target for target, (kind, _) in base.items() if kind == "volume"}
        # A cache volume added to compose.yaml must be added to the override too.
        self.assertEqual(named, set(CACHE_TARGETS) | {"/root/.android"})

        merged = self.mounts("compose.yaml", "compose.host-cache.yaml", cache_dir="/var/cache/mavi-android")
        for target, name in CACHE_TARGETS.items():
            self.assertEqual(merged[target], ("bind", f"/var/cache/mavi-android/{name}"))
        # The debug signing key never leaves its Docker volume.
        self.assertEqual(merged["/root/.android"][0], "volume")
        self.assertEqual(set(merged), set(base))

    def test_override_refuses_to_run_without_a_cache_directory(self):
        result = subprocess.run(
            ["docker", "compose", "--file", str(BUILD_DIR / "compose.yaml"),
             "--file", str(BUILD_DIR / "compose.host-cache.yaml"), "config"],
            env={key: value for key, value in os.environ.items() if key != "MAVI_ANDROID_CACHE_DIR"},
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MAVI_ANDROID_CACHE_DIR", result.stderr)


if __name__ == "__main__":
    unittest.main()
