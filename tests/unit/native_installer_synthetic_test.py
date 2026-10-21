"""Pure Python fixtures/mocked Win32 and child processes; never execute a setup."""
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import test_native_installer_synthetic as synthetic


class NativeInstallerSyntheticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        (self.work / "evidence").mkdir()
        self.root = self.work / synthetic.EXECUTION_RELATIVE
        self.root.mkdir(parents=True)
        self.executable = self.root / synthetic.FIXTURE_EXE
        self.executable.write_bytes(b"MZinert Python fixture; never execute")
        self.compilation = {"synthetic_fixture_compiled": True, "fixture_root": synthetic.EXECUTION_WINDOWS,
                            "generated_installer_sha256": synthetic.native.digest(self.executable), "release_eligible": False}
        self.calls = []
        self.uninstall_calls = 0
        self.junction_targets = {}
        self.reader_calls = 0

    def report(self):
        return json.loads((self.work / "evidence/installer-synthetic-execution.json").read_text())

    def fake_run(self, executable, arguments, root, environment, log, commands):
        self.assertEqual(environment["TEMP"], str(self.root / "tmp"))
        self.assertEqual(environment["APPDATA"], str(self.root / "roaming"))
        self.assertEqual(environment["LOCALAPPDATA"], str(self.root / "local"))
        self.calls.append((executable, arguments))
        commands.append({"argv": [str(executable), *arguments], "started": True, "exit_code": 0})
        app = root / "app"
        owned = [root / "local/PrivacyBrowser", root / "roaming/PrivacyBrowser"]
        if executable == self.executable:
            self.assertEqual(arguments, ["/S"])
            if app.exists():
                commands[-1]["exit_code"] = 1
                return 1
            app.mkdir()
            (app / "uninstall.exe").write_bytes(b"MZinert generated-uninstaller fixture")
            for path in owned:
                path.mkdir()
                marker = f"PrivacyBrowser-UserData-v1\r\n{app}\r\n{path}\r\n".encode("utf-16-le")
                (path / synthetic.MARKER).write_bytes(marker)
        else:
            self.assertEqual(executable, app / "uninstall.exe")
            self.assertEqual(arguments, ["/S", "_?=" + str(app)])
            self.uninstall_calls += 1
            if self.uninstall_calls in (1, 2, 3):
                commands[-1]["exit_code"] = 1
                return 1
            if self.uninstall_calls == 4:
                # Simulate NSIS only inside this unit test's new temporary data.
                for path in owned:
                    shutil.rmtree(path)
        return 0

    def fake_reader(self, work, root, environment, log, commands):
        # An explicit mock, not PowerShell/COM execution or Windows evidence.
        self.reader_calls += 1
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.uninstall_calls, 0)
        self.assertTrue((root / "app/uninstall.exe").is_file())
        self.assertFalse((root / "local/PrivacyBrowser/Profile-日本語").exists())
        return {"path": synthetic.SHORTCUT_OUTPUT, "sha256": "f" * 64, "mocked": True}

    @contextmanager
    def execution_mocks(self, runner=None, reader=None):
        is_reparse = synthetic.native.is_link_or_reparse
        readlink = os.readlink

        def make_junction(path, target, *_):
            path.mkdir()
            self.junction_targets[path] = str(target)

        def remove_junction(path):
            path.rmdir()
            del self.junction_targets[path]

        with mock.patch.object(synthetic, "require_host"), \
             mock.patch.object(synthetic, "run_fixture", side_effect=runner or self.fake_run), \
             mock.patch.object(synthetic, "run_shortcut_readback", side_effect=reader or self.fake_reader), \
             mock.patch.object(synthetic, "locked_file", return_value=nullcontext()), \
             mock.patch.object(synthetic, "make_junction", side_effect=make_junction), \
             mock.patch.object(synthetic, "remove_junction", side_effect=remove_junction), \
             mock.patch.object(synthetic.native, "is_link_or_reparse",
                               side_effect=lambda path: path in self.junction_targets or is_reparse(path)), \
             mock.patch.object(synthetic.os, "readlink",
                               side_effect=lambda path: self.junction_targets[path] if path in self.junction_targets else readlink(path)):
            yield

    def test_full_harness_with_mocked_execution_distinguishes_production(self):
        with self.execution_mocks():
            report = synthetic.execute_synthetic(self.work, self.compilation)
        self.assertEqual(len(self.calls), 10)
        self.assertEqual([item["exit_code"] for item in report["commands"][:5]], [1, 1, 1, 1, 0])
        self.assertTrue(report["passed"])
        self.assertTrue(report["synthetic_fixture_executed"])
        self.assertFalse(report["production_syntax_installer_executed"])
        self.assertFalse(report["browser_executed"])
        self.assertFalse(report["release_eligible"])
        self.assertFalse(report["generated_executables_uploaded"])
        self.assertEqual(len(report["checks"]), 14)
        self.assertTrue(report["checks"]["synthetic_shortcut_roundtrip_readback"])
        self.assertEqual(self.reader_calls, 1)
        self.assertTrue(all(value is True for value in report["checks"].values()))
        self.assertEqual(self.report(), report)
        self.assertTrue((self.root / "foreign/retain.txt").is_file())
        self.assertFalse((self.work / "artifact").exists())
        self.assertEqual(list((self.work / "evidence").glob("*.exe")), [])

    def test_real_execution_is_blocked_outside_hosted_windows(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "RUNNER_ENVIRONMENT": "local"}), \
             mock.patch.object(synthetic.subprocess, "Popen") as run, self.assertRaises(ValueError):
            synthetic.execute_synthetic(self.work, self.compilation)
        run.assert_not_called()

    def test_compilation_hash_mismatch_refuses_all_execution(self):
        self.compilation["generated_installer_sha256"] = "0" * 64
        with mock.patch.object(synthetic, "require_host"), mock.patch.object(synthetic, "run_fixture") as run, \
             self.assertRaises(ValueError):
            synthetic.execute_synthetic(self.work, self.compilation)
        run.assert_not_called()

    def test_existing_execution_data_is_never_overwritten(self):
        (self.root / "retain.txt").write_text("retain")
        with mock.patch.object(synthetic, "require_host"), mock.patch.object(synthetic, "run_fixture") as run, \
             self.assertRaises(ValueError):
            synthetic.execute_synthetic(self.work, self.compilation)
        run.assert_not_called()
        self.assertEqual((self.root / "retain.txt").read_text(), "retain")

    def test_failed_initialization_retains_truthful_failed_report(self):
        def failure(exe, args, root, env, log, commands):
            if (root / "app").exists():
                return self.fake_run(exe, args, root, env, log, commands)
            commands.append({"started": True, "exit_code": 1})
            return 1
        with self.execution_mocks(failure), self.assertRaises(ValueError):
            synthetic.execute_synthetic(self.work, self.compilation)
        report = self.report()
        self.assertTrue(report["synthetic_fixture_executed"])
        self.assertFalse(report["passed"])
        self.assertEqual(len(report["checks"]), 4)
        self.assertNotIn("fresh_application_directory_created", report["checks"])
        self.assertEqual(self.reader_calls, 0)
        self.assertIn("initialization/ordinary Unicode NT open failed", report["error"])

    def test_shortcut_readback_failure_retains_diagnostics_and_prevents_destructive_cases(self):
        path = self.work / "evidence" / synthetic.SHORTCUT_OUTPUT
        retained = {"passed": False, "expected": "fixed synthetic target", "actual": "different target"}
        def failed_reader(*args):
            self.fake_reader(*args)
            path.write_text(json.dumps(retained))
            raise ValueError("Synthetic shortcut readback failed; retain shortcut diagnostics")
        with self.execution_mocks(reader=failed_reader), self.assertRaisesRegex(ValueError, "shortcut readback"):
            synthetic.execute_synthetic(self.work, self.compilation)
        report = self.report()
        self.assertFalse(report["passed"])
        self.assertTrue(report["synthetic_fixture_executed"])
        self.assertNotIn("synthetic_shortcut_roundtrip_readback", report["checks"])
        self.assertEqual(len(report["checks"]), 6)
        self.assertEqual(json.loads(path.read_text()), retained)
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.uninstall_calls, 0)
        self.assertFalse((self.root / "local/PrivacyBrowser/Profile-日本語").exists())

    def test_unexpected_success_on_invalid_marker_fails_closed(self):
        def permissive(*args):
            result = self.fake_run(*args)
            return 0 if self.uninstall_calls == 1 else result
        with self.execution_mocks(permissive), self.assertRaises(ValueError):
            synthetic.execute_synthetic(self.work, self.compilation)
        self.assertFalse(self.report()["passed"])
        self.assertIn("not refused", self.report()["error"])
        self.assertEqual(len(self.calls), 6)

    def test_successful_adoption_of_existing_empty_directory_fails_closed(self):
        def permissive(*args):
            self.fake_run(*args)
            return 0
        with self.execution_mocks(permissive), self.assertRaises(ValueError):
            synthetic.execute_synthetic(self.work, self.compilation)
        report = self.report()
        self.assertFalse(report["passed"])
        self.assertEqual(report["checks"], {})
        self.assertIn("existing empty directory application path was not refused", report["error"])
        self.assertEqual(len(self.calls), 1)
        self.assertTrue((self.root / "app").is_dir())

    def test_mutated_nonempty_directory_on_refusal_fails_closed(self):
        def destructive(*args):
            result = self.fake_run(*args)
            collision = self.root / "app/nested/retain-日本語.txt"
            if collision.is_file():
                collision.write_bytes(b"incorrectly changed")
            return result
        with self.execution_mocks(destructive), self.assertRaises(ValueError):
            synthetic.execute_synthetic(self.work, self.compilation)
        report = self.report()
        self.assertFalse(report["passed"])
        self.assertIn("nonempty application directory was changed", report["error"])
        self.assertEqual(len(self.calls), 2)
        self.assertTrue((self.root / "app/nested/retain-日本語.txt").is_file())

    def test_creation_refusal_cannot_mutate_owned_data_parents(self):
        def destructive(*args):
            result = self.fake_run(*args)
            (self.root / "local/unexpected.txt").write_bytes(b"must not be written")
            return result
        with self.execution_mocks(destructive), self.assertRaises(ValueError):
            synthetic.execute_synthetic(self.work, self.compilation)
        self.assertFalse(self.report()["passed"])
        self.assertIn("refusal changed data outside the application path", self.report()["error"])
        self.assertEqual(len(self.calls), 1)

    def test_fixture_process_refuses_arbitrary_or_production_executables(self):
        for path in (self.work / "installer-syntax/DO-NOT-RUN-inert-installer-syntax" / synthetic.native.SETUP_NAME,
                     self.root / "privacybrowser.exe"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"inert")
            with mock.patch.object(synthetic.subprocess, "Popen") as run, self.assertRaises(ValueError):
                synthetic.run_fixture(path, ["/S"], self.root, {}, mock.Mock(), [])
            run.assert_not_called()

    def test_snapshot_rejects_symlinks(self):
        path = self.root / "owned"
        path.mkdir()
        foreign = self.root / "foreign"
        foreign.mkdir()
        try:
            (path / "link").symlink_to(foreign, target_is_directory=True)
        except OSError:
            self.skipTest("This test host does not permit symlinks")
        with self.assertRaises(ValueError):
            synthetic.snapshot([path])

    def test_multiple_buffers_and_unicode_fixture_contract(self):
        self.assertGreaterEqual(synthetic.FILE_COUNT, 1500)
        source = (ROOT / "tools/test_native_installer_synthetic.py").read_text(encoding="utf-8")
        self.assertIn("supplementary-", source)
        self.assertIn("Profile-日本語/nested", source)
        self.assertNotIn("shutil.rmtree", source)
        self.assertNotIn("Remove-Item", source)
        self.assertIn('"_?=" + str(app)', source)


if __name__ == "__main__":
    unittest.main()
