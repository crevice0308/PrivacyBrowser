"""Offline Windows installer packaging checks; these do not run Windows.

The PE fixtures are deliberately inert.  The optional artifact checks inspect the
actual NSIS executable with 7-Zip when PRIVACY_BROWSER_WINDOWS_SETUP is set.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_VERSION = json.loads((ROOT / "config/esr.json").read_text())["upstream_version"].removesuffix("esr")
sys.path.insert(0, str(ROOT / "tools"))
import build_dev
import package_windows


def pe_fixture(machine: int = 0x8664) -> bytes:
    """A parseable architecture header, not an executable Windows program."""
    result = bytearray(1024)
    result[:2] = b"MZ"
    struct.pack_into("<I", result, 0x3C, 0x80)
    result[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", result, 0x84, machine, 1, 0, 0, 0, 240, 0x22)
    struct.pack_into("<H", result, 0x98, 0x20B if machine == 0x8664 else 0x10B)
    return bytes(result)


def make_runtime(root: Path) -> Path:
    upstream = root / "upstream"
    upstream.mkdir()
    for name in ("firefox.exe", "xul.dll", "mozglue.dll", "nss3.dll"):
        (upstream / name).write_bytes(pe_fixture())
    (upstream / "application.ini").write_text(
        f"[App]\nName=Firefox\nVersion={UPSTREAM_VERSION}\n"
        "SourceRepository=https://hg.mozilla.org/releases/mozilla-esr153\n"
    )
    (upstream / "platform.ini").write_text(f"[Build]\nMilestone={UPSTREAM_VERSION}\n")
    (upstream / "omni.ja").write_bytes(b"fixture runtime archive")
    (upstream / "browser").mkdir()
    (upstream / "browser/omni.ja").write_bytes(b"fixture browser archive")
    (upstream / "dependentlibs.list").write_text("mozglue.dll\nxul.dll\n")
    result = root / "runtime"
    build_dev.build(upstream, ROOT / "browser/privacy", result)
    return result


class WindowsRuntimeValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = make_runtime(self.root)

    def test_valid_developer_runtime_is_accepted(self):
        result = package_windows.validate_runtime(self.runtime)
        self.assertIsInstance(result, dict)
        self.assertFalse(json.loads((self.runtime / "PRIVACY_BROWSER_BUILD.json").read_text())["official_release"])

    def test_32bit_native_binary_cannot_be_packaged_as_win64(self):
        binary = self.runtime / "firefox.exe"
        binary.write_bytes(pe_fixture(0x14C))
        manifest_file = self.runtime / "PRIVACY_BROWSER_BUILD.json"
        manifest = json.loads(manifest_file.read_text())
        manifest["native_binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        manifest_file.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            package_windows.validate_runtime(self.runtime)

    def test_incomplete_runtimes_are_rejected(self):
        for relative in ("firefox.exe", "application.ini", "omni.ja", "privacybrowser.cfg", "PRIVACY_BROWSER_BUILD.json"):
            with self.subTest(relative=relative):
                path = self.runtime / relative
                original = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises(ValueError):
                        package_windows.validate_runtime(self.runtime)
                finally:
                    path.write_bytes(original)

    def test_missing_or_truncated_pe_header_is_rejected(self):
        binary = self.runtime / "firefox.exe"
        original = binary.read_bytes()
        manifest_file = self.runtime / "PRIVACY_BROWSER_BUILD.json"
        manifest = json.loads(manifest_file.read_text())
        for contents in (b"not a PE", b"MZ", b"MZ" + b"\0" * 64):
            with self.subTest(contents=contents[:8]):
                binary.write_bytes(contents)
                manifest["native_binary_sha256"] = hashlib.sha256(contents).hexdigest()
                manifest_file.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    package_windows.validate_runtime(self.runtime)
        binary.write_bytes(original)

    def test_changed_native_payload_cannot_reuse_staging_checksum(self):
        binary = self.runtime / "firefox.exe"
        binary.write_bytes(binary.read_bytes() + b"modified after verification")
        with self.assertRaises(ValueError):
            package_windows.validate_runtime(self.runtime)

    def test_mismatched_esr_or_incompatible_updater_is_rejected(self):
        config = self.runtime / "application.ini"
        original = config.read_text()
        config.write_text(original.replace(f"Version={UPSTREAM_VERSION}", "Version=1.0"))
        with self.assertRaises(ValueError):
            package_windows.validate_runtime(self.runtime)
        config.write_text(original)
        policy_file = self.runtime / "distribution/policies.json"
        policies = json.loads(policy_file.read_text())
        policies["policies"]["DisableAppUpdate"] = False
        policy_file.write_text(json.dumps(policies))
        with self.assertRaises(ValueError):
            package_windows.validate_runtime(self.runtime)

    @unittest.skipIf(sys.platform == "win32", "POSIX symlink fixture")
    def test_payload_cannot_follow_a_symlink_outside_runtime(self):
        outside = self.root / "unrelated-private-file"
        outside.write_text("must never ship")
        (self.runtime / "escaped.txt").symlink_to(outside)
        with self.assertRaises(ValueError):
            package_windows.write_payload_include(self.runtime, self.root / "payload.nsh")
        self.assertEqual(outside.read_text(), "must never ship")


class WindowsPayloadManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = make_runtime(self.root)

    def test_payload_uses_exact_files_and_removes_children_before_parent(self):
        (self.runtime / "extra nested/a b").mkdir(parents=True)
        (self.runtime / "extra nested/a b/file (1).txt").write_text("payload")
        include = self.root / "payload.nsh"
        result = package_windows.write_payload_include(self.runtime, include)
        self.assertIsInstance(result, dict)
        source = include.read_text()
        self.assertIn("!macro InstallPayload", source)
        self.assertIn("!macro RemovePayload", source)
        self.assertNotRegex(source, r"(?im)^\s*(?:File|RMDir)\s+/r(?:\s|$)")
        expected = {p.relative_to(self.runtime).as_posix() for p in self.runtime.rglob("*") if p.is_file()}
        self.assertEqual(len(re.findall(r"(?im)^\s*File\s+", source)), len(expected))
        self.assertEqual(len(re.findall(r"(?im)^\s*Delete\s+", source)), len(expected))
        for relative in expected:
            self.assertIn('$INSTDIR\\' + relative.replace("/", "\\"), source)
        removed = re.findall(r'(?im)^\s*RMDir\s+"\$INSTDIR\\([^"\r\n]+)"\s*$', source)
        for child in removed:
            parent = child.rpartition("\\")[0]
            if parent in removed:
                self.assertLess(removed.index(child), removed.index(parent))

    def test_nsis_metacharacters_and_control_characters_cannot_enter_payload(self):
        for name in ('dollar$.txt', 'quote".txt', 'line\nbreak.txt', 'control\ttab.txt', 'colon:name.txt'):
            with self.subTest(name=name):
                path = self.runtime / name
                path.write_text("hostile payload filename")
                try:
                    with self.assertRaises(ValueError):
                        package_windows.write_payload_include(self.runtime, self.root / "payload.nsh")
                finally:
                    path.unlink()

    def test_windows_device_names_and_case_collisions_are_rejected(self):
        for name in ("CON", "nul.txt", "COM1.log", "XUL.dll"):
            with self.subTest(name=name):
                path = self.runtime / name
                path.write_text("cannot safely materialize on Windows")
                try:
                    with self.assertRaises(ValueError):
                        package_windows.write_payload_include(self.runtime, self.root / "payload.nsh")
                finally:
                    path.unlink()

    @unittest.skipIf(sys.platform == "win32", "POSIX metacharacter path fixture")
    def test_nsis_metacharacters_in_staging_parent_are_rejected(self):
        hostile = self.root / 'parent$"nsis'
        hostile.mkdir()
        staged = hostile / "runtime"
        shutil.copytree(self.runtime, staged)
        with self.assertRaises(ValueError):
            package_windows.write_payload_include(staged, self.root / "payload.nsh")

    def test_installer_never_recursively_deletes_install_directory_or_profile(self):
        # Regression guard for the old free-choice installer which recursively
        # deleted all files in a selected directory, including unrelated files.
        source = (ROOT / "packaging/windows/privacy-browser-dev.nsi").read_text()
        self.assertNotRegex(source, r'(?im)^\s*RMDir\s+/r\b')
        self.assertNotRegex(source, r'(?im)^\s*Delete\s+[^\r\n]*(?:Profile|parent\.lock)')


@unittest.skipUnless(os.environ.get("PRIVACY_BROWSER_WINDOWS_SETUP"), "Set PRIVACY_BROWSER_WINDOWS_SETUP to inspect a compiled setup executable")
class CompiledInstallerArtifactTests(unittest.TestCase):
    def test_actual_setup_is_pe_and_contains_complete_isolated_payload(self):
        setup = Path(os.environ["PRIVACY_BROWSER_WINDOWS_SETUP"])
        data = setup.read_bytes()
        self.assertEqual(data[:2], b"MZ")
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        self.assertEqual(data[offset:offset + 4], b"PE\0\0")
        sevenzip = shutil.which("7z") or shutil.which("7zz")
        if sevenzip is None:
            self.skipTest("7-Zip is required to inspect NSIS payload")
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            result = subprocess.run([sevenzip, "x", "-y", f"-o{destination}", str(setup)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifests = list(destination.rglob("PRIVACY_BROWSER_BUILD.json"))
            self.assertEqual(len(manifests), 1)
            payload = manifests[0].parent
            manifest = json.loads(manifests[0].read_text())
            self.assertFalse(manifest["official_release"])
            self.assertEqual(hashlib.sha256((payload / manifest["native_binary"]).read_bytes()).hexdigest(), manifest["native_binary_sha256"])
            self.assertTrue((payload / "privacybrowser.cfg").is_file())
            self.assertTrue((payload / "distribution/privacybrowser/chrome.manifest").is_file())
            self.assertTrue(list(destination.rglob("*ninstall*.exe")), "NSIS uninstaller is missing")
            self.assertFalse(list(payload.rglob("parent.lock")))
            expected_runtime = os.environ.get("PRIVACY_BROWSER_WINDOWS_RUNTIME")
            if expected_runtime:
                expected_runtime = Path(expected_runtime)
                for source in expected_runtime.rglob("*"):
                    if source.is_file():
                        packaged = payload / source.relative_to(expected_runtime)
                        self.assertTrue(packaged.is_file(), str(source.relative_to(expected_runtime)))
                        self.assertEqual(hashlib.sha256(packaged.read_bytes()).digest(), hashlib.sha256(source.read_bytes()).digest(), str(source.relative_to(expected_runtime)))


if __name__ == "__main__":
    unittest.main()
