"""Offline native Windows identity tests using inert, resource-only PE fixtures.

Fixtures contain no executable code. Passing these unit tests does not validate a
real browser binary, source build, Windows execution, installation, or signing.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import verify_windows_native as native

UPSTREAM = json.loads((ROOT / "config/esr.json").read_text())["upstream_version"]
ENGINE = UPSTREAM.removesuffix("esr")
ICON = (ROOT / "browser/privacy/assets/privacybrowser.ico").read_bytes()
STRINGS = {"ProductName": "PrivacyBrowser", "FileDescription": "PrivacyBrowser",
           "InternalName": "PrivacyBrowser", "OriginalFilename": "privacybrowser.exe",
           "CompanyName": "PrivacyBrowser", "FileVersion": ENGINE, "ProductVersion": ENGINE,
           "LegalCopyright": "Mozilla contributors; legal notices retained"}


def pad(data: bytes, alignment: int = 4) -> bytes:
    return data + b"\0" * (-len(data) % alignment)


def node(key: str, value: bytes = b"", kind: int = 1, children: tuple = ()) -> bytes:
    result = pad(b"\0" * 6 + key.encode("utf-16le") + b"\0\0") + value
    for child in children:
        result = pad(result) + child
    return struct.pack("<HHH", len(result), len(value) // 2 if kind == 1 else len(value), kind) + result[6:]


def version_fixture(strings: dict | None = None, extra_table: dict | None = None, engine: str = ENGINE) -> bytes:
    version = [int(part) for part in engine.split(".")]
    version += [0] * (4 - len(version))
    ms, ls = (version[0] << 16) | version[1], (version[2] << 16) | version[3]
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x10000, ms, ls, ms, ls, 0x3F, 0, 0x40004, 1, 0, 0, 0)
    tables = []
    for language, fields in (("040904B0", STRINGS if strings is None else strings), ("041104B0", extra_table)):
        if fields is not None:
            values = tuple(node(key, (value + "\0").encode("utf-16le")) for key, value in fields.items())
            tables.append(node(language, children=values))
    string_info = node("StringFileInfo", children=tuple(tables))
    var_info = node("VarFileInfo", children=(node("Translation", struct.pack("<HH", 0x409, 1200), 0),))
    return node("VS_VERSION_INFO", fixed, 0, (string_info, var_info))


def resources_fixture(strings: dict | None = None, extra_table: dict | None = None, engine: str = ENGINE) -> dict:
    count = struct.unpack_from("<H", ICON, 4)[0]
    resources = {(16, 1, 1033): version_fixture(strings, extra_table, engine)}
    group = bytearray(struct.pack("<HHH", 0, 1, count))
    for index in range(count):
        width, height, colors, reserved, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", ICON, 6 + index * 16)
        group += struct.pack("<BBBBHHIH", width, height, colors, reserved, planes, bits, size, index + 1)
        resources[(3, index + 1, 1033)] = ICON[offset:offset + size]
    resources[(14, 1, 1033)] = bytes(group)
    return resources


def pe_fixture(resources: dict | None = None, machine: int = 0x8664) -> bytes:
    """PE headers and a resource section only; no code or entry point exists."""
    resources = resources_fixture() if resources is None else resources
    tree = {}
    for (kind, name, language), value in sorted(resources.items()):
        tree.setdefault(kind, {}).setdefault(name, {})[language] = value
    output = bytearray()
    leaves = []

    def directory(branch):
        offset = len(output)
        output.extend(struct.pack("<IIHHHH", 0, 0, 0, 0, 0, len(branch)) + b"\0" * (8 * len(branch)))
        for index, (key, child) in enumerate(sorted(branch.items())):
            if isinstance(child, dict):
                target = directory(child) | 0x80000000
            else:
                target = len(output)
                output.extend(b"\0" * 16)
                leaves.append((target, child))
            struct.pack_into("<II", output, offset + 16 + index * 8, key, target)
        return offset

    directory(tree)
    for offset, payload in leaves:
        output.extend(b"\0" * (-len(output) % 4))
        address = 0x1000 + len(output)
        struct.pack_into("<IIII", output, offset, address, len(payload), 0, 0)
        output.extend(payload)
    resource_size = len(output)
    raw = pad(bytes(output), 512)
    header = bytearray(512)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x80)
    header[0x80:0x84] = b"PE\0\0"
    optional_size = 240 if machine == 0x8664 else 224
    directory_offset = 112 if machine == 0x8664 else 96
    struct.pack_into("<HHIIIHH", header, 0x84, machine, 1, 0, 0, 0, optional_size, 0x22)
    optional = 0x98
    struct.pack_into("<H", header, optional, 0x20B if machine == 0x8664 else 0x10B)
    struct.pack_into("<I", header, optional + directory_offset - 4, 16)
    struct.pack_into("<II", header, optional + directory_offset + 16, 0x1000, resource_size)
    section = optional + optional_size
    header[section:section + 8] = b".rsrc\0\0\0"
    struct.pack_into("<IIII", header, section + 8, resource_size, 0x1000, len(raw), 512)
    return bytes(header) + raw


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def make_candidate(root: Path) -> tuple[Path, Path]:
    runtime = root / "runtime"
    runtime.mkdir()
    for name in native.REQUIRED:
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"inert unit-test file, not a browser")
    (runtime / native.BINARY).write_bytes(pe_fixture())
    (runtime / "application.ini").write_text("[App]\nName=PrivacyBrowser\nVendor=PrivacyBrowser\n"
                                             f"Version={ENGINE}\nID={native.APP_ID}\nRemotingName=privacybrowser\n")
    (runtime / "platform.ini").write_text(f"[Build]\nMilestone={ENGINE}\n")
    (runtime / "distribution/privacybrowser/assets/privacybrowser.ico").write_bytes(ICON)
    (runtime / "privacybrowser.cfg").write_bytes((ROOT / "browser/privacy/bootstrap.cfg").read_bytes())
    (runtime / "distribution/privacybrowser/chrome.manifest").write_bytes((ROOT / "browser/privacy/chrome.manifest").read_bytes())
    (runtime / "defaults/pref/privacybrowser-autoconfig.js").write_bytes(native.AUTOCONFIG.encode("utf-8"))
    evidence_root = root / "evidence"
    evidence_root.mkdir()
    preparation = {"prepared": True, "compiled": False, "target": "windows", "native_binary": native.BINARY,
                   "app_name": "PrivacyBrowser", "upstream_version": UPSTREAM,
                   "icon_sha256": hashlib.sha256(ICON).hexdigest()}
    write_json(evidence_root / "preparation.json", preparation)
    trust = json.loads((ROOT / "config/update-trust.json").read_text())
    source = {"kind": "source", "signature_verified": True, "upstream_version": UPSTREAM,
              "archive": f"firefox-{UPSTREAM}.source.tar.xz", "sha512": "0" * 128,
              "checksum_signer": trust["mozilla_release_key_fingerprints"][0]}
    write_json(evidence_root / "source-verification.json", source)
    (evidence_root / "build.log").write_text("INERT UNIT FIXTURE: ./mach build command output\n")
    (evidence_root / "package.log").write_text("INERT UNIT FIXTURE: ./mach package command output\n")
    proof = {"schema_version": 1, "build_kind": "full-source-build", "source_compiled": True,
             "official_release": False, "target": "windows", "upstream_version": UPSTREAM,
             "native_binary": native.BINARY, "files": native.inventory(runtime),
             "commands": [{"argv": ["./mach", name], "exit_code": 0} for name in ("build", "package")]}
    for role, path in (("preparation", "preparation.json"), ("source_verification", "source-verification.json"),
                       ("build_log", "build.log"), ("package_log", "package.log")):
        proof[role] = {"path": path, "sha256": native.digest(evidence_root / path)}
    evidence = evidence_root / "build-evidence.json"
    write_json(evidence, proof)
    return runtime, evidence


class NativePEResourceTests(unittest.TestCase):
    def test_resource_only_fixture_is_parsed_without_execution(self):
        pe = native.PE(pe_fixture())
        version = native.verify_versions(pe, ENGINE)
        self.assertEqual(version[0]["strings"]["ProductName"], "PrivacyBrowser")
        icons = native.verify_icons(pe, ICON)
        self.assertTrue(icons["verified"])
        self.assertEqual(icons["frames_per_group"], 7)

    def test_mozilla_branded_renamed_exe_is_rejected_for_each_identity_field(self):
        for key, value in (("ProductName", "Firefox"), ("FileDescription", "Firefox"), ("InternalName", "Firefox"),
                           ("OriginalFilename", "firefox.exe"), ("CompanyName", "Mozilla Corporation")):
            with self.subTest(key=key):
                pe = native.PE(pe_fixture(resources_fixture({**STRINGS, key: value})))
                with self.assertRaises(ValueError):
                    native.verify_versions(pe, ENGINE)

    def test_branding_is_verified_in_every_language_table(self):
        pe = native.PE(pe_fixture(resources_fixture(extra_table={**STRINGS, "ProductName": "Firefox"})))
        with self.assertRaises(ValueError):
            native.verify_versions(pe, ENGINE)

    def test_missing_identity_field_or_marketing_version_is_rejected(self):
        for field in ("ProductName", "FileDescription", "InternalName", "OriginalFilename", "CompanyName", "ProductVersion", "FileVersion"):
            with self.subTest(field=field):
                fields = {key: value for key, value in STRINGS.items() if key != field}
                with self.assertRaises(ValueError):
                    native.verify_versions(native.PE(pe_fixture(resources_fixture(fields))), ENGINE)
        fields = {**STRINGS, "ProductVersion": "0.1.0"}
        with self.assertRaises(ValueError):
            native.verify_versions(native.PE(pe_fixture(resources_fixture(fields))), ENGINE)

    def test_missing_version_resource_is_rejected(self):
        resources = resources_fixture()
        del resources[(16, 1, 1033)]
        with self.assertRaises(ValueError):
            native.verify_versions(native.PE(pe_fixture(resources)), ENGINE)

    def test_duplicate_version_string_is_rejected(self):
        resources = resources_fixture()
        resources[(16, 1, 1033)] = resources[(16, 1, 1033)].replace("InternalName".encode("utf-16le"), "ProductName\0".encode("utf-16le"))
        with self.assertRaises(ValueError):
            native.verify_versions(native.PE(pe_fixture(resources)), ENGINE)

    def test_missing_icon_group_frame_and_changed_pixel_bytes_are_rejected(self):
        for mutation in ("group", "frame", "pixels", "unreferenced"):
            with self.subTest(mutation=mutation):
                resources = resources_fixture()
                if mutation == "group":
                    del resources[(14, 1, 1033)]
                elif mutation == "frame":
                    del resources[(3, 1, 1033)]
                elif mutation == "pixels":
                    frame = resources[(3, 1, 1033)]
                    resources[(3, 1, 1033)] = frame[:-1] + bytes([frame[-1] ^ 1])
                else:
                    resources[(3, 99, 1033)] = resources[(3, 1, 1033)]
                with self.assertRaises(ValueError):
                    native.verify_icons(native.PE(pe_fixture(resources)), ICON)

    def test_secondary_mozilla_icon_group_cannot_hide_behind_custom_primary(self):
        resources = resources_fixture()
        group = bytearray(resources[(14, 1, 1033)])
        struct.pack_into("<H", group, 6 + 12, 99)
        resources[(14, 2, 1033)] = bytes(group)
        resources[(3, 99, 1033)] = b"x" * len(resources[(3, 1, 1033)])
        with self.assertRaises(ValueError):
            native.verify_icons(native.PE(pe_fixture(resources)), ICON)

    def test_neutral_language_icon_frames_are_supported(self):
        resources = resources_fixture()
        for key in list(resources):
            if key[0] == 3:
                resources[(3, key[1], 0)] = resources.pop(key)
        self.assertTrue(native.verify_icons(native.PE(pe_fixture(resources)), ICON)["verified"])

    def test_truncated_and_malformed_pe_headers_are_rejected(self):
        valid = pe_fixture()
        cases = [b"", b"MZ", valid[:63], valid[:512], b"XX" + valid[2:]]
        for offset, fmt, value in ((0x3C, "<I", 0xFFFFFFF0), (0x84, "<H", 0x14C), (0x86, "<H", 100),
                                   (0x98, "<H", 0x10B), (0x98 + 108, "<I", 2), (0x98 + 128, "<I", 0xFFFFFFFF),
                                   (0x98 + 132, "<I", 0xFFFFFFFF)):
            changed = bytearray(valid)
            struct.pack_into(fmt, changed, offset, value)
            cases.append(bytes(changed))
        for index, data in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                native.PE(data)

    def test_cyclic_resource_tree_and_out_of_bounds_leaf_are_rejected(self):
        binary = bytearray(pe_fixture())
        struct.pack_into("<I", binary, 512 + 16 + 4, 0x80000000)
        with self.assertRaises(ValueError):
            native.PE(bytes(binary))
        binary = bytearray(pe_fixture())
        # Walk the first type/name/language entry to its IMAGE_RESOURCE_DATA_ENTRY.
        cursor = 0
        for _ in range(3):
            cursor = struct.unpack_from("<I", binary, 512 + cursor + 20)[0] & 0x7FFFFFFF
        struct.pack_into("<I", binary, 512 + cursor, 0xFFFFFFFF)
        with self.assertRaises(ValueError):
            native.PE(bytes(binary))

    def test_malformed_ico_and_version_lengths_are_rejected(self):
        for icon in (b"", ICON[:5], ICON[:20], ICON[:6] + b"\0" * (len(ICON) - 6)):
            with self.subTest(icon_length=len(icon)), self.assertRaises(ValueError):
                native.icon_frames(icon)
        for value in (0, 7, 65535):
            resource = bytearray(version_fixture())
            struct.pack_into("<H", resource, 0, value)
            resources = resources_fixture()
            resources[(16, 1, 1033)] = bytes(resource)
            with self.subTest(length=value), self.assertRaises(ValueError):
                native.verify_versions(native.PE(pe_fixture(resources)), ENGINE)


class NativeInstallerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.installer = Path(self.temporary.name) / native.SETUP_NAME
        self.fields = {"ProductName": "PrivacyBrowser Setup", "FileDescription": "PrivacyBrowser Installer",
                       "InternalName": "PrivacyBrowser Setup", "OriginalFilename": native.SETUP_NAME,
                       "CompanyName": "PrivacyBrowser contributors", "FileVersion": "0.1.0-dev", "ProductVersion": "0.1.0-dev"}
        self.resources = resources_fixture(self.fields, engine="0.1.0")
        self.installer.write_bytes(pe_fixture(self.resources, machine=0x14C))

    def test_i386_setup_wrapper_identity_has_separate_unexecuted_report(self):
        result = native.validate_installer(self.installer)
        self.assertTrue(result["installer_static_identity_passed"])
        self.assertEqual(result["stub_architecture"], "i386")
        self.assertFalse(result["payload_extraction_verified"])
        self.assertFalse(result["windows_execution_tested"])
        self.assertFalse(result["release_eligible"])

    def test_i386_setup_mode_cannot_weaken_default_amd64_browser_requirement(self):
        with self.assertRaises(ValueError):
            native.PE(self.installer.read_bytes())
        self.installer.write_bytes(pe_fixture(self.resources))
        with self.assertRaises(ValueError):
            native.validate_installer(self.installer)

    def test_installer_wrong_identity_version_or_icon_is_rejected(self):
        for field in self.fields:
            resources = resources_fixture({**self.fields, field: "Firefox"}, engine="0.1.0")
            self.installer.write_bytes(pe_fixture(resources, machine=0x14C))
            with self.subTest(field=field), self.assertRaises(ValueError):
                native.validate_installer(self.installer)
        resources = resources_fixture(self.fields, engine="1.0.0")
        self.installer.write_bytes(pe_fixture(resources, machine=0x14C))
        with self.assertRaises(ValueError):
            native.validate_installer(self.installer)
        resources = resources_fixture(self.fields, engine="0.1.0")
        del resources[(14, 1, 1033)]
        self.installer.write_bytes(pe_fixture(resources, machine=0x14C))
        with self.assertRaises(ValueError):
            native.validate_installer(self.installer)

    def test_nsis_overlay_is_hashed_but_never_interpreted_as_code_or_payload(self):
        image = self.installer.read_bytes()
        appended = b"INERT COMPRESSED-PAYLOAD PLACEHOLDER" * 1000
        self.installer.write_bytes(image + appended)
        self.assertEqual(native.read_pe_image(self.installer), image)
        result = native.validate_installer(self.installer)
        self.assertEqual(result["sha256"], hashlib.sha256(image + appended).hexdigest())
        self.assertEqual(result["artifact_bytes"], len(image + appended))
        self.assertFalse(result["payload_extraction_verified"])

    def test_installer_filename_is_exact(self):
        renamed = self.installer.with_name("Firefox Setup.exe")
        self.installer.rename(renamed)
        with self.assertRaises(ValueError):
            native.validate_installer(renamed)

    def test_bounded_image_reader_rejects_declared_sections_outside_file(self):
        data = bytearray(self.installer.read_bytes())
        section = 0x98 + 224
        struct.pack_into("<I", data, section + 16, 0xFFFFFFFF)
        self.installer.write_bytes(data)
        with self.assertRaises(ValueError):
            native.read_pe_image(self.installer)


class NativeSourceCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime, self.evidence = make_candidate(self.root)

    def refresh(self):
        proof = native.load_json(self.evidence)
        proof["files"] = native.inventory(self.runtime)
        for role in ("preparation", "source_verification", "build_log", "package_log"):
            proof[role]["sha256"] = native.digest(self.evidence.parent / proof[role]["path"])
        write_json(self.evidence, proof)

    def test_valid_static_candidate_reports_limits_and_payload_hashes(self):
        result = native.validate_candidate(self.runtime, self.evidence)
        self.assertTrue(result["static_candidate_checks_passed"])
        self.assertTrue(result["source_build_evidence"]["integrity_checked"])
        self.assertFalse(result["source_build_evidence"]["compilation_independently_proven"])
        self.assertFalse(result["windows_execution_tested"])
        self.assertFalse(result["windows_install_uninstall_tested"])
        self.assertFalse(result["official_release"])
        self.assertFalse(result["release_eligible"])
        self.assertEqual(result["authenticode_verification"], "not performed")
        self.assertEqual(result["files"], native.inventory(self.runtime))
        self.assertTrue(result["startup_resources"]["exact_startup_content_verified"])
        self.assertFalse(result["startup_resources"]["runtime_loading_verified"])

    def test_rehashed_startup_resource_tampering_is_rejected(self):
        for relative in ("privacybrowser.cfg", "distribution/privacybrowser/chrome.manifest",
                         "defaults/pref/privacybrowser-autoconfig.js"):
            path = self.runtime / relative
            original = path.read_bytes()
            with self.subTest(relative=relative):
                path.write_bytes(original + b"\n// unexpected changed startup content\n")
                self.refresh()
                with self.assertRaisesRegex(ValueError, "startup resource|AutoConfig"):
                    native.validate_candidate(self.runtime, self.evidence)
            path.write_bytes(original)
        self.refresh()

    def test_windows_autoconfig_newlines_are_accepted_but_other_changes_rejected(self):
        path = self.runtime / "defaults/pref/privacybrowser-autoconfig.js"
        original = native.AUTOCONFIG.encode("utf-8")
        path.write_bytes(original.replace(b"\n", b"\r\n"))
        self.refresh()
        self.assertTrue(native.validate_candidate(self.runtime, self.evidence)["startup_resources"]["exact_startup_content_verified"])
        for changed in (original.replace(b"\n", b"\r"), original.replace(b"privacybrowser.cfg", b"different.cfg"),
                        original * 3):
            with self.subTest(changed=changed[:50]):
                path.write_bytes(changed)
                self.refresh()
                with self.assertRaisesRegex(ValueError, "AutoConfig"):
                    native.validate_candidate(self.runtime, self.evidence)

    def test_developer_overlay_and_firefox_binary_are_rejected(self):
        for name in ("firefox.exe", "nested/firefox.exe", "PRIVACY_BROWSER_BUILD.json"):
            path = self.runtime / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("developer overlay")
            self.refresh()
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    native.validate_candidate(self.runtime, self.evidence)
            finally:
                path.unlink()

    def test_missing_required_files_are_rejected(self):
        for name in native.REQUIRED:
            path = self.runtime / name
            original = path.read_bytes()
            path.unlink()
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    native.validate_candidate(self.runtime, self.evidence)
            finally:
                path.write_bytes(original)

    def test_altered_or_omitted_payload_cannot_reuse_build_evidence(self):
        (self.runtime / "xul.dll").write_bytes(b"modified")
        with self.assertRaises(ValueError):
            native.validate_candidate(self.runtime, self.evidence)
        self.refresh()
        (self.runtime / "unlisted.txt").write_text("unlisted")
        with self.assertRaises(ValueError):
            native.validate_candidate(self.runtime, self.evidence)

    def test_wrong_application_identity_or_engine_version_is_rejected(self):
        path = self.runtime / "application.ini"
        original = path.read_text()
        for old, new in (("Name=PrivacyBrowser", "Name=Firefox"), ("Vendor=PrivacyBrowser", "Vendor=Mozilla"),
                         (f"Version={ENGINE}", "Version=0.1.0"), (native.APP_ID, "different-id"),
                         ("RemotingName=privacybrowser", "RemotingName=firefox")):
            path.write_text(original.replace(old, new))
            self.refresh()
            with self.subTest(old=old), self.assertRaises(ValueError):
                native.validate_candidate(self.runtime, self.evidence)
        path.write_text(original)

    def test_no_build_or_preparation_evidence_fails_closed(self):
        original = self.evidence.read_text()
        for key, value in (("build_kind", "developer-runtime-overlay"), ("source_compiled", False),
                           ("official_release", True), ("upstream_version", "1.0esr"), ("target", "linux"),
                           ("native_binary", "firefox.exe"), ("commands", []), ("preparation", None)):
            proof = json.loads(original)
            proof[key] = value
            write_json(self.evidence, proof)
            with self.subTest(key=key), self.assertRaises(ValueError):
                native.validate_candidate(self.runtime, self.evidence)
        self.evidence.write_text(original)

    def test_failed_build_or_missing_log_cannot_pass(self):
        proof = native.load_json(self.evidence)
        proof["commands"][0]["exit_code"] = 1
        write_json(self.evidence, proof)
        with self.assertRaises(ValueError):
            native.validate_candidate(self.runtime, self.evidence)
        proof["commands"][0]["exit_code"] = 0
        write_json(self.evidence, proof)
        (self.evidence.parent / "build.log").write_text("")
        self.refresh()
        with self.assertRaises(ValueError):
            native.validate_candidate(self.runtime, self.evidence)

    def test_changed_log_and_untrusted_source_verification_fail(self):
        log = self.evidence.parent / "build.log"
        log.write_text("changed")
        with self.assertRaises(ValueError):
            native.validate_candidate(self.runtime, self.evidence)
        self.refresh()
        source_path = self.evidence.parent / "source-verification.json"
        original = native.load_json(source_path)
        for key, value in (("signature_verified", False), ("kind", "windows"), ("sha512", "bad"),
                           ("checksum_signer", "A" * 40), ("upstream_version", "1.0esr")):
            write_json(source_path, {**original, key: value})
            self.refresh()
            with self.subTest(key=key), self.assertRaises(ValueError):
                native.validate_candidate(self.runtime, self.evidence)

    def test_source_preparation_must_match_the_compiled_identity(self):
        path = self.evidence.parent / "preparation.json"
        original = native.load_json(path)
        for key, value in (("app_name", "Firefox"), ("icon_sha256", "0" * 64), ("compiled", True),
                           ("prepared", False), ("target", "linux"), ("native_binary", "firefox.exe")):
            write_json(path, {**original, key: value})
            self.refresh()
            with self.subTest(key=key), self.assertRaises(ValueError):
                native.validate_candidate(self.runtime, self.evidence)

    def test_unsafe_evidence_paths_are_rejected(self):
        original = native.load_json(self.evidence)
        for name in ("../build.log", "/tmp/build.log", "C:/build.log", "..\\build.log", "a//build.log",
                     "./build.log", "a:stream", "NUL.txt", "COM1", "a/b. ", "x$.log"):
            proof = json.loads(json.dumps(original))
            proof["build_log"]["path"] = name
            write_json(self.evidence, proof)
            with self.subTest(name=name), self.assertRaises(ValueError):
                native.validate_candidate(self.runtime, self.evidence)

    @unittest.skipIf(sys.platform == "win32", "POSIX-only hostile filenames/symlinks")
    def test_unsafe_payload_paths_and_case_collision_are_rejected(self):
        for name in ("evil$.txt", "a:b", "NUL", "COM1.log", "XUL.dll", "x.", "x ", "line\nbreak", "a\\b"):
            path = self.runtime / name
            path.write_text("unsafe")
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    native.inventory(self.runtime)
            finally:
                path.unlink()
        link = self.runtime / "escape"
        link.symlink_to(self.evidence.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            native.inventory(self.runtime)
        link.unlink()
        log = self.evidence.parent / "build.log"
        log.unlink()
        log.symlink_to(self.runtime / "application.ini")
        with self.assertRaises(ValueError):
            native.validate_candidate(self.runtime, self.evidence)

    def test_unreadable_payload_directory_cannot_be_silently_omitted(self):
        with mock.patch.object(native.os, "scandir", side_effect=PermissionError("inaccessible payload")):
            with self.assertRaises(PermissionError):
                native.inventory(self.runtime)

    def test_evidence_cannot_be_inside_the_payload(self):
        with self.assertRaises(ValueError):
            native.validate_candidate(self.runtime, self.runtime / "build-evidence.json")

    def test_duplicate_json_key_is_rejected(self):
        self.evidence.write_text('{"schema_version": 1, "schema_version": 1}')
        with self.assertRaises(ValueError):
            native.validate_candidate(self.runtime, self.evidence)

    def test_cli_writes_json_without_running_candidate_and_refuses_overwrite(self):
        output = self.root / "report.json"
        command = [sys.executable, str(ROOT / "tools/verify_windows_native.py"), "--runtime", str(self.runtime),
                   "--evidence", str(self.evidence), "--output", str(output)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(native.load_json(output)["app_name"], "PrivacyBrowser")
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)


if __name__ == "__main__":
    unittest.main()
