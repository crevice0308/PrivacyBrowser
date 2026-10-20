"""Offline installer orchestration tests; NSIS and EXEs are never executed.

Both native validators and the compiler are mocked here. Real static PE resource
validation has separate inert-fixture tests in native_package_test.py.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import package_windows_native as package


def snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def simulate_removal_fixture(include: str, present: set[str], locked: set[str]) -> tuple[set[str], int, list[str]]:
    """In-memory model of our narrow generated macro, not an NSIS interpreter.

    This deliberately supports only the reviewed generator's few statements.
    Native installer execution and Win32 file-lock behavior require Windows CI.
    """
    macro = re.search(r"(?ms)^!macro RemoveNativePayload\n(.*?)^!macroend", include)
    if macro is None:
        raise AssertionError("Missing native removal macro")
    lines = [line.strip() for line in macro.group(1).splitlines() if line.strip()]
    remaining = set(present)
    deleted = []
    flag = 0
    index = 0
    pending_path = None
    while index < len(lines):
        line = lines[index]
        if line.startswith("StrCpy $R9 "):
            flag = int(line.rsplit(" ", 1)[1])
        elif line.startswith('Push "$INSTDIR\\'):
            if pending_path is not None:
                raise AssertionError("Unconsumed path in removal macro")
            pending_path = line.removeprefix('Push "$INSTDIR\\').removesuffix('"')
        elif line == "Call un.DeletePayloadFile":
            if pending_path is None:
                raise AssertionError("Missing exact file argument")
            name, pending_path = pending_path, None
            if name in locked:
                # OpenSafeFile refuses a locked object before deletion. This
                # models only its failure result, never Windows file handles.
                return remaining, 1, deleted
            if name in remaining:
                remaining.discard(name)
                deleted.append(name)
        elif line == "Call un.RemovePayloadDirectory":
            if pending_path is None:
                raise AssertionError("Missing exact directory argument")
            pending_path = None  # Empty-directory cleanup does not change files.
        elif line.startswith('IfFileExists "$INSTDIR\\'):
            name = line.removeprefix('IfFileExists "$INSTDIR\\').removesuffix('" 0 +2')
            if name not in remaining:
                index += 2
                continue
        elif line == "${If} $R9 == 0":
            if flag != 0:
                index = lines.index("${EndIf}", index)
        elif line in ("${EndIf}", "Call un.ClosePayloadDirectoryLocks"):
            pass
        else:
            raise AssertionError("Unexpected generated instruction: " + line)
        index += 1
    if pending_path is not None:
        raise AssertionError("Unconsumed path at end of removal macro")
    return remaining, flag, deleted


class NativeInstallerOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        for name, data in {
            "privacybrowser.exe": b"INERT fixture, never executed",
            "LICENSE": b"MPL-2.0 license fixture",
            "NOTICE": b"Upstream copyright notice fixture",
            "subdirectory/payload.txt": b"nested payload",
        }.items():
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.evidence = self.root / "build-evidence.json"
        self.evidence.write_text('{"inert_fixture":true}', encoding="utf-8")
        self.output = self.root / "artifacts" / package.SETUP_NAME
        self.compiler = self.root / "compiler fixture"
        self.compiler.write_text("INERT compiler placeholder; subprocess is mocked", encoding="utf-8")
        self.validation = {
            "native_binary_sha256": hashlib.sha256((self.runtime / "privacybrowser.exe").read_bytes()).hexdigest(),
            "source_build_evidence": {"integrity_checked": True, "compilation_independently_proven": False},
        }
        self.installer_validation = {
            "installer_static_identity_passed": True,
            "payload_extraction_verified": False,
            "windows_execution_tested": False,
            "release_eligible": False,
        }
        self.candidate = self.enterContext(mock.patch.object(package, "validate_candidate", return_value=self.validation))
        self.installer = self.enterContext(mock.patch.object(package, "validate_installer", return_value=self.installer_validation))
        self.compiler_run = self.enterContext(mock.patch.object(
            package.subprocess, "run", side_effect=AssertionError("An unconfigured compiler must never execute")))

    def build(self, **changes):
        arguments = {"runtime": self.runtime, "evidence": self.evidence,
                     "output": self.output, "compiler": self.compiler}
        arguments.update(changes)
        return package.build(**arguments)

    def compile_fixture(self, command, **options):
        self.command = command
        self.options = options
        prefix = "/" if os.name == "nt" else "-"
        include = Path(next(value.removeprefix(prefix + "DPAYLOAD_INCLUDE=")
                            for value in command if value.startswith(prefix + "DPAYLOAD_INCLUDE=")))
        self.include = include
        self.include_text = include.read_text(encoding="utf-8")
        self.output.write_bytes(b"INERT setup artifact; never a runnable program")
        return subprocess.CompletedProcess(command, 0, "mock compiler output\n", "mock compiler warning\n")

    def test_existing_artifact_or_report_is_never_overwritten(self):
        self.output.parent.mkdir()
        for suffix in ("", ".json", ".build.log"):
            path = Path(str(self.output) + suffix)
            path.write_bytes(b"previous artifact must remain")
            try:
                with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "already exists"):
                    self.build()
                self.assertEqual(path.read_bytes(), b"previous artifact must remain")
                self.compiler_run.assert_not_called()
                self.candidate.assert_not_called()
            finally:
                path.unlink()

    @unittest.skipIf(sys.platform == "win32", "POSIX symlink fixture")
    def test_output_runtime_and_dangling_report_links_are_rejected(self):
        self.output.parent.mkdir()
        link = self.root / "runtime-link"
        link.symlink_to(self.runtime, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.build(runtime=link)
        for suffix in ("", ".json", ".build.log"):
            target = Path(str(self.output) + suffix)
            target.symlink_to(self.root / "missing-target")
            try:
                with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                    self.build()
            finally:
                target.unlink()
        self.compiler_run.assert_not_called()

    def test_fixed_output_name_and_runtime_boundary_are_enforced(self):
        for target in (self.root / "Firefox Setup.exe", self.runtime / package.SETUP_NAME,
                       self.runtime / "nested" / package.SETUP_NAME):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.build(output=target)
        self.compiler_run.assert_not_called()

    def test_output_cannot_overwrite_evidence(self):
        for suffix in ("", ".json", ".build.log"):
            target = Path(str(self.output) + suffix)
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "evidence"):
                self.build(evidence=target)
        self.compiler_run.assert_not_called()

    def test_nonempty_regular_license_and_notice_are_required(self):
        for name in ("LICENSE", "NOTICE"):
            path = self.runtime / name
            original = path.read_bytes()
            for value in (None, b"", b" \n\t"):
                if value is None:
                    path.unlink()
                else:
                    path.write_bytes(value)
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, "LICENSE and NOTICE"):
                    self.build()
                path.write_bytes(original)
        self.compiler_run.assert_not_called()
        self.candidate.assert_not_called()

    def test_installer_reserved_filenames_are_rejected_case_insensitively(self):
        reserved = {"uninstall.exe", "PrivacyBrowser.install-marker", "PrivacyBrowser.cleanup-intent",
                    "PrivacyBrowser.desktop-link", "PrivacyBrowser.program-link"}
        self.assertEqual(package.RESERVED, reserved)
        for name in sorted(reserved | {name.upper() for name in reserved} | {"Uninstall.Exe"}):
            path = self.runtime / name
            path.write_bytes(b"must not collide with generated installer files")
            try:
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "reserved"):
                    self.build()
            finally:
                path.unlink()
        self.compiler_run.assert_not_called()

    @unittest.skipIf(sys.platform == "win32", "POSIX hostile filename fixture")
    def test_unsafe_payload_and_nsis_output_paths_are_rejected(self):
        for name in ('dollar$.txt', 'quote".txt', "line\nbreak", "colon:stream", "CON", "nul.txt",
                     "trailing.", "trailing ", "PRIVACYBROWSER.EXE"):
            path = self.runtime / name
            path.write_bytes(b"unsafe path fixture")
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.build()
            finally:
                path.unlink()
        for parent in ('dollar$', 'quote"', "line\nbreak", "tab\tname"):
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                self.build(output=self.root / parent / package.SETUP_NAME)
        self.compiler_run.assert_not_called()

    def test_overlong_relative_payload_path_refuses_before_compiler_or_output(self):
        relative = Path("a" * 120) / ("b" * 116 + ".txt")
        self.assertEqual(len(relative.as_posix()), 241)
        fixture = self.runtime / relative
        fixture.parent.mkdir()
        fixture.write_bytes(b"inert overlong-path fixture")
        before = snapshot(self.runtime)
        with self.assertRaisesRegex(ValueError, "path limit"):
            self.build()
        self.compiler_run.assert_not_called()
        self.candidate.assert_not_called()
        self.assertFalse(self.output.parent.exists())
        self.assertEqual(snapshot(self.runtime), before)

    def test_candidate_rejection_aborts_before_compiler_or_output_creation(self):
        self.candidate.side_effect = ValueError("source-build evidence rejected")
        before = snapshot(self.runtime)
        with self.assertRaisesRegex(ValueError, "evidence rejected"):
            self.build()
        self.compiler_run.assert_not_called()
        self.installer.assert_not_called()
        self.assertFalse(self.output.parent.exists())
        self.assertEqual(snapshot(self.runtime), before)

    def test_missing_compiler_aborts_without_installation_or_fallback(self):
        with self.assertRaisesRegex(ValueError, "makensis compiler"):
            self.build(compiler=self.root / "missing-compiler")
        with mock.patch.object(package.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "makensis compiler"):
                self.build(compiler=None)
        self.compiler_run.assert_not_called()
        self.assertFalse(self.output.parent.exists())

    def test_success_constructs_isolated_exact_payload_command_without_mutating_inputs(self):
        self.compiler_run.side_effect = self.compile_fixture
        before = snapshot(self.runtime)
        evidence_before = self.evidence.read_bytes()
        production_before = (ROOT / "config/esr.json").read_bytes()
        result = self.build()
        prefix = "/" if os.name == "nt" else "-"
        self.assertEqual(self.command, [str(self.compiler), prefix + "V3", prefix + "NOCD",
                                      prefix + "DRUNTIME_DIR=" + str(self.runtime.resolve()),
                                      prefix + "DPAYLOAD_INCLUDE=" + str(self.include),
                                      prefix + "DOUTPUT_FILE=" + str(self.output.resolve()),
                                      str(ROOT / "packaging/windows/privacy-browser-native.nsi")])
        self.assertEqual(self.options, {"capture_output": True, "text": True, "timeout": 900})
        self.assertNotIn("shell", self.options)
        self.assertNotIn(self.runtime, self.include.parents)
        self.assertFalse(self.include.exists(), "Temporary compiler input must be cleaned up")
        self.assertIn("!macro InstallPayload", self.include_text)
        self.assertIn("!macro ValidateNativePayload", self.include_text)
        self.assertIn("!macro RemoveNativePayload", self.include_text)
        self.assertEqual(len(re.findall(r"(?m)^  File ", self.include_text)), len(before))
        self.assertEqual(self.include_text.count("Call un.DeletePayloadFile"), len(before))
        self.assertNotRegex(self.include_text, r"(?im)^\s*(?:Delete|RMDir)\s")
        self.assertNotRegex(self.include_text, r"(?im)^\s*(?:File|RMDir)\s+/r\b")
        self.assertEqual(snapshot(self.runtime), before)
        self.assertEqual(self.evidence.read_bytes(), evidence_before)
        self.assertEqual((ROOT / "config/esr.json").read_bytes(), production_before)
        self.assertIs(json.loads(production_before)["release_allowed"], False)
        self.candidate.assert_called_once_with(self.runtime, self.evidence)
        self.installer.assert_called_once_with(self.output.resolve())
        self.assertEqual(result["sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(result["native_binary_sha256"], self.validation["native_binary_sha256"])
        self.assertEqual(result["source_build_evidence"], self.validation["source_build_evidence"])
        self.assertEqual(result["installer_static_validation"], self.installer_validation)
        self.assertEqual(json.loads(Path(str(self.output) + ".json").read_text()), result)
        self.assertEqual(Path(str(self.output) + ".build.log").read_text(), "mock compiler output\nmock compiler warning\n")

    def test_success_retains_explicit_unsigned_unexecuted_nonproduction_state(self):
        self.compiler_run.side_effect = self.compile_fixture
        result = self.build()
        self.assertEqual(result["build_kind"], "full-source-build-candidate-installer")
        self.assertIs(result["installer_compiled"], True)
        for name in ("authenticode_signed", "windows_execution_tested", "windows_install_uninstall_tested",
                     "official_release", "release_eligible"):
            self.assertIs(result[name], False, name)
        self.assertEqual(result["automatic_updates"], "disabled")
        self.assertIn("no associations are changed", result["default_browser_registration"])

    def test_compiler_failure_retains_log_without_claiming_success(self):
        self.compiler_run.side_effect = None
        self.compiler_run.return_value = subprocess.CompletedProcess([], 7, "failed compiler\n", "reason\n")
        with self.assertRaisesRegex(RuntimeError, "compilation failed"):
            self.build()
        self.assertEqual(Path(str(self.output) + ".build.log").read_text(), "failed compiler\nreason\n")
        self.assertFalse(Path(str(self.output) + ".json").exists())
        self.installer.assert_not_called()

    def test_installer_identity_rejection_never_creates_success_report(self):
        self.compiler_run.side_effect = self.compile_fixture
        self.installer.side_effect = ValueError("stale native identity")
        with self.assertRaisesRegex(ValueError, "stale native identity"):
            self.build()
        self.assertTrue(Path(str(self.output) + ".build.log").is_file())
        self.assertFalse(Path(str(self.output) + ".json").exists())

    def test_native_removal_checks_each_exact_file_and_deletes_executable_last(self):
        include = self.root / "native-payload.nsh"
        before = snapshot(self.runtime)
        package.write_native_payload_include(self.runtime, include)
        source = include.read_text(encoding="utf-8")
        expected = {name.replace("/", "\\") for name in before}
        removal = source.split("!macro RemoveNativePayload\n", 1)[1]
        checked = set(re.findall(r'IfFileExists "\$INSTDIR\\([^"\r\n]+)" 0 \+2', removal))
        self.assertEqual(checked, expected)
        self.assertEqual(removal.count('Push "$INSTDIR\\privacybrowser.exe"'), 1)
        self.assertNotRegex(removal, r"(?im)^\s*(?:Delete|RMDir)\s")
        self.assertNotIn("!macro RemovePayload\n", source)
        self.assertEqual(snapshot(self.runtime), before)
        unrelated = {"uninstall.exe", "PrivacyBrowser.install-marker", "unrelated.txt", "Profile\\bookmarks.sqlite"}
        remaining, flag, deleted = simulate_removal_fixture(source, expected | unrelated, set())
        self.assertEqual(remaining, unrelated)
        self.assertEqual(flag, 0)
        self.assertEqual(deleted[-1], "privacybrowser.exe")

    def test_native_preflight_locks_exact_parent_directories_before_exact_files(self):
        nested = self.runtime / "subdirectory" / "nested" / "payload.txt"
        nested.parent.mkdir()
        nested.write_bytes(b"inert nested payload")
        include = self.root / "native-payload.nsh"
        before = snapshot(self.runtime)
        package.write_native_payload_include(self.runtime, include)
        source = include.read_text(encoding="utf-8")
        self.assertRegex(source, rf"(?m)^!define NATIVE_MAX_RELATIVE_PATH {max(map(len, before))}$")
        macro = re.search(r"(?ms)^!macro ValidateNativePayload\n(.*?)^!macroend", source)
        self.assertIsNotNone(macro)
        validation = macro.group(1)
        directories = re.findall(r'Push "\$INSTDIR\\([^"\r\n]+)"\n\s*Call un\.LockPayloadDirectory', validation)
        self.assertEqual(directories, ["subdirectory", "subdirectory\\nested"])
        files = re.findall(r'Push "\$INSTDIR\\([^"\r\n]+)"\n\s*Call un\.ValidatePayloadFile', validation)
        self.assertCountEqual(files, [name.replace("/", "\\") for name in before])
        self.assertLess(validation.rindex("Call un.LockPayloadDirectory"),
                        validation.index("Call un.ValidatePayloadFile"))
        self.assertNotRegex(validation, r"(?im)^\s*(?:Delete|RMDir|File)\s")
        self.assertNotIn("*", validation)
        self.assertNotIn("?", validation)
        removal = source.split("!macro RemoveNativePayload\n", 1)[1]
        self.assertRegex(removal, r'\$\{If\} \$R9 == 0\n\s*Call un.ClosePayloadDirectoryLocks\n\s*Push "\$INSTDIR\\')
        removed_directories = re.findall(r'Push "\$INSTDIR\\([^"\r\n]+)"\n\s*Call un\.RemovePayloadDirectory', removal)
        self.assertEqual(removed_directories, list(reversed(directories)))
        self.assertEqual(snapshot(self.runtime), before)

    def test_missing_executable_retry_checks_the_entire_exact_inventory(self):
        include = self.root / "native-payload.nsh"
        before = snapshot(self.runtime)
        package.write_native_payload_include(self.runtime, include)
        source = include.read_text(encoding="utf-8")
        macro = re.search(r"(?ms)^!macro VerifyNativePayloadAbsent\n(.*?)^!macroend", source)
        self.assertIsNotNone(macro)
        checks = re.findall(r'IfFileExists "\$INSTDIR\\([^"\r\n]+)" 0 \+2\n\s*Call un.DataRefusal', macro.group(1))
        expected = {name.replace("/", "\\") for name in before}
        self.assertCountEqual(checks, expected)
        self.assertEqual(macro.group(1).count("Call un.DataRefusal"), len(expected))
        self.assertNotRegex(macro.group(1), r"(?im)^\s*(?:Delete|RMDir|File)\s")
        self.assertEqual(snapshot(self.runtime), before)

    def test_any_locked_payload_retains_executable_for_safe_uninstall_retry(self):
        include = self.root / "native-payload.nsh"
        package.write_native_payload_include(self.runtime, include)
        source = include.read_text(encoding="utf-8")
        payload = {name.replace("/", "\\") for name in snapshot(self.runtime)}
        retained = {"uninstall.exe", "PrivacyBrowser.install-marker", "unrelated.txt"}
        for locked in payload:
            with self.subTest(locked=locked):
                remaining, flag, _ = simulate_removal_fixture(source, payload | retained, {locked})
                self.assertEqual(flag, 1)
                self.assertIn(locked, remaining)
                self.assertIn("privacybrowser.exe", remaining)
                self.assertTrue(retained <= remaining)
                retried, retry_flag, _ = simulate_removal_fixture(source, remaining, set())
                self.assertEqual(retry_flag, 0)
                self.assertEqual(retried, retained)

    def test_native_helper_does_not_change_developer_overlay_removal(self):
        native_include, old_include = self.root / "native.nsh", self.root / "overlay.nsh"
        package.write_native_payload_include(self.runtime, native_include)
        package.write_payload_include(self.runtime, old_include)
        self.assertIn("!macro RemoveNativePayload", native_include.read_text())
        old_source = old_include.read_text()
        self.assertIn("!macro RemovePayload\n", old_source)
        self.assertNotIn("RemoveNativePayload", old_source)
        self.assertNotIn("$R9", old_source)


class NativeInstallerTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "packaging/windows/privacy-browser-native.nsi").read_text(encoding="utf-8")
        cls.commands = "\n".join(line for line in cls.source.splitlines() if not line.lstrip().startswith(";"))

    def function(self, name: str) -> str:
        match = re.search(r"(?ms)^Function " + re.escape(name) + r"\s*$\n(.*?)^FunctionEnd\s*$", self.commands)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def test_product_icon_and_binary_identity_are_exact(self):
        self.assertIn('Name "PrivacyBrowser"', self.commands)
        self.assertIn('InstallDir "$LOCALAPPDATA\\Programs\\PrivacyBrowser"', self.commands)
        for key, value in (("ProductName", "PrivacyBrowser Setup"), ("FileDescription", "PrivacyBrowser Installer"),
                           ("InternalName", "PrivacyBrowser Setup"), ("OriginalFilename", package.SETUP_NAME)):
            self.assertIn(f'VIAddVersionKey /LANG=1033 "{key}" "{value}"', self.commands)
        for key in ("MUI_ICON", "MUI_UNICON"):
            self.assertIn(f'!define {key} "${{RUNTIME_DIR}}/distribution/privacybrowser/assets/privacybrowser.ico"', self.commands)
        shortcuts = [line for line in self.commands.splitlines() if line.strip().startswith("CreateShortcut ")]
        self.assertEqual(len(shortcuts), 2)
        for line in shortcuts:
            self.assertIn('"$INSTDIR\\privacybrowser.exe"', line)
            self.assertIn('"$INSTDIR\\distribution\\privacybrowser\\assets\\privacybrowser.ico"', line)
        self.assertNotIn("firefox.exe", self.commands.lower())
        self.assertNotIn("PrivacyBrowser-Dev", self.commands)

    def test_installation_is_per_user_without_hklm_writes_or_associations(self):
        self.assertIn("RequestExecutionLevel user", self.commands)
        self.assertNotRegex(self.commands, r"(?im)^\s*SetShellVarContext\s+all\b")
        registry_writes = re.findall(r'(?im)^\s*(WriteReg\w+|DeleteReg\w+)\s+(?:/ifempty\s+)?(HK\w+)\s+"([^"\r\n]+)"',
                                     self.commands)
        self.assertTrue(registry_writes)
        uninstall_key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser"
        cleanup_keys = {"${LAUNCHER_KEY}", "${SKELETON_KEY}", r"SOFTWARE\PrivacyBrowser\PrivacyBrowser",
                        r"SOFTWARE\PrivacyBrowser"}
        for command, hive, key in registry_writes:
            self.assertEqual(hive, "HKCU", (command, hive, key))
            if key != uninstall_key:
                self.assertIn(key, cleanup_keys)
                self.assertTrue(command.startswith("DeleteReg"), command)
        self.assertNotRegex(self.commands, r"(?i)RegisteredApplications|Software\\Classes|Capabilities|StartMenuInternet|UserChoice")

    def test_browser_launch_requires_unchecked_finish_page_choice(self):
        self.assertIn("!define MUI_FINISHPAGE_RUN_NOTCHECKED", self.commands)
        self.assertIn("!define MUI_FINISHPAGE_RUN_FUNCTION LaunchPrivacyBrowser", self.commands)
        launcher = self.function("LaunchPrivacyBrowser")
        self.assertIn('ExecShell "open" "$INSTDIR\\privacybrowser.exe"', launcher)
        remaining = self.commands.replace(launcher, "")
        self.assertNotRegex(remaining, r"(?im)^\s*(?:Exec|ExecWait|ExecShell|Call\s+LaunchPrivacyBrowser)\b")

    def test_installation_refuses_existing_directory_before_payload_write(self):
        initializer = self.function(".onInit")
        self.assertIn('IfFileExists "$INSTDIR\\*.*" 0 ready', initializer)
        self.assertRegex(initializer, r'(?s)IfFileExists.*?MessageBox.*?Abort\s+ready:')
        self.assertNotIn(".install-marker", initializer)
        self.assertIn("Call CheckBrowserClosed", initializer)
        self.assertIn("!insertmacro InstallPayload", self.commands)

    def test_uninstaller_checks_install_marker_and_never_recursively_deletes_paths(self):
        initializer = self.function("un.onInit")
        self.assertIn('IfFileExists "$INSTDIR\\PrivacyBrowser.install-marker" 0 invalid', initializer)
        self.assertIn('StrCpy $0 "$INSTDIR\\PrivacyBrowser.install-marker"', initializer)
        self.assertIn('StrCpy $1 "PrivacyBrowser 0.1.0-dev"', initializer)
        self.assertIn("Call un.VerifyAsciiFile", initializer)
        self.assertRegex(initializer, r"(?s)invalid:.*?Abort")
        self.assertIn("!insertmacro RemoveNativePayload", self.commands)
        self.assertNotRegex(self.commands, r"(?im)^\s*(?:Delete|RMDir)\s")
        self.assertNotRegex(self.commands, r"(?i)DeleteFile[AW]?\(|RemoveDirectory[AW]?\(")
        for line in self.commands.splitlines():
            if re.match(r"\s*(?:Delete|RMDir)\s", line):
                self.assertNotRegex(line, r"(?i)Profile|parent\.lock|\*|\?")

    def test_uninstall_guard_checks_installed_executable_for_every_profile(self):
        guard = self.function("un.CheckBrowserClosed")
        self.assertIn('CreateFileW(w "$INSTDIR\\privacybrowser.exe", i 0x40000000, i 0, p 0, i 3, i 0x80, p 0)', guard)
        self.assertRegex(guard, r"(?s)\$\{If\} \$0 == -1.*?SetErrorLevel 1\s+Abort\s+\$\{EndIf\}")
        self.assertIn("kernel32::CloseHandle(p r0) i.r1", guard)
        self.assertNotIn("parent.lock", guard)
        self.assertNotRegex(guard, r"\$1 == (?:32|33)")
        self.assertIn("Call un.CheckBrowserClosed", self.function("un.onInit"))
        uninstall = self.commands.split('Section "Uninstall"', 1)[1]
        self.assertLess(uninstall.index("Call un.CheckBrowserClosed"), uninstall.index("!insertmacro RemoveNativePayload"))
        self.assertNotRegex(self.commands, r"(?i)taskkill|TerminateProcess|nsProcess|KillProc")

    def test_failed_payload_removal_retains_app_marker_and_uninstall_recovery(self):
        uninstall = self.commands.split('Section "Uninstall"', 1)[1]
        guard = re.search(r"(?s)!insertmacro RemoveNativePayload\s+\$\{If\} \$R9 != 0.*?SetErrorLevel 1\s+Abort\s+\$\{EndIf\}", uninstall)
        self.assertIsNotNone(guard)
        guard_end = guard.end()
        self.assertLess(guard_end, uninstall.index("Call un.FinalizeMetadata"))
        self.assertLess(uninstall.index("Call un.EnsureCleanupIntent"), guard.start())
        self.assertLess(uninstall.index("Call un.RemoveOwnedRuntimeRegistry"), guard.start())
        self.assertNotIn("DeleteRegKey", uninstall)
        self.assertNotIn("Call un.DeleteMetadataFile", uninstall)

    def test_all_refusals_are_silent_safe_and_return_nonzero(self):
        lines = [line.strip() for line in self.commands.splitlines() if line.strip()]
        messages = [line for line in lines if line.startswith("MessageBox ")]
        self.assertTrue(messages)
        for line in messages:
            if "MB_YESNOCANCEL" in line:
                self.assertTrue(line.endswith(" /SD IDCANCEL IDYES consent_yes IDNO consent_no"), line)
                self.assertIn("MB_DEFBUTTON2", line)
            else:
                self.assertTrue(line.endswith(" /SD IDOK"), line)
        for index, line in enumerate(lines):
            if line == "Abort":
                self.assertEqual(lines[index - 1], "SetErrorLevel 1")

    def test_purge_requires_explicit_flag_or_interactive_consent(self):
        initializer = self.function("un.onInit")
        default = initializer.index("StrCpy $PurgeUserData 0")
        flag = initializer.index('${GetOptions} "$0" "/PURGEUSERDATA" $1')
        silent = initializer.index("IfSilent consent_done")
        prompt = initializer.index("MessageBox MB_YESNOCANCEL")
        self.assertLess(default, flag)
        self.assertLess(flag, silent)
        self.assertLess(silent, prompt)
        self.assertRegex(initializer[flag:silent], r'IfErrors no_purge_flag\s+\$\{If\} \$1 != ""\s+Call un.DataRefusal')
        self.assertRegex(initializer[flag:silent], r"StrCpy \$PurgeUserData 1\s+no_purge_flag:")
        self.assertRegex(initializer, r"consent_yes:\s+StrCpy \$PurgeUserData 1\s+Goto consent_done")
        self.assertRegex(initializer, r"consent_no:\s+StrCpy \$PurgeUserData 0\s+consent_done:")
        for disclosure in ("saved passwords", "bookmarks", "history", "settings and cache",
                           "$LocalDataRoot", "$RoamingDataRoot", "cannot be undone"):
            self.assertIn(disclosure, initializer[prompt:initializer.index("consent_yes:")])
        uninstall = self.commands.split('Section "Uninstall"', 1)[1]
        self.assertRegex(uninstall, r"\$\{If\} \$PurgeUserData == 1\s+Call un.PurgeOwnedData\s+\$\{EndIf\}")
        self.assertEqual(self.commands.count("Call un.PurgeOwnedData"), 1)
        self.assertLess(uninstall.index("!insertmacro ValidateNativePayload"), uninstall.index("Call un.PurgeOwnedData"))
        self.assertLess(uninstall.index("Call un.PurgeOwnedData"), uninstall.index("!insertmacro RemoveNativePayload"))

    def test_owned_roots_are_shell_derived_disjoint_and_bound_to_installation(self):
        initialize = self.function("${PREFIX}InitializeDataPaths")
        self.assertRegex(initialize, r'Push "\$LOCALAPPDATA\\PrivacyBrowser"\s+Call \$\{PREFIX\}CanonicalizePath\s+Pop \$LocalDataRoot')
        self.assertRegex(initialize, r'Push "\$APPDATA\\PrivacyBrowser"\s+Call \$\{PREFIX\}CanonicalizePath\s+Pop \$RoamingDataRoot')
        for first, second in (("INSTDIR", "LocalDataRoot"), ("INSTDIR", "RoamingDataRoot"),
                              ("LocalDataRoot", "RoamingDataRoot")):
            self.assertRegex(initialize, rf"StrCpy \$0 \${first}\s+StrCpy \$1 \${second}\s+Call \$\{{PREFIX\}}RejectOverlap")
        overlap = self.function("${PREFIX}RejectOverlap")
        self.assertIn('${GetParent} "$2" $3', overlap)
        self.assertRegex(overlap, r"\$\{If\} \$2 == \$1\s+Call \$\{PREFIX\}DataRefusal")
        self.assertRegex(overlap, r"StrCpy \$2 \$0\s+StrCpy \$0 \$1\s+StrCpy \$1 \$2")
        self.assertNotRegex(initialize, r"(?i)ReadEnvStr")
        self.assertNotRegex(self.commands, r"(?i)profiles\.ini|installs\.ini|MOZ_PROFILE")
        self.assertRegex(initialize, r"StrLen \$0 \$INSTDIR\s+IntOp \$1 \$0 \+ \$\{NATIVE_MAX_RELATIVE_PATH\}\s+\$\{If\} \$1 > 240\s+Call \$\{PREFIX\}DataRefusal")
        registration = self.function("${PREFIX}VerifyRegistration")
        self.assertIn('"InstallLocation"', registration)
        self.assertIn('"UninstallString"', registration)
        self.assertIn("StrCmpS $0 $INSTDIR 0 registration_invalid", registration)
        self.assertIn('uninstall.exe', registration)
        self.assertIn("Call un.VerifyRegistration", self.function("un.onInit"))

    def test_canonicalization_accepts_fresh_paths_without_creating_or_opening_them(self):
        canonicalize = self.function("${PREFIX}CanonicalizePath")
        self.assertIn("GetFullPathNameW(w r0, i ${NSIS_MAX_STRLEN}, w .r1, p 0) i.r2 ?e", canonicalize)
        self.assertIn("Pop $NativeError", canonicalize)
        self.assertRegex(canonicalize, r'\$\{If\} \$0 == ""\s+StrCpy \$NativeError 87\s+Call \$\{PREFIX\}DataRefusal')
        self.assertRegex(canonicalize, r'\$\{If\} \$2 == 0\s+Call \$\{PREFIX\}DataRefusal')
        self.assertRegex(canonicalize, r'\$\{If\} \$2 >= \$\{NSIS_MAX_STRLEN\}\s+StrCpy \$NativeError 122\s+Call \$\{PREFIX\}DataRefusal')
        self.assertIn("StrCpy $0 $1", canonicalize)
        self.assertNotRegex(canonicalize, r"(?i)CreateDirectory|GetFileAttributes|IfFileExists|CreateFile|FileOpen|Delete|RMDir")
        initialize = self.function("${PREFIX}InitializeDataPaths")
        self.assertNotRegex(self.commands, r"(?m)^\s*GetFullPathName\s")
        self.assertRegex(initialize, r'Push "\$INSTDIR"\s+Call \$\{PREFIX\}CanonicalizePath\s+Pop \$INSTDIR')
        self.assertEqual(initialize.count("Call ${PREFIX}CanonicalizePath"), 5)
        self.assertLess(initialize.rindex("Call ${PREFIX}CanonicalizePath"), initialize.index("Call ${PREFIX}LockDirectoryChain"))

    def test_canonicalization_expands_existing_aliases_without_permissive_fallback(self):
        canonicalize = self.function("${PREFIX}CanonicalizePath")
        self.assertIn("GetLongPathNameW(w r0, w .r1, i ${NSIS_MAX_STRLEN}) i.r2 ?e", canonicalize)
        self.assertRegex(canonicalize, r'\$\{If\} \$NativeError != 2\s+\$\{AndIf\} \$NativeError != 3\s+Call \$\{PREFIX\}DataRefusal')
        self.assertRegex(canonicalize, r'\$\{If\} \$0 == \$5\s+\$\{OrIf\} \$0 == "\$5\\"\s+\$\{OrIf\} \$4 == ""\s+Call \$\{PREFIX\}DataRefusal')
        self.assertRegex(canonicalize, r'StrLen \$6 \$4\s+StrLen \$5 \$0\s+\$\{If\} \$6 >= \$5\s+Call \$\{PREFIX\}DataRefusal')
        self.assertIn('${GetFileName} "$0" $5', canonicalize)
        self.assertRegex(canonicalize, r'StrCpy \$3 "\\\$5\$3"\s+StrCpy \$0 \$4\s+Goto canonical_existing_ancestor')
        self.assertRegex(canonicalize, r'StrLen \$4 \$1\s+StrLen \$5 \$3\s+IntOp \$4 \$4 \+ \$5\s+\$\{If\} \$4 >= \$\{NSIS_MAX_STRLEN\}\s+StrCpy \$NativeError 122\s+Call \$\{PREFIX\}DataRefusal')
        self.assertIn('StrCpy $0 "$1$3"', canonicalize)
        self.assertEqual(re.findall(r"(?m)^\s*Push (\$\d)$", canonicalize), [f"${index}" for index in range(1, 7)])
        self.assertEqual(re.findall(r"(?m)^\s*Pop (\$\d)$", canonicalize), [f"${index}" for index in reversed(range(1, 7))])

    def test_canonicalization_refuses_ambiguous_components_before_normalizing(self):
        canonicalize = self.function("${PREFIX}CanonicalizePath")
        scanning = canonicalize.split("canonical_components_done:", 1)[0]
        for condition in ('${If} $3 == "\\"', '${OrIf} $3 == "/"', '${OrIf} $3 == ""',
                          '${If} $2 == "."', '${OrIf} $2 == " "'):
            self.assertIn(condition, scanning)
        self.assertLess(canonicalize.index("canonical_components_done:"), canonicalize.index("GetFullPathNameW"))
        self.assertRegex(canonicalize, r'\$\{If\} \$5 == "\\"\s+\$\{AndIf\} \$1 != \$4\s+\$\{AndIf\} \$1 != "\$4\\"\s+StrCpy \$1 \$1 -1')

    def test_owned_marker_is_atomic_unicode_and_exactly_path_bound(self):
        self.assertIn('!define DATA_MARKER "PrivacyBrowser.userdata-owner"', self.commands)
        self.assertIn('!define DATA_PROTOCOL "PrivacyBrowser-UserData-v1"', self.commands)
        writer = self.function("${PREFIX}WriteMarkerBytes")
        for statement in ("StrCpy $NativeAccess 0x40000000", "StrCpy $NativeShare 0",
                          "StrCpy $NativeDisposition 2", "StrCpy $NativeOptions 0x40",
                          'Push "$0\\${DATA_MARKER}"', "Call ${PREFIX}OpenNoReparse"):
            self.assertIn(statement, writer)
        self.assertIn('StrCpy $3 "${DATA_PROTOCOL}$\\r$\\n$INSTDIR$\\r$\\n$0$\\r$\\n"', writer)
        self.assertRegex(writer, r"StrLen \$4 \$3\s+IntOp \$4 \$4 \* 2")
        self.assertIn("WriteFile(p r1, w r3, i r4, *i.r3, p 0)", writer)
        self.assertIn("${OrIf} $3 != $4", writer)
        self.assertNotIn("FileOpen", writer)
        reader = self.function("${PREFIX}VerifyOwnedRoot")
        for statement in ("StrCpy $NativeAccess 0x80000000", "StrCpy $NativeShare 1",
                          "StrCpy $NativeDisposition 1", "StrCpy $NativeOptions 0x40",
                          'Push "$0\\${DATA_MARKER}"', "Call ${PREFIX}OpenNoReparse"):
            self.assertIn(statement, reader)
        self.assertIn("GetFileInformationByHandle(p r7, p r8)", reader)
        self.assertIn("IntOp $1 $1 & 0x410", reader)
        self.assertIn("GetFileSizeEx(p r7, *l.r3)", reader)
        self.assertRegex(reader, r"StrLen \$4 \$3\s+IntOp \$4 \$4 \* 2")
        self.assertIn("${OrIf} $3 != $4", reader)
        self.assertEqual(reader.count("FileReadUTF16LE"), 4)
        for expected in ('StrCmpS $3 "${DATA_PROTOCOL}$\\r$\\n" 0 owned_invalid',
                         'StrCmpS $4 "$INSTDIR$\\r$\\n" 0 owned_invalid',
                         'StrCmpS $5 "$0$\\r$\\n" 0 owned_invalid',
                         '${If} $3 != ""'):
            self.assertIn(expected, reader)
        ensure = self.function("EnsureOwnedRoot")
        self.assertIn("StrCpy $NativeDisposition 2", ensure)
        self.assertIn("StrCpy $NativeOptions 1", ensure)
        self.assertLess(ensure.index("Call VerifyOwnedRoot"), ensure.index("Call OpenNoReparse"))
        self.assertLess(ensure.index("Call OpenNoReparse"), ensure.index("Call WriteOwnershipMarker"))
        self.assertRegex(ensure, r"Call WriteOwnershipMarker\s+Push \$0\s+Call VerifyOwnedRoot")

    def test_no_follow_directory_handles_pin_ancestors_and_both_roots(self):
        opener = self.function("${PREFIX}OpenSafeDirectory")
        self.assertIn("StrCpy $3 3", opener)
        for statement in ("StrCpy $NativeAccess $DirectoryAccess", "StrCpy $NativeShare $3",
                          "StrCpy $NativeDisposition 1", "StrCpy $NativeOptions 1", "Call ${PREFIX}OpenNoReparse"):
            self.assertIn(statement, opener)
        self.assertIn("StrCpy $DirectoryAccess 0x81", self.function("${PREFIX}InitializeDataPaths"))
        for root, lock in (("LocalDataRoot", "LocalRootLock"), ("RoamingDataRoot", "RoamingRootLock")):
            self.assertRegex(opener, rf"\$\{{If\}} \$0 == \${root}\s+\$\{{AndIf\}} \${lock} != 0\s+StrCpy \$3 7")
        self.assertIn("GetFileInformationByHandle(p r1, p r2)", opener)
        self.assertIn("IntOp $2 $3 & 0x400", opener)
        self.assertIn("IntOp $2 $3 & 0x10", opener)
        chain = self.function("${PREFIX}LockDirectoryChain")
        self.assertLess(chain.index("Call ${PREFIX}LockDirectoryChain"), chain.index("Call ${PREFIX}OpenSafeDirectory"))
        self.assertIn("StrCpy $DirectoryLocks $2", chain)
        self.assertNotIn("CloseHandle", chain)
        purge = self.function("un.PurgeOwnedData")
        preflight = purge.index("StrCpy $TreeMode 0")
        self.assertIn("StrCpy $DirectoryAccess 0x10081", purge[:preflight])
        self.assertIn("Pop $LocalRootLock", purge[:preflight])
        self.assertIn("Pop $RoamingRootLock", purge[:preflight])
        self.assertNotIn("CloseHandle", purge)

    def test_new_and_reused_owned_roots_stay_pinned_through_installation(self):
        ensure = self.function("EnsureOwnedRoot")
        created = ensure.split("owned_existing:", 1)[0]
        self.assertRegex(created, r"Call OpenNoReparse\s+Pop \$1")
        self.assertLess(created.index("Call OpenNoReparse"), created.index("Call WriteOwnershipMarker"))
        self.assertRegex(created, r"Call WriteOwnershipMarker\s+Push \$0\s+Call VerifyOwnedRoot\s+Goto owned_pinned")
        reused = ensure.split("owned_existing:", 1)[1].split("owned_pinned:", 1)[0]
        self.assertRegex(reused, r"Push \$0\s+Call OpenSafeDirectory\s+Pop \$1\s+Push \$0\s+Call VerifyOwnedRoot")
        pinned = ensure.split("owned_pinned:", 1)[1]
        self.assertRegex(pinned, r"\$\{If\} \$0 == \$LocalDataRoot\s+StrCpy \$LocalRootLock \$1")
        self.assertRegex(pinned, r"\$\{ElseIf\} \$0 == \$RoamingDataRoot\s+StrCpy \$RoamingRootLock \$1")
        self.assertNotIn("CloseHandle", ensure)
        install = self.commands.split('Section "PrivacyBrowser"', 1)[1].split("SectionEnd", 1)[0]
        self.assertLess(install.rindex("Call EnsureOwnedRoot"), install.index("!insertmacro InstallPayload"))
        self.assertNotRegex(install, r"CloseHandle\(p \$(?:LocalRootLock|RoamingRootLock)\)")

    def test_fresh_install_directory_is_created_and_pinned_atomically(self):
        create = self.function("CreateFreshInstallDirectory")
        self.assertRegex(create, r"\$\{If\} \$InstallDirectoryLock != 0\s+Call DataRefusal")
        self.assertRegex(create, r'\$\{GetParent\} "\$INSTDIR" \$0\s+Push \$0\s+Call LockDirectoryChain')
        self.assertLess(create.index("Call LockDirectoryChain"), create.index("Call OpenNoReparse"))
        self.assertRegex(create, r"StrCpy \$NativeAccess 0x81\s+StrCpy \$NativeShare 3\s+StrCpy \$NativeDisposition 2\s+StrCpy \$NativeOptions 1\s+Push \$INSTDIR\s+Call OpenNoReparse\s+Pop \$InstallDirectoryLock")
        self.assertNotIn("0x10081", create)
        self.assertRegex(create, r"\$\{If\} \$InstallDirectoryLock == 0\s+\$\{OrIf\} \$InstallDirectoryLock == -1\s+Call DataRefusal")
        self.assertRegex(create, r"System::Alloc 52\s+Pop \$1\s+\$\{If\} \$1 == 0\s+Call DataRefusal")
        self.assertIn("GetFileInformationByHandle(p $InstallDirectoryLock, p r1) i.r2", create)
        self.assertRegex(create, r"i\.r2'\s+\$\{If\} \$2 == 0\s+Call DataRefusal")
        self.assertRegex(create, r"IntOp \$2 \$2 & 0x410\s+\$\{If\} \$2 != 0x10\s+Call DataRefusal")
        self.assertEqual(create.count("Call OpenNoReparse"), 1)
        self.assertNotRegex(create, r"(?i)IfFileExists|CreateDirectory|OpenSafeDirectory|CloseHandle|RMDir|Delete")
        self.assertEqual(re.findall(r"(?m)^\s*Push (\$\d)$", create), ["$0", "$1", "$2", "$0"])
        self.assertEqual(re.findall(r"(?m)^\s*Pop (\$\d)$", create), ["$1", "$2", "$1", "$0"])
        self.assertNotIn("Function un.CreateFreshInstallDirectory", self.commands)

    def test_production_and_synthetic_install_share_fresh_directory_creation(self):
        install = self.commands.split('Section "PrivacyBrowser"', 1)[1].split("SectionEnd", 1)[0]
        fixture = (ROOT / "packaging/windows/native-safety-fixture.nsh").read_text(encoding="utf-8")
        synthetic = fixture.split("Function NativeSafetyFixtureMain", 1)[1].split("FunctionEnd", 1)[0]
        for caller in (install, synthetic):
            self.assertEqual(caller.count("Call CreateFreshInstallDirectory"), 1)
            self.assertLess(caller.index("Call CreateFreshInstallDirectory"), caller.index("Call EnsureOwnedRoot"))
            self.assertNotIn('CreateDirectory "$INSTDIR"', caller)
            self.assertNotIn("Call LockInstallDirectory", caller)
            self.assertNotIn('IfFileExists "$INSTDIR\\*.*"', caller)
            self.assertNotIn("CloseHandle(p $InstallDirectoryLock)", caller)
        self.assertLess(install.index("Call CreateFreshInstallDirectory"), install.index("!insertmacro InstallPayload"))
        self.assertIn("Existing folders, even empty ones, are refused.", self.function(".onInit"))
        self.assertRegex(synthetic, r'Call CreateFreshInstallDirectory\s+;[^\n]*\n\s+!insertmacro SyntheticPhase "" "set-application-output-path"\s+ClearErrors\s+SetOutPath "\$INSTDIR"\s+IfErrors 0 \+2\s+Call DataRefusal')
        self.assertIn("kernel32::GetCurrentDirectoryW(i ${NSIS_MAX_STRLEN}, w .r0) i.r1", synthetic)
        self.assertRegex(synthetic, r"i\.r1'\s+\$\{If\} \$1 == 0\s+\$\{OrIf\} \$1 >= \$\{NSIS_MAX_STRLEN\}\s+Call DataRefusal")
        self.assertRegex(synthetic, r"StrCmpS \$0 \$INSTDIR \+2\s+Call DataRefusal")
        self.assertLess(synthetic.index('SetOutPath "$INSTDIR"'), synthetic.index("GetCurrentDirectoryW"))
        self.assertLess(synthetic.index("GetCurrentDirectoryW"), synthetic.index("Call EnsureOwnedRoot"))
        self.assertNotIn("$OUTDIR", synthetic)
        # Uninstall still opens its existing directory rather than creating one.
        lock = self.function("${PREFIX}LockInstallDirectory")
        self.assertRegex(lock, r'Call \$\{PREFIX\}LockDirectoryChain\s+StrCpy \$DirectoryAccess 0x10081\s+Push \$INSTDIR\s+Call \$\{PREFIX\}OpenSafeDirectory\s+Pop \$InstallDirectoryLock\s+StrCpy \$DirectoryAccess 0x81')
        self.assertIn("Call un.LockInstallDirectory", self.function("un.onInit"))
        self.assertIn("Call un.LockInstallDirectory", fixture)

    def test_both_trees_preflight_before_files_and_markers_are_deleted_last(self):
        purge = self.function("un.PurgeOwnedData")
        positions = [purge.index(f"StrCpy $TreeMode {mode}") for mode in (0, 1, 2)]
        positions.append(purge.index("Call un.FinalizeOwnedRoot"))
        self.assertEqual(positions, sorted(positions))
        for start, end in zip(positions, positions[1:]):
            phase = purge[start:end]
            for root in ("LocalDataRoot", "RoamingDataRoot"):
                self.assertRegex(phase, rf"Push \${root}\s+Call un.WalkOwnedTree")
        self.assertLess(purge.index("Call un.DeleteHeldProfileLocks"), positions[2])
        self.assertGreater(purge.index("Call un.DeleteHeldProfileLocks"), positions[1])
        walker = self.function("un.WalkOwnedTree")
        marker_skip = walker.split('${If} $3 == "$LocalDataRoot\\${DATA_MARKER}"', 1)[1].split('${If} $TreeMode == 2', 1)[0]
        self.assertIn('${OrIf} $3 == "$RoamingDataRoot\\${DATA_MARKER}"', marker_skip)
        self.assertRegex(marker_skip, r"\$\{If\} \$TreeMode == 0\s+Push \$3\s+Call un.OpenSafeFile")
        self.assertIn("Goto walk_next", marker_skip)
        self.assertNotIn("Call un.DeleteOpenedObject", marker_skip)
        self.assertIn("IntOp $5 $4 & 0x400", walker)
        self.assertIn("${If} $3 > 240", walker)
        self.assertRegex(walker, r"\$\{If\} \$TreeMode == 2\s+Call un.DataRefusal")
        self.assertRegex(walker, r"\$\{If\} \$TreeMode == 1\s+Push \$8\s+Call un.DeleteOpenedObject")
        self.assertRegex(walker, r'\$\{If\} \$TreeMode == 0\s+\$\{AndIf\} \$2 == "parent.lock"')
        self.assertIn("StrCpy $ProfileLocks $5", walker)
        self.assertIn("${If} $9 != 18", walker)
        self.assertNotRegex(walker, r"(?m)^\s*Delete\s")

    def test_file_deletion_uses_verified_handles_and_restores_failed_root_marker(self):
        opener = self.function("un.OpenSafeFile")
        for statement in ("StrCpy $NativeAccess 0xC0010080", "StrCpy $NativeShare 0",
                          "StrCpy $NativeDisposition 1", "StrCpy $NativeOptions 0x40", "Call un.OpenNoReparse"):
            self.assertIn(statement, opener)
        self.assertIn("GetFileInformationByHandle(p r1, p r2)", opener)
        self.assertIn("IntOp $2 $3 & 0x411", opener)
        deletion = self.function("un.DeleteOpenedObject")
        self.assertIn("SetFileInformationByHandle(p r0, i 4, p r1, i 1)", deletion)
        self.assertNotIn("CreateFile", deletion)
        finalize = self.function("un.FinalizeOwnedRoot")
        self.assertLess(finalize.index("Call un.VerifyOwnedRoot"), finalize.index("Call un.DeleteOpenedObject"))
        self.assertIn('Push "$0\\${DATA_MARKER}"', finalize)
        self.assertIn("StrCpy $1 $LocalRootLock", finalize)
        self.assertIn("StrCpy $1 $RoamingRootLock", finalize)
        self.assertEqual(finalize.count("Call un.DeleteOpenedObject"), 2)
        self.assertRegex(finalize, r"Push \$1\s+Call un.DeleteOpenedObject\s+Pop \$3\s+\$\{If\} \$3 == 0\s+Push \$0\s+Call un.WriteMarkerBytes\s+Call un.DataRefusal")
        self.assertLess(finalize.index("Call un.WriteMarkerBytes"), finalize.index("CloseHandle(p r1)"))
        self.assertNotRegex(finalize, r"(?m)^\s*(?:RMDir|Delete)\s")

    def test_runtime_registry_cleanup_is_exact_install_bound_and_shared_keys_survive(self):
        cleanup = self.function("un.RemoveOwnedRuntimeRegistry")
        self.assertIn('!define LAUNCHER_KEY "SOFTWARE\\PrivacyBrowser\\PrivacyBrowser\\Launcher"', cleanup)
        self.assertIn('!define SKELETON_KEY "SOFTWARE\\PrivacyBrowser\\PrivacyBrowser\\PreXULSkeletonUISettings"', cleanup)
        expected = {
            "LAUNCHER_KEY": {"Launcher", "Browser", "Image", "Telemetry", "Blocklist", "LauncherCrashTime"},
            "SKELETON_KEY": {"Enabled", "ScreenX", "ScreenY", "Width", "Height", "Maximized", "UrlbarCSSSpan",
                             "CssToDevPixelScaling", "SearchbarCSSSpan", "SpringsCSSSpan", "Theme", "Flags", "Progress"},
        }
        entries = re.findall(r'DeleteRegValue HKCU "\$\{(\w+)\}" "\$INSTDIR\\privacybrowser\.exe\|([^"\r\n]+)"', cleanup)
        self.assertEqual(len(entries), sum(map(len, expected.values())))
        self.assertEqual(cleanup.count("DeleteRegValue"), len(entries))
        for key, suffixes in expected.items():
            self.assertEqual({value for entry, value in entries if entry == key}, suffixes)
        key_deletions = [line.strip() for line in cleanup.splitlines() if line.strip().startswith("DeleteRegKey ")]
        self.assertEqual(len(key_deletions), 4)
        for line in key_deletions:
            self.assertTrue(line.startswith("DeleteRegKey /ifempty HKCU "), line)
        self.assertNotRegex(cleanup, r"(?i)EnumReg|ReadReg|Mozilla|Firefox|\*|\?")
        verified = re.findall(r'!insertmacro VerifyRemovedRuntimeValue "\$\{(\w+)\}" "([^"\r\n]+)"', cleanup)
        self.assertCountEqual(verified, entries)
        self.assertEqual(cleanup.count("Call un.RequireRegistryAbsentOrNonempty"), len(key_deletions))
        readback = self.function("un.RequireRegistryValueAbsent")
        self.assertIn("RegQueryValueExW(p r2, w r1, p 0, p 0, p 0, p 0)", readback)
        self.assertRegex(readback, r"\$\{If\} \$3 != 2\s+Call un.DataRefusal")
        key_readback = self.function("un.RequireRegistryAbsentOrNonempty")
        self.assertIn("RegQueryInfoKeyW", key_readback)
        self.assertRegex(key_readback, r"\$\{If\} \$3 == 0\s+\$\{AndIf\} \$4 == 0\s+Call un.DataRefusal")

    def test_native_handle_open_refuses_reparse_in_any_path_component(self):
        self.assertIn("!if ${NSIS_PTR_SIZE} != 4", self.commands)
        opener = self.function("${PREFIX}OpenNoReparse")
        self.assertIn('StrCpy $0 "\\??\\UNC\\$0"', opener)
        self.assertIn('StrCpy $0 "\\??\\$0"', opener)
        # OBJ_DONT_REPARSE | OBJ_CASE_INSENSITIVE is attached to the whole
        # native object path, rather than only its final component.
        self.assertIn("*(i 24, p 0, p r4, i 0x1040, p 0, p 0)", opener)
        self.assertIn("IntOp $8 $NativeAccess | 0x100000", opener)
        self.assertIn("IntOp $9 $NativeOptions | 0x00200020", opener)
        self.assertIn("ntdll::NtCreateFile(*p.r7, i r8, p r5, p r6, p 0, i 0, i $NativeShare, i $NativeDisposition, i r9, p 0, i 0)",
                      opener)
        self.assertIn("IntOp $9 $8 & 0x80000000", opener)
        self.assertIn("RtlNtStatusToDosError", opener)
        self.assertIn("StrCpy $7 -1", opener)
        self.assertNotIn("CreateFileW", opener)

    def test_tree_enumeration_uses_pinned_handles_and_bounded_unicode_records(self):
        walker = self.function("un.WalkOwnedTree")
        for information_class in (15, 14):
            self.assertIn(f"GetFileInformationByHandleEx(p r7, i {information_class}, p r6, i 65536)", walker)
        self.assertNotRegex(walker, r"(?i)FindFirstFile|FindNextFile|FindClose")
        for check in ("${If} $8 > 510", "${OrIf} $8 == 0", "IntOp $5 $8 & 1",
                      "${If} $5 > 65536", "${OrIf} $5 > 65466", "StrCpy $2 $2 $8"):
            self.assertIn(check, walker)
        self.assertLess(walker.index("Call un.OpenSafeDirectory"), walker.index("GetFileInformationByHandleEx"))
        self.assertGreater(walker.index("CloseHandle(p r7)"), walker.rindex("GetFileInformationByHandleEx"))
        self.assertRegex(walker, r"\$\{If\} \$TreeMode == 2\s+Push \$3\s+Call un.RemoveEmptyDirectory")

    def test_empty_directory_removal_preserves_unknown_payload_contents(self):
        removal = self.function("un.RemoveEmptyDirectory")
        for statement in ("StrCpy $NativeAccess 0x10081", "StrCpy $NativeShare 3",
                          "StrCpy $NativeDisposition 1", "StrCpy $NativeOptions 1", "Call un.OpenNoReparse"):
            self.assertIn(statement, removal)
        self.assertIn("IntOp $2 $3 & 0x400", removal)
        self.assertIn("Call un.DeleteOpenedObject", removal)
        self.assertRegex(removal, r"\$\{If\} \$OptionalDirectoryRemoval == 1\s+\$\{AndIf\} \$3 == 145\s+Goto empty_directory_done")
        optional = self.function("un.RemovePayloadDirectory")
        self.assertRegex(optional, r"StrCpy \$OptionalDirectoryRemoval 1\s+Call un.RemoveEmptyDirectory\s+StrCpy \$OptionalDirectoryRemoval 0")
        payload_file = self.function("un.DeletePayloadFile")
        self.assertRegex(payload_file, r"Call un.OpenSafeFile\s+Pop \$1\s+Push \$1\s+Call un.DeleteOpenedObject")
        self.assertRegex(payload_file, r"\$\{If\} \$2 == 0\s+StrCpy \$R9 1")

    def test_shortcut_deletion_requires_exact_install_time_snapshot_bytes(self):
        install = self.commands.split('Section "PrivacyBrowser"', 1)[1].split("SectionEnd", 1)[0]
        for current, snapshot_name in (("$DESKTOP\\PrivacyBrowser.lnk", "PrivacyBrowser.desktop-link"),
                                       ("$SMPROGRAMS\\PrivacyBrowser\\PrivacyBrowser.lnk", "PrivacyBrowser.program-link")):
            self.assertIn(f'CreateShortcut "$INSTDIR\\{snapshot_name}"', install)
            self.assertRegex(install, re.escape(f'StrCpy $0 "$INSTDIR\\{snapshot_name}"') +
                             r"\s+" + re.escape(f'StrCpy $1 "{current}"') + r"\s+Call CopyNewShortcut")
        self.assertLess(install.rindex("CreateShortcut "), install.index("Call CopyNewShortcut"))
        self.assertNotIn("CopyFileW", self.commands)
        copier = self.function("${PREFIX}CopyNewShortcut")
        self.assertRegex(copier, r"StrCpy \$NativeAccess 0x80000000\s+StrCpy \$NativeShare 1\s+StrCpy \$NativeDisposition 1\s+StrCpy \$NativeOptions 0x40\s+Push \$0\s+Call \$\{PREFIX\}OpenNoReparse\s+Pop \$2")
        self.assertRegex(copier, r"StrCpy \$NativeAccess 0x40000000\s+StrCpy \$NativeShare 0\s+StrCpy \$NativeDisposition 2\s+StrCpy \$NativeOptions 0x40\s+Push \$1\s+Call \$\{PREFIX\}OpenNoReparse\s+Pop \$3")
        self.assertIn("ReadFile(p r2, p r4, i 4096, *i.r5, p 0)", copier)
        self.assertIn("WriteFile(p r3, p r4, i r5, *i.r7, p 0)", copier)
        self.assertRegex(copier, r"\$\{OrIf\} \$7 != \$5\s+Call \$\{PREFIX\}DataRefusal")
        self.assertIn("Goto copy_shortcut_read", copier)
        shortcut = self.function("un.RemoveOwnedShortcut")
        self.assertRegex(shortcut, r"Push \$0\s+Call un.OpenSafeFile\s+Pop \$2\s+Push \$1\s+Call un.OpenSafeFile\s+Pop \$3")
        self.assertIn("ReadFile(p r2, p r4, i 4096, *i.r6, p 0)", shortcut)
        self.assertIn("ReadFile(p r3, p r5, i 4096, *i.r7, p 0)", shortcut)
        self.assertIn("${OrIf} $6 != $7", shortcut)
        self.assertIn("msvcrt::memcmp(p r4, p r5, i r6) i.r8 ?c", shortcut)
        self.assertRegex(shortcut, r"\$\{If\} \$8 != 0\s+Call un.DataRefusal")
        self.assertRegex(shortcut, r"Push \$2\s+Call un.DeleteOpenedObject\s+Pop \$8")
        self.assertLess(shortcut.index("memcmp"), shortcut.index("Call un.DeleteOpenedObject"))
        preflight = shortcut.split("${If} $ShortcutDelete == 0", 1)[1].split("${EndIf}", 1)[0]
        self.assertIn("CloseHandle(p r2)", preflight)
        self.assertIn("Goto shortcut_done", preflight)
        self.assertNotIn("Call un.DeleteOpenedObject", preflight)
        self.assertLess(shortcut.index("Call un.DeleteOpenedObject"), shortcut.rindex("CloseHandle(p r2)"))
        uninstall = self.commands.split('Section "Uninstall"', 1)[1]
        self.assertEqual(uninstall.count("Call un.RemoveOwnedShortcut"), 4)
        self.assertLess(uninstall.index("Call un.LockShortcutParents"), uninstall.index("Call un.RemoveOwnedShortcut"))
        self.assertLess(uninstall.rindex("Call un.RemoveOwnedShortcut"), uninstall.index("!insertmacro RemoveNativePayload"))
        purge = uninstall.index("Call un.PurgeOwnedData")
        preflight = uninstall[uninstall.index("StrCpy $ShortcutDelete 0"):purge]
        deletion = uninstall[uninstall.index("StrCpy $ShortcutDelete 1"):]
        self.assertEqual(preflight.count("Call un.RemoveOwnedShortcut"), 2)
        self.assertEqual(deletion.count("Call un.RemoveOwnedShortcut"), 2)
        self.assertLess(purge, uninstall.index("StrCpy $ShortcutDelete 1"))

    def test_missing_executable_retry_requires_cleanup_intent_and_empty_inventory(self):
        guard = self.function("un.CheckBrowserClosed")
        self.assertRegex(guard, r'IfFileExists "\$INSTDIR\\privacybrowser.exe" browser_present\s+Call un.VerifyCleanupIntent\s+!insertmacro VerifyNativePayloadAbsent\s+Return\s+browser_present:')
        registration = self.function("${PREFIX}VerifyRegistration")
        missing_registration = registration.split('${If} $0 == ""', 1)[1].split("StrCmpS $0", 1)[0]
        self.assertIn('${AndIf} $1 == ""', missing_registration)
        self.assertIn("RegOpenKeyExW", missing_registration)
        self.assertIn("${ElseIf} $1 != 2", missing_registration)
        self.assertRegex(missing_registration, r"Call \$\{PREFIX\}VerifyCleanupIntent\s+Return")
        intent = self.function("${PREFIX}EnsureCleanupIntent")
        self.assertIn('StrCpy $0 "$INSTDIR\\PrivacyBrowser.cleanup-intent"', intent)
        self.assertIn('StrCpy $1 "PrivacyBrowser-Cleanup-v1"', intent)
        self.assertRegex(intent, r"Call \$\{PREFIX\}WriteNewAsciiFile\s+intent_exists:\s+Call \$\{PREFIX\}VerifyCleanupIntent")
        writer = self.function("${PREFIX}WriteNewAsciiFile")
        self.assertIn("StrCpy $NativeDisposition 2", writer)
        self.assertIn("Call ${PREFIX}OpenNoReparse", writer)
        self.assertIn("${OrIf} $3 != $4", writer)
        uninstall = self.commands.split('Section "Uninstall"', 1)[1]
        order = [uninstall.index(statement) for statement in ("Call un.RemoveOwnedRuntimeRegistry",
                 "Call un.EnsureCleanupIntent", "!insertmacro RemoveNativePayload", "Call un.FinalizeMetadata")]
        self.assertEqual(order, sorted(order))

    def test_fixed_recovery_protocols_require_exact_ascii_from_no_follow_handle(self):
        intent = self.function("${PREFIX}VerifyCleanupIntent")
        self.assertIn('StrCpy $0 "$INSTDIR\\PrivacyBrowser.cleanup-intent"', intent)
        self.assertIn('StrCpy $1 "PrivacyBrowser-Cleanup-v1"', intent)
        self.assertIn("Call ${PREFIX}VerifyAsciiFile", intent)
        reader = self.function("${PREFIX}VerifyAsciiFile")
        for statement in ("StrCpy $NativeAccess 0x80000000", "StrCpy $NativeShare 1",
                          "StrCpy $NativeDisposition 1", "StrCpy $NativeOptions 0x40", "Call ${PREFIX}OpenNoReparse",
                          "GetFileSizeEx(p r2, *l.r5)", "ReadFile(p r2, p r3, i r4, *i.r5, p 0)"):
            self.assertIn(statement, reader)
        self.assertEqual(reader.count("${OrIf} $5 != $4"), 2)
        self.assertRegex(reader, r"StrCmpS \$7 \$1 \+2\s+Call \$\{PREFIX\}DataRefusal")
        self.assertNotIn("FileOpen", reader)

    def test_final_metadata_deletion_retains_recoverable_markers_on_each_failure(self):
        finalize = self.function("un.FinalizeMetadata")
        self.assertLess(finalize.index("Call un.VerifyRegistration"), finalize.index("DeleteRegKey HKCU"))
        self.assertIn("RegOpenKeyExW", finalize)
        self.assertIn("${ElseIf} $1 != 2", finalize)
        first_delete = finalize.index("Call un.DeleteOpenedObject")
        for name, handle in (("uninstall.exe", "6"), ("PrivacyBrowser.install-marker", "7"),
                             ("PrivacyBrowser.cleanup-intent", "8")):
            self.assertRegex(finalize[:first_delete], rf'Push "\$INSTDIR\\{re.escape(name)}"\s+Call un.OpenSafeFile\s+Pop \${handle}')
        self.assertEqual(finalize.count("Call un.DeleteOpenedObject"), 3)
        self.assertEqual(finalize.count("Call un.RestoreRecoveryMarkers"), 3)
        for failure in re.findall(r"(?s)\$\{If\} \$9 == 0\s+(.*?)\$\{EndIf\}", finalize):
            self.assertIn("Call un.RestoreRecoveryMarkers", failure)
            self.assertIn("Call un.DataRefusal", failure)
        recovery = self.function("un.RestoreRecoveryMarkers")
        self.assertIn('IfFileExists "$INSTDIR\\PrivacyBrowser.install-marker" app_marker_present', recovery)
        self.assertIn('StrCpy $1 "PrivacyBrowser 0.1.0-dev"', recovery)
        self.assertIn("Call un.WriteNewAsciiFile", recovery)
        self.assertIn("Call un.EnsureCleanupIntent", recovery)
        self.assertNotRegex(recovery, r"(?im)^\s*(?:Delete|RMDir|WriteReg\w+)\s")

    def test_synthetic_fixture_is_compile_time_bound_and_hosted_only(self):
        header = self.commands.split('!ifndef RUNTIME_DIR', 1)[0]
        self.assertRegex(header, r'!ifdef PB_SYNTHETIC_FIXTURE\s+!if "\$\{PB_SYNTHETIC_FIXTURE\}" != "1"\s+!error')
        self.assertIn('!if "${PB_SYNTHETIC_ROOT}" != "D:\\pb\\installer-syntax\\execution"', header)
        self.assertRegex(header, r"!ifndef PB_SYNTHETIC_INCLUDE\s+!error")
        self.assertRegex(self.commands, r'!ifdef PB_SYNTHETIC_FIXTURE\s+!include "\$\{PB_SYNTHETIC_INCLUDE\}"\s+!endif')
        environment = self.function("${PREFIX}RequireSyntheticEnvironment")
        for statement in ('ReadEnvStr $0 "GITHUB_ACTIONS"', 'ReadEnvStr $1 "RUNNER_ENVIRONMENT"',
                          'StrCmpS $0 "true" +2', 'StrCmpS $1 "github-hosted" +2',
                          'StrCmpS $EXEDIR "${PB_SYNTHETIC_ROOT}" fixture_directory_ok',
                          'StrCmpS $EXEDIR "${PB_SYNTHETIC_ROOT}\\app" fixture_directory_ok'):
            self.assertIn(statement, environment)
        for prefix in ("", "un."):
            initializer = self.function(prefix + "onInit" if prefix else ".onInit")
            fixture = initializer.split("!else", 1)[0]
            self.assertIn("!ifdef PB_SYNTHETIC_FIXTURE", fixture)
            self.assertRegex(fixture, rf"Call {re.escape(prefix)}RequireSyntheticEnvironment\s+Call {re.escape(prefix)}NativeSafetyFixtureMain\s+Quit")
        roots = self.function("${PREFIX}InitializeDataPaths").split("!ifdef PB_SYNTHETIC_FIXTURE", 1)[1].split("!else", 1)[0]
        self.assertRegex(roots, r'StrCmpS \$INSTDIR "\$\{PB_SYNTHETIC_ROOT\}\\app" \+2\s+Call \$\{PREFIX\}DataRefusal')
        self.assertRegex(roots, r'Push "\$\{PB_SYNTHETIC_ROOT\}\\local\\PrivacyBrowser"\s+Call \$\{PREFIX\}CanonicalizePath\s+Pop \$LocalDataRoot')
        self.assertRegex(roots, r'Push "\$\{PB_SYNTHETIC_ROOT\}\\roaming\\PrivacyBrowser"\s+Call \$\{PREFIX\}CanonicalizePath\s+Pop \$RoamingDataRoot')

    def test_synthetic_fixture_compiles_out_production_install_uninstall_and_launch(self):
        launch = re.search(r"(?ms)^!ifndef PB_SYNTHETIC_FIXTURE\nFunction LaunchPrivacyBrowser\n(.*?)^!else\nSection \"Synthetic safety fixture\"\nSectionEnd\n!endif", self.commands)
        self.assertIsNotNone(launch)
        self.assertIn('Section "PrivacyBrowser"', launch.group(1))
        self.assertIn("!insertmacro InstallPayload", launch.group(1))
        self.assertRegex(self.commands, r"(?ms)^!ifndef PB_SYNTHETIC_FIXTURE\n!define MUI_FINISHPAGE_RUN\n.*?^!endif")
        uninstall = re.search(r'(?ms)^!ifndef PB_SYNTHETIC_FIXTURE\nSection "Uninstall"\n(.*?)^!else\nSection "Uninstall"\nSectionEnd\n!endif', self.commands)
        self.assertIsNotNone(uninstall)
        self.assertIn("Call un.PurgeOwnedData", uninstall.group(1))
        self.assertIn("!insertmacro RemoveNativePayload", uninstall.group(1))

    def test_synthetic_diagnostics_are_bounded_stdout_only_and_preserve_registers(self):
        self.assertRegex(self.commands, r"(?ms)^!ifdef PB_SYNTHETIC_FIXTURE\nFunction \$\{PREFIX\}TraceSyntheticState\n.*?^FunctionEnd\n!endif")
        trace = self.function("${PREFIX}TraceSyntheticState")
        self.assertIn('${If} $SyntheticTraceCount >= 64', trace)
        self.assertIn('${AndIf} $SyntheticEvent != "refusal"', trace)
        self.assertIn("kernel32::GetStdHandle(i -11) p.r0", trace)
        self.assertIn("kernel32::WriteFile(p r0, m r1, i r2, *i.r3, p 0) i.r4", trace)
        for field in ("phase=$SyntheticPhase", "event=$SyntheticEvent", "ntstatus=$SyntheticNtStatus",
                      "native_error=$NativeError", "path_chars=$SyntheticPathCharacters"):
            self.assertIn(field, trace)
        self.assertEqual(re.findall(r"(?m)^\s*Push (\$\d)$", trace), [f"${index}" for index in range(5)])
        self.assertEqual(re.findall(r"(?m)^\s*Pop (\$\d)$", trace), [f"${index}" for index in reversed(range(5))])
        self.assertNotRegex(trace, r"(?i)CreateFile|FileOpen|OpenNoReparse|WriteReg|Delete|CloseHandle|DataRefusal")
        refusal = self.function("${PREFIX}DataRefusal")
        self.assertRegex(refusal, r'!ifdef PB_SYNTHETIC_FIXTURE\s+StrCpy \$SyntheticEvent "refusal"\s+Call \$\{PREFIX\}TraceSyntheticState\s+!endif')
        opener = self.function("${PREFIX}OpenNoReparse")
        self.assertRegex(opener, r"i\.r8'\s+!ifdef PB_SYNTHETIC_FIXTURE\s+StrCpy \$SyntheticNtStatus \$8\s+!endif")
        self.assertLess(opener.index("StrCpy $SyntheticNtStatus $8"), opener.index("RtlNtStatusToDosError"))
        self.assertRegex(opener, r'!ifdef PB_SYNTHETIC_FIXTURE\s+StrCpy \$SyntheticEvent "nt-open"\s+Call \$\{PREFIX\}TraceSyntheticState\s+!endif')

    def test_synthetic_init_diagnostics_identify_each_phase_before_work(self):
        fixture = (ROOT / "packaging/windows/native-safety-fixture.nsh").read_text(encoding="utf-8")
        for prefix, phases in (("", ("ordinary-unicode-open", "ordinary-unicode-close", "initialize-data-paths",
                                    "create-fresh-application-directory", "set-application-output-path",
                                    "verify-application-current-directory",
                                    "ensure-owned-local-root", "ensure-owned-roaming-root",
                                    "write-synthetic-uninstaller", "initialization-complete")),
                               ("un.", ("uninstall-initialize-data-paths", "uninstall-lock-application",
                                        "uninstall-verify-local-root", "uninstall-verify-roaming-root",
                                        "uninstall-purge-owned-data", "uninstall-complete"))):
            self.assertEqual(re.findall(r'!insertmacro SyntheticPhase "' + re.escape(prefix) + r'" "([a-z-]+)"', fixture), list(phases))
        self.assertLess(fixture.index('"ordinary-unicode-open"'), fixture.index("Call OpenNoReparse"))
        self.assertLess(fixture.index('"initialize-data-paths"'), fixture.index("Call InitializeDataPaths"))
        self.assertLess(fixture.index('"uninstall-purge-owned-data"'), fixture.index("Call un.PurgeOwnedData"))


class HostedInstallerTestContractTests(unittest.TestCase):
    """Inspect the hosted-only PowerShell test as text; never invoke it here."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "tools/test_native_installer.ps1").read_text(encoding="utf-8")
        cls.commands = "\n".join(line for line in cls.source.splitlines() if not line.lstrip().startswith("#"))

    def test_hosted_windows_and_fresh_dedicated_paths_are_required_before_execution(self):
        for guard in ("$env:GITHUB_ACTIONS -eq 'true'", "$env:RUNNER_ENVIRONMENT -eq 'github-hosted'",
                      "Assert-True ($IsWindows)", "$WorkRoot -ceq 'D:\\pb\\installer-test'",
                      "-not (Test-Path -LiteralPath $WorkRoot)", "-not (Test-Path -LiteralPath $Output)"):
            self.assertIn(guard, self.commands)
            self.assertLess(self.commands.index(guard), self.commands.index("$exitCode = Run-Bounded $Installer"))
        self.assertIn("@($profileRoot, $roamingRoot, $desktopLink, $programFolder, $registry)", self.commands)
        self.assertIn("$profileRoot = Join-Path $env:LOCALAPPDATA 'PrivacyBrowser'", self.commands)
        self.assertIn("$roamingRoot = Join-Path $env:APPDATA 'PrivacyBrowser'", self.commands)
        self.assertIn("[IO.FileMode]::CreateNew", self.commands)

    def test_source_candidate_identity_and_hashes_are_checked_before_install(self):
        position = self.commands.index("$exitCode = Run-Bounded $Installer")
        for check in ("$proof.sha256 -ceq $installerHash", "$proof.native_binary_sha256 -ceq $nativeHash",
                      "$proof.build_kind -ceq 'full-source-build-candidate-installer'",
                      "$proof.release_eligible -eq $false", "$proof.installer_static_validation.installer_static_identity_passed -eq $true"):
            self.assertLess(self.commands.index(check), position)

    def test_hosted_test_cleans_only_exact_synthetic_fixtures_without_security_changes(self):
        self.assertNotRegex(self.commands, r"(?im)^\s*(?:Remove-Item|Clear-Content|Set-MpPreference|Add-MpPreference|Set-ExecutionPolicy|Set-NetFirewall\w*|Disable-NetFirewall\w*)\b")
        self.assertNotRegex(self.commands, r"(?i)MOZ_DISABLE_CONTENT_SANDBOX|security\.sandbox|fission\.autostart|xpinstall\.signatures\.required|--no-sandbox")
        self.assertNotRegex(self.commands, r"(?i)Stop-Process|taskkill|\.Save\(")
        # Only these single-argument .NET calls may clean up this test's
        # own exact fixtures. In particular Directory.Delete must never gain
        # its recursive overload or target a profile/data root.
        cleanup = re.findall(r"(?i)\[(?:System\.)?IO\.(File|Directory)\]::Delete\(([^\r\n]*)\)", self.commands)
        self.assertCountEqual(cleanup, [("File", "$unknown"), ("Directory", "$installDir"),
                                       ("Directory", "$junction")])
        self.assertIn("$unknown = Join-Path $installDir 'retained-unknown-fixture.txt'", self.commands)
        self.assertIn("$installDir = Join-Path $WorkRoot 'PrivacyBrowser-日本語'", self.commands)
        self.assertIn("$junction = Join-Path $profile 'outside-junction'", self.commands)
        self.assertIn("New-Item -ItemType Junction -Path $junction -Target $outside", self.commands)
        self.assertLess(self.commands.index("$checks.default_silent_retains_user_data = $true"),
                        self.commands.index("[IO.File]::Delete($unknown)"))
        self.assertLess(self.commands.index("[IO.File]::Delete($unknown)"),
                        self.commands.index("[IO.Directory]::Delete($installDir)"))
        self.assertIn("$process.Kill($true)", self.commands)
        self.assertIn("$browser.Kill($true)", self.commands)
        self.assertIn("$process = Start-Process", self.commands)
        self.assertIn("$browser = Start-Process", self.commands)

    def test_active_and_locked_executable_refusals_are_reported_separately(self):
        self.assertIn("[IO.File]::Open($exe, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)", self.commands)
        self.assertIn("$browserProfile = Join-Path $WorkRoot 'active-browser-profile'", self.commands)
        self.assertIn("'--profile', $browserProfile, 'about:blank'", self.commands)
        self.assertEqual(self.commands.count("Assert-True ($exitCode -ne 0)"), 2)
        for check in ("locked_payload_uninstall_refused", "active_browser_uninstall_refused"):
            self.assertIn(f"$checks.{check} = $true", self.commands)

    def test_active_browser_logs_are_retained_without_uploading_profiles(self):
        for variable, filename in (("$browserStdout", "active-browser.stdout.log"),
                                   ("$browserStderr", "active-browser.stderr.log")):
            self.assertIn(f"{variable} = Join-Path (Split-Path -Parent $Output) '{filename}'", self.commands)
        self.assertIn("-RedirectStandardOutput $browserStdout -RedirectStandardError $browserStderr", self.commands)
        self.assertLess(self.commands.index("-not (Test-Path -LiteralPath $log)"),
                        self.commands.index("$browser = Start-Process"))
        workflow = (ROOT / ".github/workflows/windows-native.yml").read_text(encoding="utf-8")
        self.assertIn("path: D:/pb/evidence/", workflow)
        self.assertNotIn("path: D:/pb/installer-test", workflow)

    def test_keep_then_explicit_purge_are_verified_without_claiming_full_gui_acceptance(self):
        for check in ("exact_payload_hashes", "uninstall_exact_payload_and_metadata",
                      "profile_download_unknown_file_retention", "default_silent_retains_user_data",
                      "reinstall_preserves_owned_profile", "unicode_owned_root_markers",
                      "explicit_purge_owned_profile_cache_and_registry",
                      "foreign_files_profiles_downloads_and_registry_retained", "association_state_unchanged"):
            self.assertIn(f"$checks.{check} = $true", self.commands)
        keep = self.commands.index("$exitCode = Run-Bounded $uninstaller @('/S') 120")
        keep_verified = self.commands.index("$checks.default_silent_retains_user_data = $true")
        reinstall = self.commands.index("$checks.reinstall_preserves_owned_profile = $true")
        purge = self.commands.index("$exitCode = Run-Bounded $uninstaller @('/S', '/PURGEUSERDATA') 120")
        purge_verified = self.commands.index("$checks.explicit_purge_owned_profile_cache_and_registry = $true")
        self.assertLess(keep, keep_verified)
        self.assertLess(keep_verified, reinstall)
        self.assertLess(reinstall, purge)
        self.assertLess(purge, purge_verified)
        self.assertIn("@($profileRoot, $roamingRoot, $marker, $uninstaller, $registry, $desktopLink, $programLink)",
                      self.commands[purge:purge_verified])
        self.assertIn("$expectedOwnership = \"PrivacyBrowser-UserData-v1`r`n$installDir`r`n$root`r`n\"", self.commands)
        self.assertIn("[IO.File]::ReadAllText($ownership, [Text.Encoding]::Unicode) -ceq $expectedOwnership",
                      self.commands)
        self.assertIn("(File-Hash $profileFixture) -ceq $profileHash", self.commands)
        self.assertIn("(File-Hash $cacheFixture) -ceq $cacheHash", self.commands[keep:keep_verified])
        self.assertIn("(File-Hash $roamingFixture) -ceq $roamingHash", self.commands[keep:keep_verified])
        self.assertIn("(File-Hash $downloadFixture) -ceq $downloadHash", self.commands)
        self.assertIn("(File-Hash $unknown) -ceq $unknownHash", self.commands)
        self.assertIn("(File-Hash $outsideFile) -ceq $outsideHash", self.commands[purge:purge_verified])
        self.assertIn("(Test-Path -LiteralPath $browserProfile)", self.commands[purge:purge_verified])
        self.assertIn("(Association-Snapshot) -ceq $beforeAssociations", self.commands)
        for field in ("interactive_installer_gui_tested", "normal_browsing_ime_tested", "release_eligible"):
            self.assertRegex(self.commands, rf"(?m)^\s*{field} = \$false$")

    def test_corrupt_marker_junction_and_locked_cache_refuse_before_final_purge(self):
        purge = self.commands.index("$exitCode = Run-Bounded $uninstaller @('/S', '/PURGEUSERDATA') 120")
        refusals = ("ownership_marker_purge_refused", "reparse_purge_refused", "locked_user_data_purge_retry_safe")
        for check in refusals:
            self.assertLess(self.commands.index(f"$checks.{check} = $true"), purge)
        self.assertEqual(self.commands.count("$exitCode = Run-PurgeRefusal"), 3)
        self.assertIn("$negativeUninstaller = Join-Path $WorkRoot 'negative-case-uninstall.exe'", self.commands)
        self.assertIn("Assert-True (-not (Test-Path -LiteralPath $negativeUninstaller))", self.commands)
        self.assertIn("[IO.File]::Copy($uninstaller, $negativeUninstaller, $false)", self.commands)
        self.assertIn("$uninstallerHash = File-Hash $uninstaller", self.commands)
        probe = self.commands.split("function Run-PurgeRefusal {", 1)[1].split("\n}", 1)[0]
        self.assertIn("(File-Hash $negativeUninstaller) -ceq $uninstallerHash -and (File-Hash $uninstaller) -ceq $uninstallerHash", probe)
        self.assertIn("return (Run-Bounded $negativeUninstaller @('/S', '/PURGEUSERDATA', ('_?=' + $installDir)) 30)", probe)
        self.assertNotIn("Run-Bounded $uninstaller @('/S', '/PURGEUSERDATA', ('_?=' + $installDir))", self.commands)
        refusal_assertion = "Assert-True ($exitCode -ne 0 -and (Test-Path -LiteralPath $marker) -and (Test-Path -LiteralPath $registry)"
        self.assertEqual(self.commands.count(refusal_assertion), 3)
        self.assertIn("$ownerBytes = [IO.File]::ReadAllBytes($ownerMarker)", self.commands)
        self.assertIn("[IO.File]::WriteAllBytes($ownerMarker, [Text.Encoding]::UTF8.GetBytes('foreign ownership fixture'))",
                      self.commands)
        self.assertIn("finally { [IO.File]::WriteAllBytes($ownerMarker, $ownerBytes) }", self.commands)
        self.assertIn("$cacheHandle = [IO.File]::Open($cacheFixture, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)",
                      self.commands)
        self.assertIn("finally { $cacheHandle.Dispose() }", self.commands)
        locked = self.commands.split("$cacheHandle = ", 1)[1].split("$checks.locked_user_data_purge_retry_safe", 1)[0]
        self.assertIn("(Test-Path -LiteralPath $uninstaller)", locked)
        self.assertIn("(File-Hash $exe) -ceq $nativeHash", locked)
        self.assertIn("(File-Hash $cacheFixture) -ceq $cacheHash", locked)

    def test_explicit_purge_checks_exact_registry_values_and_foreign_sentinels(self):
        self.assertIn("$launcherKey = 'HKCU:\\Software\\PrivacyBrowser\\PrivacyBrowser\\Launcher'", self.commands)
        self.assertIn("$skeletonKey = 'HKCU:\\Software\\PrivacyBrowser\\PrivacyBrowser\\PreXULSkeletonUISettings'",
                      self.commands)
        self.assertIn("$firefoxKey = 'HKCU:\\Software\\Mozilla\\Firefox\\PrivacyBrowserUninstallFixture'", self.commands)
        for suffixes, key in (("launcherSuffixes", "launcherKey"), ("skeletonSuffixes", "skeletonKey")):
            self.assertIn(f"foreach ($suffix in ${suffixes}) {{ New-ItemProperty -LiteralPath ${key} -Name ($exe + $suffix)",
                          self.commands)
            self.assertIn(f"foreach ($suffix in ${suffixes}) {{ Assert-True ($null -eq (Get-Item -LiteralPath ${key}).GetValue($exe + $suffix, $null))",
                          self.commands)
        self.assertIn("$foreignName = 'D:\\pb\\foreign-browser\\privacybrowser.exe|Launcher'", self.commands)
        self.assertIn("(Get-Item -LiteralPath $launcherKey).GetValue($foreignName) -ceq 'foreign installation'", self.commands)
        for key in ("launcherKey", "skeletonKey"):
            self.assertIn(f"(Get-Item -LiteralPath ${key}).GetValue('UnrelatedSentinel') -ceq 'shared value'", self.commands)
        self.assertIn("(Get-Item -LiteralPath $firefoxKey).GetValue('Sentinel') -ceq 'Firefox data must remain'", self.commands)


if __name__ == "__main__":
    unittest.main()
