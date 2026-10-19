"""Fast-job orchestration fixtures only; never execute Windows/compiler output."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import run_windows_installer_safety as fast


class FastInstallerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.work = self.root / "pb"
        self.identity = {"source_commit": "a" * 40, "quality_run_id": "123", "workflow_run_id": "456",
                         "workflow_run_attempt": "1", "repository": "test/repo"}

    def test_main_refuses_nonhosted_execution_before_source_or_files(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "RUNNER_ENVIRONMENT": "local"}), \
             mock.patch.object(fast, "source_identity") as identity, \
             mock.patch.object(fast, "run_preflight") as run, self.assertRaises(ValueError):
            fast.main()
        identity.assert_not_called()
        run.assert_not_called()

    def test_source_identity_is_exact_quality_and_native_run_binding(self):
        environment = {"PB_EXPECTED_COMMIT": "a" * 40, "PB_QUALITY_RUN_ID": "123", "GITHUB_RUN_ID": "456",
                       "GITHUB_RUN_ATTEMPT": "2", "GITHUB_REPOSITORY": "test/repo"}
        result = subprocess.CompletedProcess([], 0, "a" * 40 + "\n", "")
        with mock.patch.dict(os.environ, environment), mock.patch.object(fast.subprocess, "run", return_value=result) as run:
            identity = fast.source_identity()
        self.assertEqual(identity["source_commit"], "a" * 40)
        self.assertEqual(identity["quality_run_id"], "123")
        self.assertEqual(identity["workflow_run_id"], "456")
        self.assertEqual(identity["workflow_run_attempt"], "2")
        self.assertEqual(run.call_args.args[0], ["git", "rev-parse", "HEAD"])
        self.assertEqual(run.call_args.kwargs["timeout"], 30)
        for name, value in (("PB_EXPECTED_COMMIT", "main"), ("PB_QUALITY_RUN_ID", "0"),
                            ("GITHUB_RUN_ID", ""), ("GITHUB_REPOSITORY", "../test/repo")):
            with self.subTest(name=name), mock.patch.dict(os.environ, {**environment, name: value}), \
                 mock.patch.object(fast.subprocess, "run") as run, self.assertRaises(ValueError):
                fast.source_identity()
            run.assert_not_called()
        with mock.patch.dict(os.environ, environment), \
             mock.patch.object(fast.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "b" * 40, "")), \
             self.assertRaisesRegex(ValueError, "does not match"):
            fast.source_identity()

    def prepare_fixture_archive(self):
        self.work.mkdir()
        (self.work / "downloads").mkdir()
        (self.work / "evidence").mkdir()
        compiler_bytes = b"inert compiler fixture, never executable"
        archive = self.root / "nsis-fixture.zip"
        with zipfile.ZipFile(archive, "x") as stream:
            stream.writestr("nsis-3.07/Bin/makensis.exe", compiler_bytes)
            stream.writestr("nsis-3.07/COPYING", b"retained fixture notices")
        data = archive.read_bytes()
        return data, hashlib.sha256(data).hexdigest(), hashlib.sha256(compiler_bytes).hexdigest()

    def test_fixture_provisioning_matches_archive_and_compiler_pins_without_source_claim(self):
        data, archive_hash, compiler_hash = self.prepare_fixture_archive()
        with mock.patch.object(fast, "NSIS_BYTES", len(data)), mock.patch.object(fast, "NSIS_SHA256", archive_hash), \
             mock.patch.object(fast.syntax, "COMPILER_SHA256", compiler_hash), \
             mock.patch.object(fast, "download_nsis", side_effect=lambda path: path.write_bytes(data)), \
             mock.patch.object(fast.subprocess, "run") as run:
            compiler = fast.provision_fixture_nsis(self.work, self.identity["source_commit"])
        run.assert_not_called()
        self.assertEqual(fast.native.digest(compiler), compiler_hash)
        record = json.loads((self.work / "evidence/nsis-toolchain.json").read_text())
        self.assertEqual(record["purpose"], "synthetic-fixture-only")
        self.assertEqual(record["source_commit"], "a" * 40)
        self.assertFalse(record["gecko_source_definition_verified"])
        self.assertFalse(record["source_compiled"])
        self.assertFalse(record["release_eligible"])
        self.assertFalse((self.work / "source").exists())

    def test_bad_archive_refused_before_extraction_or_compiler(self):
        data, archive_hash, _ = self.prepare_fixture_archive()
        with mock.patch.object(fast, "NSIS_BYTES", len(data)), mock.patch.object(fast, "NSIS_SHA256", "0" * 64), \
             mock.patch.object(fast, "download_nsis", side_effect=lambda path: path.write_bytes(data)), \
             mock.patch.object(fast, "safe_extract_zip") as extract, self.assertRaises(ValueError):
            fast.provision_fixture_nsis(self.work, self.identity["source_commit"])
        extract.assert_not_called()
        self.assertFalse((self.work / "evidence/nsis-toolchain.json").exists())

    def test_bad_compiler_refused_before_evidence_or_execution(self):
        data, archive_hash, _ = self.prepare_fixture_archive()
        with mock.patch.object(fast, "NSIS_BYTES", len(data)), mock.patch.object(fast, "NSIS_SHA256", archive_hash), \
             mock.patch.object(fast.syntax, "COMPILER_SHA256", "0" * 64), \
             mock.patch.object(fast, "download_nsis", side_effect=lambda path: path.write_bytes(data)), \
             mock.patch.object(fast.subprocess, "run") as run, self.assertRaises(ValueError):
            fast.provision_fixture_nsis(self.work, self.identity["source_commit"])
        run.assert_not_called()
        self.assertFalse((self.work / "evidence/nsis-toolchain.json").exists())

    def test_fast_run_orders_phases_and_retains_truthful_commit_bound_success(self):
        calls = []
        compiler = self.work / fast.syntax.COMPILER_RELATIVE
        execution = {"passed": True, "synthetic_fixture_executed": True,
                     "production_syntax_installer_executed": False, "browser_executed": False, "release_eligible": False}
        def provision(work, commit):
            self.assertEqual(os.environ["TEMP"], str(work / "tmp"))
            self.assertEqual(commit, "a" * 40)
            calls.append("provision")
            return compiler
        with mock.patch.object(fast, "provision_fixture_nsis", side_effect=provision), \
             mock.patch.object(fast.syntax, "compile_syntax", side_effect=lambda *_: calls.append("production-compile-only")), \
             mock.patch.object(fast.syntax, "compile_synthetic", side_effect=lambda *_: calls.append("synthetic-compile") or {"fixture": True}), \
             mock.patch.object(fast.synthetic, "execute_synthetic", side_effect=lambda *_: calls.append("synthetic-only-execution") or execution), \
             mock.patch.dict(os.environ, {"TEMP": "unchanged-temp", "TMP": "unchanged-tmp"}):
            report = fast.run_preflight(self.work, self.identity)
            self.assertEqual(os.environ["TEMP"], "unchanged-temp")
            self.assertEqual(os.environ["TMP"], "unchanged-tmp")
        self.assertEqual(calls, ["provision", "production-compile-only", "synthetic-compile", "synthetic-only-execution"])
        self.assertTrue(report["passed"])
        self.assertEqual(report["source_commit"], "a" * 40)
        self.assertFalse(report["gecko_downloaded"])
        self.assertFalse(report["mozilla_build_installed"])
        self.assertFalse(report["release_eligible"])
        self.assertFalse((self.work / "artifact").exists())
        self.assertEqual(json.loads((self.work / "evidence/installer-safety-preflight.json").read_text()), report)

    def test_failure_blocks_remaining_phases_and_retains_failed_bound_report(self):
        with mock.patch.object(fast, "provision_fixture_nsis", side_effect=ValueError("fixture integrity mismatch")), \
             mock.patch.object(fast.syntax, "compile_syntax") as compile, \
             mock.patch.object(fast.synthetic, "execute_synthetic") as execute, self.assertRaises(ValueError):
            fast.run_preflight(self.work, self.identity)
        compile.assert_not_called()
        execute.assert_not_called()
        report = json.loads((self.work / "evidence/installer-safety-preflight.json").read_text())
        self.assertFalse(report["passed"])
        self.assertEqual(report["quality_run_id"], "123")

    def test_existing_root_is_not_reused(self):
        self.work.mkdir()
        sentinel = self.work / "retain.txt"
        sentinel.write_text("retain")
        with mock.patch.object(fast, "provision_fixture_nsis") as provision, self.assertRaises(ValueError):
            fast.run_preflight(self.work, self.identity)
        provision.assert_not_called()
        self.assertEqual(sentinel.read_text(), "retain")

    def test_workflow_requires_fast_success_for_same_quality_revision(self):
        workflow = (ROOT / ".github/workflows/windows-native.yml").read_text()
        fast_job = workflow.split("\n  installer-safety:\n", 1)[1].split("\n  build:\n", 1)[0]
        build_job = workflow.split("\n  build:\n", 1)[1]
        for job in (fast_job, build_job):
            self.assertIn("ref: ${{ github.event.workflow_run.head_sha }}", job)
            self.assertIn("PB_EXPECTED_COMMIT: ${{ github.event.workflow_run.head_sha }}", job)
            self.assertIn("persist-credentials: false", job)
        self.assertIn("PB_QUALITY_RUN_ID: ${{ github.event.workflow_run.id }}", fast_job)
        self.assertIn("needs: [changes, installer-safety]", build_job)
        self.assertIn("needs['installer-safety'].result == 'success'", build_job)
        self.assertIn("python tools/run_windows_installer_safety.py", fast_job)
        self.assertIn("D:/pb/evidence/*.json", fast_job)
        self.assertIn("D:/pb/evidence/*.log", fast_job)
        for forbidden in ("pip install", "MozillaBuildSetup", "build_windows_native.ps1", "D:/pb/artifact/", "*.exe"):
            self.assertNotIn(forbidden, fast_job)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)


if __name__ == "__main__":
    unittest.main()
