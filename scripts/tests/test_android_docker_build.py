"""Exercise staging and artifact handling without downloading Android toolchains."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "android_docker_build", ROOT / "Android-docker-build/build.py"
)
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def write(root, name, contents="fixture"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def package(path, abis=BUILD.ABIS):
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "base/lib" if path.suffix == ".aab" else "lib"
    with zipfile.ZipFile(path, "w") as archive:
        for abi in abis:
            archive.writestr(f"{prefix}/{abi}/libmavivpn.so", b"native fixture")


class AndroidDockerBuildTests(unittest.TestCase):
    def test_stage_preserves_workspace_and_excludes_host_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            workspace = Path(temporary) / "workspace"
            write(source, "Cargo.toml", '[workspace]\nmembers = ["android/app/src/main/rust", "shared"]\n')
            included = [
                "Cargo.lock", "README.md", ".cargo/config.toml",
                "android/gradle/wrapper/gradle-wrapper.jar",
                "android/app/src/main/rust/Cargo.toml",
                "android/app/src/main/rust/src/lib.rs",
                "android/app/src/main/AndroidManifest.xml",
                "shared/Cargo.toml", "shared/src/lib.rs", "shared/build.rs",
                "scripts/check_fork_sources.py",
            ]
            excluded = [
                "android/local.properties", "android/.gradle/cache",
                "android/app/build/stale.apk", "android/app/release/signing.jks",
                "android/app/src/main/jniLibs/arm64-v8a/libmavivpn.so",
                "android/app/src/main/rust/target/old.so",
                "android/.env.production", ".cargo/credentials.toml",
                "shared/src/private.key",
            ]
            for name in included + excluded:
                write(source, name)
            wrapper = write(source, "android/gradlew")
            wrapper.write_bytes(b"#!/bin/sh\r\necho wrapper\r\n")

            BUILD.stage_sources(source, workspace)

            for name in included:
                self.assertTrue((workspace / name).is_file(), name)
            for name in excluded:
                self.assertFalse((workspace / name).exists(), name)
            self.assertNotIn(b"\r", (workspace / "android/gradlew").read_bytes())
            self.assertIn(b"\r", wrapper.read_bytes())

    def test_exports_verified_apk_and_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            build, output = Path(temporary) / "build", Path(temporary) / "output"
            for name in ("apk/debug/app-debug.apk", "bundle/release/app-release.aab"):
                package(build / "outputs" / name)
            BUILD.export_results(build, output, success=True)
            self.assertTrue((output / "apk/debug/app-debug.apk").is_file())
            self.assertTrue((output / "bundle/release/app-release.aab").is_file())

    def test_missing_abi_rejects_all_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            build, output = Path(temporary) / "build", Path(temporary) / "output"
            package(build / "outputs/apk/debug/app-debug.apk")
            package(build / "outputs/bundle/release/app-release.aab", ("arm64-v8a",))
            with self.assertRaisesRegex(RuntimeError, "missing libmavivpn.so.*armeabi-v7a"):
                BUILD.export_results(build, output, success=True)
            self.assertFalse(output.exists())

    def test_failed_build_exports_diagnostics_without_apk(self):
        with tempfile.TemporaryDirectory() as temporary:
            build, output = Path(temporary) / "build", Path(temporary) / "output"
            package(build / "outputs/apk/debug/app-debug.apk")
            write(build, "reports/tests/testDebugUnitTest/index.html", "test failed")
            write(build, "test-results/testDebugUnitTest/TEST-failure.xml", "failure")
            BUILD.export_results(build, output, success=False)
            self.assertFalse((output / "apk").exists())
            self.assertTrue((output / "reports/tests/testDebugUnitTest/index.html").is_file())
            self.assertTrue((output / "test-results/testDebugUnitTest/TEST-failure.xml").is_file())

    def test_corrupt_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = write(Path(temporary), "broken.apk", "not a zip")
            with self.assertRaises(zipfile.BadZipFile):
                BUILD.verify_native_libraries(archive)


if __name__ == "__main__":
    unittest.main()
