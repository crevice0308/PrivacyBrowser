"""Inert fixtures and mocked compiler only; never execute NSIS or a setup."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import check_native_installer_syntax as syntax
from native_package_test import pe_fixture, resources_fixture


class NativeInstallerSyntaxTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        (self.work / "evidence").mkdir()
        self.compiler = self.work / syntax.COMPILER_RELATIVE
        self.compiler.parent.mkdir(parents=True)
        self.compiler.write_bytes(b"inert compiler fixture; subprocess execution is mocked")
        self.compiler_hash = hashlib.sha256(self.compiler.read_bytes()).hexdigest()
        self.record = {"source_url": syntax.NSIS_URL, "archive_sha256": syntax.NSIS_SHA256,
                       "compiler": syntax.COMPILER_RELATIVE, "nsis_directory": syntax.NSIS_DIRECTORY,
                       "compiler_sha256": self.compiler_hash}
        self.record_path = self.work / "evidence/nsis-toolchain.json"
        self.record_path.write_text(json.dumps(self.record))
        patch = mock.patch.object(syntax, "COMPILER_SHA256", self.compiler_hash)
        patch.start()
        self.addCleanup(patch.stop)

    def fake_compile(self, command, **kwargs):
        self.assertEqual(command[0], str(self.compiler))
        self.assertEqual(kwargs["timeout"], 120)
        self.assertEqual(kwargs["env"]["NSISDIR"], str(self.work / syntax.NSIS_DIRECTORY))
        output_argument = next(value for value in command if "DOUTPUT_FILE=" in value)
        output = Path(output_argument.split("=", 1)[1])
        self.assertEqual(output.parent, self.work / "installer-syntax/DO-NOT-RUN-inert-installer-syntax")
        self.assertEqual(output.name, syntax.native.SETUP_NAME)
        fields = {"ProductName": "PrivacyBrowser Setup", "FileDescription": "PrivacyBrowser Installer",
                  "InternalName": "PrivacyBrowser Setup", "OriginalFilename": syntax.native.SETUP_NAME,
                  "CompanyName": "PrivacyBrowser contributors", "FileVersion": "0.1.0-dev", "ProductVersion": "0.1.0-dev"}
        output.write_bytes(pe_fixture(resources_fixture(fields, engine="0.1.0", structure_version=0), machine=0x14C))
        kwargs["stdout"].write("Mocked compiler success, not an actual compilation.\n")
        return subprocess.CompletedProcess(command, 0)

    def report(self):
        return json.loads((self.work / "evidence/installer-syntax.json").read_text())

    def test_compiler_only_preflight_with_inert_payload(self):
        with mock.patch.object(syntax.subprocess, "run", side_effect=self.fake_compile) as run:
            report = syntax.compile_syntax(self.work, self.compiler)
        run.assert_called_once()
        self.assertTrue(report["syntax_compiled"])
        self.assertTrue(report["installer_static_identity_passed"])
        self.assertTrue(report["installer_static_validation"]["installer_static_identity_passed"])
        self.assertEqual(report["installer_static_validation"]["sha256"], report["generated_installer_sha256"])
        self.assertFalse(report["generated_installer_executed"])
        self.assertFalse(report["generated_installer_uploaded"])
        self.assertFalse(report["browser_compiled"])
        self.assertFalse(report["release_eligible"])
        runtime = self.work / "installer-syntax/inert-runtime"
        self.assertEqual((runtime / "privacybrowser.exe").read_bytes(), syntax.FIXTURE_BYTES)
        self.assertFalse(syntax.FIXTURE_BYTES.startswith(b"MZ"))
        self.assertTrue((runtime / "LICENSE").is_file())
        self.assertTrue((runtime / "NOTICE").is_file())
        self.assertEqual((runtime / "distribution/privacybrowser/assets/privacybrowser.ico").read_bytes(),
                         (ROOT / "browser/privacy/assets/privacybrowser.ico").read_bytes())
        self.assertFalse((self.work / "artifact").exists())
        self.assertEqual(list((self.work / "evidence").glob("*.exe")), [])
        self.assertEqual(self.report()["generated_installer_sha256"], report["generated_installer_sha256"])

    def test_compiler_success_with_bad_wrapper_is_retained_and_blocks_synthetic_execution(self):
        def wrong_wrapper(command, **kwargs):
            result = self.fake_compile(command, **kwargs)
            output = Path(next(value.split("=", 1)[1] for value in command if "DOUTPUT_FILE=" in value))
            data = output.read_bytes().replace("PrivacyBrowser Setup".encode("utf-16le"),
                                             "AnotherBrowser Setup".encode("utf-16le"))
            output.write_bytes(data)
            return result
        with mock.patch.object(syntax.subprocess, "run", side_effect=wrong_wrapper), \
             self.assertRaisesRegex(ValueError, "static identity preflight"):
            syntax.compile_syntax(self.work, self.compiler)
        report = self.report()
        self.assertTrue(report["syntax_compiled"])
        self.assertFalse(report["installer_static_identity_passed"])
        self.assertIn("Unexpected installer native ProductName", report["installer_static_validation_error"])
        self.assertFalse(report["generated_installer_executed"])
        self.assertFalse(report["generated_installer_uploaded"])
        with mock.patch.object(syntax.subprocess, "run") as run, self.assertRaises(ValueError):
            syntax.compile_synthetic(self.work, self.compiler)
        run.assert_not_called()

    def test_zero_exit_mz_only_output_does_not_bypass_identity_check(self):
        def malformed(command, **kwargs):
            output = Path(next(value.split("=", 1)[1] for value in command if "DOUTPUT_FILE=" in value))
            output.write_bytes(b"MZ not a complete PE resource fixture")
            return subprocess.CompletedProcess(command, 0)
        with mock.patch.object(syntax.subprocess, "run", side_effect=malformed), \
             self.assertRaisesRegex(ValueError, "static identity preflight"):
            syntax.compile_syntax(self.work, self.compiler)
        self.assertTrue(self.report()["syntax_compiled"])
        self.assertFalse(self.report()["installer_static_identity_passed"])
        self.assertIn("installer_static_validation_error", self.report())

    def test_synthetic_compilation_requires_positive_identity_gate(self):
        self.compile_production_mock()
        for value in (False, None, "true", 1):
            report = self.report()
            report["installer_static_identity_passed"] = value
            (self.work / "evidence/installer-syntax.json").write_text(json.dumps(report))
            with self.subTest(value=value), mock.patch.object(syntax.subprocess, "run") as run, self.assertRaises(ValueError):
                syntax.compile_synthetic(self.work, self.compiler)
            run.assert_not_called()

    def test_compiler_failure_retains_log_and_failed_evidence(self):
        result = subprocess.CompletedProcess([str(self.compiler)], 1)
        with mock.patch.object(syntax.subprocess, "run", return_value=result), self.assertRaises(ValueError):
            syntax.compile_syntax(self.work, self.compiler)
        self.assertFalse(self.report()["syntax_compiled"])
        self.assertEqual(self.report()["exit_code"], 1)
        self.assertTrue((self.work / "evidence/installer-syntax.log").is_file())

    def test_timeout_retains_failed_evidence(self):
        with mock.patch.object(syntax.subprocess, "run", side_effect=subprocess.TimeoutExpired("mock-compiler", 120)), \
             self.assertRaises(ValueError):
            syntax.compile_syntax(self.work, self.compiler)
        self.assertTrue(self.report()["timed_out"])
        self.assertFalse(self.report()["syntax_compiled"])

    def test_zero_exit_without_generated_output_is_rejected(self):
        with mock.patch.object(syntax.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
             self.assertRaises(ValueError):
            syntax.compile_syntax(self.work, self.compiler)
        self.assertFalse(self.report()["syntax_compiled"])

    def test_wrong_compiler_hash_rejected_before_fixture_or_execution(self):
        self.compiler.write_bytes(b"changed compiler fixture")
        with mock.patch.object(syntax.subprocess, "run") as run, self.assertRaises(ValueError):
            syntax.compile_syntax(self.work, self.compiler)
        run.assert_not_called()
        self.assertFalse((self.work / "installer-syntax").exists())

    def test_arbitrary_compiler_path_is_rejected(self):
        different = self.work / "another-compiler.exe"
        different.write_bytes(self.compiler.read_bytes())
        with mock.patch.object(syntax.subprocess, "run") as run, self.assertRaises(ValueError):
            syntax.compile_syntax(self.work, different)
        run.assert_not_called()

    def test_existing_fixture_directory_is_preserved(self):
        directory = self.work / "installer-syntax"
        directory.mkdir()
        sentinel = directory / "keep.txt"
        sentinel.write_text("existing file")
        with mock.patch.object(syntax.subprocess, "run") as run, self.assertRaises(ValueError):
            syntax.compile_syntax(self.work, self.compiler)
        run.assert_not_called()
        self.assertEqual(sentinel.read_text(), "existing file")

    def test_preflight_runs_after_source_verification_and_before_gecko(self):
        script = (ROOT / "tools/build_windows_native.ps1").read_text()
        source = script.index("'tools\\prepare_source.py')")
        compiler = script.index("'--prepare-nsis')")
        preflight = script.index("'tools\\check_native_installer_syntax.py')")
        build = script.index("& cmd.exe /d /c $Launch")
        self.assertLess(source, compiler)
        self.assertLess(compiler, preflight)
        self.assertLess(preflight, build)
        workflow = (ROOT / ".github/workflows/windows-native.yml").read_text()
        self.assertNotIn("installer-syntax", workflow)
        self.assertIn("'--execute-fixtures'", script)

    def compile_production_mock(self):
        with mock.patch.object(syntax.subprocess, "run", side_effect=self.fake_compile):
            return syntax.compile_syntax(self.work, self.compiler)

    def fake_synthetic_compile(self, command, **kwargs):
        self.assertEqual(command[0], str(self.compiler))
        self.assertEqual(kwargs["timeout"], 120)
        self.assertIn("-DPB_SYNTHETIC_FIXTURE=1" if sys.platform != "win32" else "/DPB_SYNTHETIC_FIXTURE=1", command)
        self.assertTrue(any(value.endswith("DPB_SYNTHETIC_ROOT=" + syntax.synthetic.EXECUTION_WINDOWS) for value in command))
        output = Path(next(value.split("=", 1)[1] for value in command if "DOUTPUT_FILE=" in value))
        self.assertEqual(output, self.work / syntax.synthetic.EXECUTION_RELATIVE / syntax.synthetic.FIXTURE_EXE)
        output.write_bytes(b"MZmocked separate synthetic output")
        return subprocess.CompletedProcess(command, 0)

    def test_second_compilation_is_explicitly_synthetic_and_never_executes(self):
        original = self.compile_production_mock()
        with mock.patch.object(syntax.subprocess, "run", side_effect=self.fake_synthetic_compile) as run, \
             mock.patch.object(syntax.synthetic, "execute_synthetic") as execute:
            report = syntax.compile_synthetic(self.work, self.compiler)
        run.assert_called_once()
        execute.assert_not_called()
        self.assertTrue(report["synthetic_fixture_compiled"])
        self.assertFalse(report["generated_installer_executed"])
        self.assertFalse(report["release_eligible"])
        self.assertEqual(self.report(), original)
        include = self.work / "installer-syntax/synthetic-entry.nsh"
        self.assertTrue(include.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(include.read_text(encoding="utf-8-sig"),
                         (ROOT / "packaging/windows/native-safety-fixture.nsh").read_text(encoding="utf-8"))
        self.assertEqual(list((self.work / "evidence").glob("*.exe")), [])

    def test_synthetic_compilation_refuses_reused_execution_root(self):
        self.compile_production_mock()
        directory = self.work / syntax.synthetic.EXECUTION_RELATIVE
        directory.mkdir()
        (directory / "retain.txt").write_text("retain")
        with mock.patch.object(syntax.subprocess, "run") as run, self.assertRaises(ValueError):
            syntax.compile_synthetic(self.work, self.compiler)
        run.assert_not_called()
        self.assertEqual((directory / "retain.txt").read_text(), "retain")

    def test_synthetic_compilation_failure_blocks_execution(self):
        self.compile_production_mock()
        with mock.patch.object(syntax.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), \
             mock.patch.object(syntax.synthetic, "execute_synthetic") as execute, self.assertRaises(ValueError):
            syntax.compile_synthetic(self.work, self.compiler)
        execute.assert_not_called()
        report = json.loads((self.work / "evidence/installer-synthetic-compilation.json").read_text())
        self.assertFalse(report["synthetic_fixture_compiled"])
        self.assertFalse(report["generated_installer_executed"])

    def test_synthetic_compile_requires_successful_never_run_production_preflight(self):
        self.compile_production_mock()
        report = self.report()
        report["generated_installer_executed"] = True
        (self.work / "evidence/installer-syntax.json").write_text(json.dumps(report))
        with mock.patch.object(syntax.subprocess, "run") as run, self.assertRaises(ValueError):
            syntax.compile_synthetic(self.work, self.compiler)
        run.assert_not_called()

    def test_synthetic_hooks_never_enter_real_packaging_command(self):
        packager = (ROOT / "tools/package_windows_native.py").read_text()
        self.assertNotIn("PB_SYNTHETIC", packager)
        hook = (ROOT / "packaging/windows/native-safety-fixture.nsh").read_text(encoding="utf-8")
        for forbidden in ("ExecShell", "ExecWait", "CreateShortcut", "WriteReg", "DeleteReg", "$APPDATA", "$LOCALAPPDATA"):
            self.assertNotIn(forbidden, hook)
        self.assertIn("Call OpenNoReparse", hook)
        self.assertIn("Call un.PurgeOwnedData", hook)
        template = (ROOT / "packaging/windows/privacy-browser-native.nsi").read_text(encoding="utf-8")
        self.assertIn('!ifdef PB_SYNTHETIC_FIXTURE\nInstallDir "${PB_SYNTHETIC_ROOT}\\app"\n!else\n'
                      'InstallDir "$LOCALAPPDATA\\Programs\\PrivacyBrowser"\n!endif', template)


if __name__ == "__main__":
    unittest.main()
