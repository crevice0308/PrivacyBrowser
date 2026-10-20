"""Portable Places evidence contracts. All generated fixtures are INERT.

These tests never execute a browser, native PE, xpcshell, installer, or shell
command and never write environment variables or known-folder locations.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import windows_places_smoke as smoke

INERT_LOG = (
    "INERT fixture: synthetic test observations, no executable was launched.\n"
    " 0:00.10 SUITE_START: xpcshell - running 1 tests\n"
    " 0:00.10 TEST_START: toolkit/components/places/tests/unit/test_isURIVisited.js\n"
    " 0:00.11 PASS inert assertion one\n"
    " 0:00.12 PASS inert assertion two\n"
    " 0:00.13 TEST_END: Test PASS. Subtests passed 2/2. Unexpected 0\n"
    " 0:00.13 INFO INFO | Result summary:\n"
    " 0:00.13 INFO INFO | Passed: 1\n"
    " 0:00.13 INFO INFO | Failed: 0\n"
    " 0:00.13 INFO INFO | Todo: 0\n"
    " 0:00.13 INFO INFO | Retried: 0\n"
    " 0:00.13 SUITE_END\n"
    "\nxpcshell\n~~~~~~~~\n"
    "Ran 3 checks (2 subtests, 1 tests)\n"
    "Expected results: 3\n"
    "Unexpected results: 0\n"
    "OK\n"
    " 0:00.13 INFO http3Server server shutting down ...\n"
).encode("utf-8")
INERT_EXIT = {"schema_version": 1, "phase": "xpcshell", "command_exit_code": 0, "tee_exit_code": 0}


def make_retained_fixture(evidence: Path, runtime: Path) -> dict:
    """Create new INERT raw evidence and report using an existing build manifest.

    The caller supplies an existing inert runtime/privacybrowser.exe and complete
    evidence/build-evidence.json. No existing file is changed or executed.
    """
    for name, data in ((smoke.LOG, INERT_LOG), (smoke.EXIT, json.dumps(INERT_EXIT).encode("utf-8"))):
        with (evidence / name).open("xb") as stream:
            stream.write(data)
    report = smoke._derive(evidence, runtime)
    with (evidence / smoke.REPORT).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    return report


class PlacesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.evidence = self.work / "evidence"
        self.runtime = self.work / "candidate/PrivacyBrowser"
        self.evidence.mkdir()
        self.runtime.mkdir(parents=True)
        self.binary = self.runtime / "privacybrowser.exe"
        self.binary.write_bytes(b"INERT fixture binary, never executable")
        self.manifest = {
            "schema_version": 1, "build_kind": "full-source-build", "source_compiled": True,
            "official_release": False, "target": "windows", "native_binary": "privacybrowser.exe",
            "files": {"privacybrowser.exe": smoke.native.digest(self.binary)},
            "commands": [{"argv": ["./mach", phase], "exit_code": 0} for phase in ("build", "package")],
            "commit": "a" * 40,
            "workflow_run": "https://github.com/crevice0308/PrivacyBrowser/actions/runs/123",
        }
        self.write_json(smoke.MANIFEST, self.manifest)
        self.report = make_retained_fixture(self.evidence, self.runtime)
        self.report_bytes = (self.evidence / smoke.REPORT).read_bytes()

    def write_json(self, name, value):
        (self.evidence / name).write_text(json.dumps(value), encoding="utf-8")

    def validate(self):
        return smoke.validate_retained(self.evidence, self.runtime)

    def replace_record(self, name, data):
        (self.evidence / name).write_bytes(data)
        report = deepcopy(self.report)
        role = {smoke.LOG: "xpcshell_log", smoke.EXIT: "xpcshell_exit"}.get(name)
        item = report["records"][role] if role else report["build_manifest"]
        item["sha256"] = smoke.native.digest(self.evidence / name)
        self.write_json(smoke.REPORT, report)

    def test_complete_fixture_and_record_pass_without_execution_or_input_changes(self):
        before = {path: path.read_bytes() for path in self.work.rglob("*") if path.is_file()}
        report = self.validate()
        self.assertTrue(report["passed"])
        self.assertFalse(report["release_eligible"])
        self.assertFalse(report["broader_privacy_security_acceptance"])
        self.assertNotIn("owned_roots_established", report)
        self.assertEqual(report["results"], {"checks": 3, "subtests": 2, "tests": 1, "expected": 3,
                                             "unexpected": 0, "passed": 1, "failed": 0, "todo": 0})
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        (self.evidence / smoke.REPORT).unlink()
        with mock.patch.dict(smoke.os.environ, {}, clear=True):
            self.assertEqual(smoke.record(self.work), report)
        self.assertEqual(self.validate(), report)
        for path, data in before.items():
            if path.name != smoke.REPORT:
                self.assertEqual(path.read_bytes(), data)

    def test_retained_runtime_may_be_at_arbitrary_portable_path(self):
        moved = self.work / "arbitrary-linux-unpack"
        self.runtime.rename(moved)
        self.assertEqual(smoke.validate_retained(self.evidence, moved), self.report)

    def test_no_overwrite_even_on_success(self):
        with self.assertRaises(FileExistsError):
            smoke.record(self.work)
        self.assertEqual((self.evidence / smoke.REPORT).read_bytes(), self.report_bytes)
        with self.assertRaises(FileExistsError):
            make_retained_fixture(self.evidence, self.runtime)
        self.assertEqual((self.evidence / smoke.REPORT).read_bytes(), self.report_bytes)

    def test_invalid_inputs_do_not_create_report(self):
        (self.evidence / smoke.REPORT).unlink()
        self.write_json(smoke.EXIT, {**INERT_EXIT, "tee_exit_code": 1})
        with self.assertRaises(ValueError):
            smoke.record(self.work)
        self.assertFalse((self.evidence / smoke.REPORT).exists())

    def test_manifest_native_and_raw_hashes_are_bound(self):
        for name in (smoke.MANIFEST, smoke.LOG, smoke.EXIT):
            path = self.evidence / name
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.validate()
            path.write_bytes(original)
        self.binary.write_bytes(b"INERT changed binary")
        with self.assertRaises(ValueError):
            self.validate()

    def test_report_fields_are_exact_and_type_strict(self):
        mutations = [
            (("schema_version",), True), (("passed",), 1), (("release_eligible",), 0),
            (("broader_privacy_security_acceptance",), 0), (("commit",), "b" * 40),
            (("workflow_run",), "https://github.com/crevice0308/PrivacyBrowser/actions/runs/124"),
            (("native_binary_sha256",), "b" * 64), (("command",), smoke.COMMAND + ["--extra"]),
            (("command",), smoke.COMMAND[:-1]), (("command",), " ".join(smoke.COMMAND)),
            (("build_manifest", "path"), "../build-evidence.json"),
            (("build_manifest", "sha256"), "b" * 64),
            (("records", "xpcshell_log", "path"), "renamed.log"),
            (("records", "xpcshell_exit", "path"), "../xpcshell-exit.json"),
            (("records", "xpcshell_log", "sha256"), "b" * 64),
            (("records", "xpcshell_exit", "sha256"), "b" * 64),
        ]
        for field in self.report["results"]:
            mutations.extend([(("results", field), True), (("results", field), "0"),
                              (("results", field), self.report["results"][field] + 1)])
        for path, value in mutations:
            report = deepcopy(self.report)
            container = report
            for key in path[:-1]:
                container = container[key]
            container[path[-1]] = value
            self.write_json(smoke.REPORT, report)
            with self.subTest(path=path, value=value), self.assertRaises(ValueError):
                self.validate()
        for report in ({}, {**self.report, "unknown": True}, {**self.report, "records": []}):
            self.write_json(smoke.REPORT, report)
            with self.assertRaises(ValueError):
                self.validate()

    def test_rehashed_exit_failures_and_malformed_types_cannot_use_positive_report(self):
        for field in ("schema_version", "command_exit_code", "tee_exit_code"):
            for value in (True, False, None, "0", [], {}, 0.0, -1, 2):
                raw = {**INERT_EXIT, field: value}
                self.replace_record(smoke.EXIT, json.dumps(raw).encode())
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.validate()
        for raw in ({**INERT_EXIT, "phase": "build"}, {**INERT_EXIT, "unknown": 0},
                    {key: value for key, value in INERT_EXIT.items() if key != "tee_exit_code"}):
            self.replace_record(smoke.EXIT, json.dumps(raw).encode())
            with self.assertRaises(ValueError):
                self.validate()

    def test_rehashed_raw_failures_cannot_use_positive_report(self):
        failures = (
            b" 0:00.14 TEST-UNEXPECTED-FAIL | test_isURIVisited | assertion failed\n",
            b" 0:00.14 TEST-UNEXPECTED-ERROR | test_isURIVisited | error\n",
            b" 0:00.14 PROCESS-CRASH | test_isURIVisited | process died\n",
            b" 0:00.14 ERROR an error after summary\n",
            b" 0:00.14 FAIL assertion failed\n",
            b" 0:00.14 TIMEOUT\n",
            b" 0:00.14 INFO Unexpected 1\n",
            b"Failed: 1\n",
            b"Unexpected results: 1\n",
        )
        for failure in failures:
            for data in (failure + INERT_LOG, INERT_LOG + failure):
                self.replace_record(smoke.LOG, data)
                with self.subTest(failure=failure, appended=data.startswith(INERT_LOG)), self.assertRaises(ValueError):
                    self.validate()

    def test_missing_inconsistent_duplicate_and_wrong_test_summaries_fail(self):
        replacements = (
            (b"Ran 3 checks (2 subtests, 1 tests)", b"Ran 0 checks (0 subtests, 0 tests)"),
            (b"Ran 3 checks", b"Ran 4 checks"), (b"2 subtests", b"3 subtests"),
            (b"1 tests)", b"2 tests)"), (b"Expected results: 3", b"Expected results: 2"),
            (b"Unexpected results: 0", b"Unexpected results: 1"),
            (b"INFO INFO | Passed: 1", b"INFO INFO | Passed: 0"),
            (b"INFO INFO | Failed: 0", b"INFO INFO | Failed: 1"),
            (b"INFO INFO | Todo: 0", b"INFO INFO | Todo: 1"),
            (b"INFO INFO | Retried: 0", b"INFO INFO | Retried: 1"),
            (b"Subtests passed 2/2", b"Subtests passed 1/2"),
            (b"Test PASS.", b"Test FAIL."), (b"test_isURIVisited.js", b"test_other.js"),
            (b"SUITE_START: xpcshell - running 1 tests", b"SUITE_START: xpcshell - running 0 tests"),
            (b"PASS inert assertion one", b"INFO omitted assertion one"),
            (b"\nOK\n", b"\nNOT OK\n"), (b"\nxpcshell\n", b"\nother suite\n"),
        )
        for old, new in replacements:
            self.replace_record(smoke.LOG, INERT_LOG.replace(old, new))
            with self.subTest(old=old, new=new), self.assertRaises(ValueError):
                self.validate()
        for line in INERT_LOG.splitlines(keepends=True):
            if any(token in line for token in (b"SUITE_", b"TEST_", b"INFO INFO |", b"Ran ",
                                              b"Expected results:", b"Unexpected results:", b"OK")):
                for data in (INERT_LOG.replace(line, b""), INERT_LOG + line):
                    # The optional Retried: 0 record may be omitted.
                    if b"Retried:" in line and data == INERT_LOG.replace(line, b""):
                        continue
                    self.replace_record(smoke.LOG, data)
                    with self.subTest(line=line, duplicate=data.startswith(INERT_LOG)), self.assertRaises(ValueError):
                        self.validate()

    def test_crlf_actual_harness_format_is_accepted(self):
        (self.evidence / smoke.REPORT).unlink()
        (self.evidence / smoke.LOG).write_bytes(INERT_LOG.replace(b"\n", b"\r\n"))
        report = smoke.record(self.work)
        self.assertEqual(self.validate(), report)

    def test_manifest_fields_fail_closed_even_after_manifest_record_rehash(self):
        changes = (
            ("schema_version", True), ("source_compiled", 1), ("official_release", 0),
            ("build_kind", "overlay"), ("target", "linux"), ("native_binary", "firefox.exe"),
            ("commit", "main"), ("commit", "a" * 39), ("commit", None), ("commit", 123),
            ("workflow_run", None), ("workflow_run", "https://github.com/other/repo/actions/runs/123"),
            ("workflow_run", "https://github.com/crevice0308/PrivacyBrowser/actions/runs/0"),
            ("workflow_run", "https://github.com/crevice0308/PrivacyBrowser/actions/runs/0123"),
            ("workflow_run", "https://github.com/crevice0308/PrivacyBrowser/actions/runs/123?x=1"),
            ("workflow_run", "https://github.com/crevice0308/PrivacyBrowser/actions/runs/123#x"),
            ("files", []), ("files", {}), ("files", {"privacybrowser.exe": "b" * 64}),
            ("files", {"privacybrowser.exe": True}),
            ("files", {**self.manifest["files"], "../other": "b" * 64}),
            ("commands", []), ("commands", self.manifest["commands"][::-1]),
            ("commands", self.manifest["commands"] + [{"argv": smoke.COMMAND, "exit_code": 0}]),
            ("commands", [{"argv": ["./mach", "build"], "exit_code": False}, self.manifest["commands"][1]]),
            ("commands", [{"argv": ["./mach", "build"], "exit_code": 1}, self.manifest["commands"][1]]),
        )
        for field, value in changes:
            manifest = {**self.manifest, field: value}
            self.replace_record(smoke.MANIFEST, json.dumps(manifest).encode())
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.validate()

    def test_changed_source_commit_or_run_still_must_match_report_after_rehash(self):
        for field, value in (("commit", "b" * 40),
                             ("workflow_run", "https://github.com/crevice0308/PrivacyBrowser/actions/runs/124")):
            self.replace_record(smoke.MANIFEST, json.dumps({**self.manifest, field: value}).encode())
            with self.assertRaises(ValueError):
                self.validate()

    def test_empty_binary_cannot_be_authorized_by_matching_rehashed_manifest(self):
        self.binary.write_bytes(b"")
        manifest = deepcopy(self.manifest)
        manifest["files"]["privacybrowser.exe"] = smoke.native.digest(self.binary)
        self.replace_record(smoke.MANIFEST, json.dumps(manifest).encode())
        with self.assertRaises(ValueError):
            self.validate()

    def test_missing_empty_oversize_and_nonregular_evidence_is_rejected(self):
        for name in (smoke.LOG, smoke.EXIT, smoke.MANIFEST, smoke.REPORT):
            path = self.evidence / name
            original = path.read_bytes()
            path.unlink()
            with self.subTest(name=name, case="missing"), self.assertRaises((ValueError, OSError)):
                self.validate()
            path.mkdir()
            with self.subTest(name=name, case="directory"), self.assertRaises(ValueError):
                self.validate()
            path.rmdir()
            path.write_bytes(b"")
            with self.subTest(name=name, case="empty"), self.assertRaises(ValueError):
                self.validate()
            path.write_bytes(original)
        for name, limit in ((smoke.LOG, smoke.MAX_LOG), (smoke.EXIT, smoke.MAX_JSON),
                            (smoke.REPORT, smoke.MAX_JSON), (smoke.MANIFEST, smoke.native.MAX_JSON_BYTES)):
            path = self.evidence / name
            original = path.read_bytes()
            with path.open("wb") as stream:
                stream.truncate(limit + 1)
            with self.subTest(name=name, case="oversize"), self.assertRaises(ValueError):
                self.validate()
            path.write_bytes(original)

    def test_symlinks_are_rejected(self):
        for name in (smoke.LOG, smoke.EXIT, smoke.MANIFEST, smoke.REPORT):
            path = self.evidence / name
            original = path.read_bytes()
            other = self.work / ("linked-" + name)
            other.write_bytes(original)
            path.unlink()
            try:
                path.symlink_to(other)
            except OSError:
                path.write_bytes(original)
                self.skipTest("Symlink creation unavailable on this host")
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.validate()
            path.unlink()
            path.write_bytes(original)

    def test_duplicate_json_nonobjects_and_nonfinite_values_fail(self):
        invalid = (b"[]", b"null", b"true", b"0", b"{}", b'{"schema_version":1,"schema_version":1}',
                   b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}', b"\xff", b"{")
        for name in (smoke.EXIT, smoke.MANIFEST, smoke.REPORT):
            original = (self.evidence / name).read_bytes()
            for data in invalid:
                (self.evidence / name).write_bytes(data)
                with self.subTest(name=name, data=data), self.assertRaises(ValueError):
                    self.validate()
            (self.evidence / name).write_bytes(original)

    def test_cli_rejects_nonhosted_execution_and_path_overrides_before_recording(self):
        for system, environment, work in (
            ("posix", {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}, r"D:\pb"),
            ("nt", {}, r"D:\pb"),
            ("nt", {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted"}, r"D:\pb"),
            ("nt", {"GITHUB_ACTIONS": "false", "RUNNER_ENVIRONMENT": "github-hosted"}, r"D:\pb"),
            ("nt", {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}, r"D:\other"),
            ("nt", {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}, r"D:\pb\..\other"),
        ):
            with self.subTest(system=system, environment=environment, work=work), \
                    mock.patch.object(smoke.os, "name", system), \
                    mock.patch.dict(smoke.os.environ, environment, clear=True), \
                    mock.patch.object(sys, "argv", ["windows_places_smoke.py", "--work-root", work]), \
                    mock.patch.object(smoke, "record") as record, self.assertRaises(ValueError):
                smoke.main()
            record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
