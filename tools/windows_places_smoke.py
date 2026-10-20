#!/usr/bin/env python3
"""Retain and revalidate the post-install Places smoke test without executing it.

The caller runs the unchanged xpcshell command ONLY after the installer creates
and verifies owned known-folder roots. This recorder does not establish or prove
that ownership, redirect known folders, execute binaries, or change the build
manifest. Hash bindings protect retained evidence consistency, not authenticity
against an actor who can replace the whole evidence bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re

import verify_windows_native as native

COMMAND = ["./mach", "xpcshell-test", "toolkit/components/places/tests/unit/test_isURIVisited.js"]
REPORT = "windows-places-smoke.json"
LOG = "xpcshell.log"
EXIT = "xpcshell-exit.json"
MANIFEST = "build-evidence.json"
MAX_LOG = 16 * 1024 * 1024
MAX_JSON = 64 * 1024
WORKFLOW = re.compile(r"https://github\.com/crevice0308/PrivacyBrowser/actions/runs/[1-9][0-9]*\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
LIMITATION = (
    "One Places xpcshell test only, not broader privacy/security acceptance or release approval. "
    "The caller must establish owned known-folder roots before executing the test; "
    "this retained-log recorder does not independently prove that sequencing."
)


def _read(root: Path, name: str, limit: int) -> bytes:
    path = native.regular_file(root, name)
    native.require(0 < path.stat().st_size <= limit, "Missing or oversized Places evidence: " + name)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    native.require(0 < len(data) <= limit, "Missing or oversized Places evidence: " + name)
    return data


def _object(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            native.require(key not in result, "Duplicate Places evidence JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Non-finite Places evidence JSON value: " + value)

    value = json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid_constant)
    native.require(isinstance(value, dict), "Expected a Places evidence JSON object")
    return value


def _record(name: str, data: bytes) -> dict:
    return {"path": name, "sha256": hashlib.sha256(data).hexdigest()}


def _canonical(value: object) -> str:
    # Unlike Python equality, JSON distinguishes false/0 and true/1.
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _summary(data: bytes) -> dict:
    text = data.decode("utf-8")
    native.require("\0" not in text and "\x1b" not in text, "Control bytes in Places test log")
    timestamp = re.compile(r"^\s*[0-9]+:[0-9]{2}\.[0-9]+\s+")
    lines = [timestamp.sub("", line).strip() for line in text.splitlines()]
    failure = re.compile(
        r"^(?:ERROR|FAIL|CRASH|TIMEOUT)\b"
        r"|\b(?:TEST[-_]UNEXPECTED[-_][A-Z_-]+|PROCESS[-_]CRASH|TEST[-_]FAIL|TEST[-_]TIMEOUT)\b"
        r"|\bUnexpected\s+[1-9][0-9]*\b"
        r"|\b(?:Failed|Unexpected results)\s*:\s*[1-9][0-9]*\b", re.I)
    native.require(not any(failure.search(line) for line in lines), "Raw Places log contains a failure")

    def one(prefix: str) -> tuple[int, str]:
        found = [(index, line) for index, line in enumerate(lines) if line.startswith(prefix)]
        native.require(len(found) == 1, "Missing or duplicate Places log record: " + prefix)
        return found[0]

    suite_start, suite = one("SUITE_START:")
    start, test = one("TEST_START:")
    end, finish = one("TEST_END:")
    suite_end, ended = one("SUITE_END")
    native.require(suite == "SUITE_START: xpcshell - running 1 tests"
                   and test == "TEST_START: " + COMMAND[-1] and ended == "SUITE_END"
                   and suite_start < start < end < suite_end, "Wrong or incomplete Places test execution")
    ending = re.fullmatch(
        r"TEST_END: Test PASS\. Subtests passed ([1-9][0-9]{0,8})/([1-9][0-9]{0,8})\. Unexpected 0", finish)
    native.require(ending is not None and ending[1] == ending[2], "Places test did not pass every subtest")
    subtests = int(ending[1])
    native.require(sum(line.startswith("PASS ") for line in lines) == subtests
                   and all(start < index < end for index, line in enumerate(lines) if line.startswith("PASS ")),
                   "Places raw subtest results differ from their summary")

    summary, heading = one("INFO INFO | Result summary:")
    native.require(heading == "INFO INFO | Result summary:", "Malformed Places result summary")
    for offset, (label, count) in enumerate((("Passed", 1), ("Failed", 0), ("Todo", 0)), 1):
        index, line = one("INFO INFO | " + label + ":")
        native.require(index == summary + offset and line == f"INFO INFO | {label}: {count}",
                       "Places harness result missing or unsuccessful: " + label)
    native.require(end < summary and summary + 3 < suite_end, "Misordered Places harness summary")
    retries = [line for line in lines if line.startswith("INFO INFO | Retried:")]
    native.require(retries in ([], ["INFO INFO | Retried: 0"]), "Unexpected Places test retries")

    ran_index, ran = one("Ran ")
    match = re.fullmatch(r"Ran ([1-9][0-9]{0,8}) checks \(([1-9][0-9]{0,8}) subtests, 1 tests\)", ran)
    native.require(match is not None, "Missing real Places check summary")
    checks = int(match[1])
    native.require(int(match[2]) == subtests and checks == subtests + 1,
                   "Inconsistent Places check and subtest counts")
    expected_index, expected = one("Expected results:")
    unexpected_index, unexpected = one("Unexpected results:")
    ok = [index for index, line in enumerate(lines) if line == "OK"]
    native.require(suite_end < ran_index and ran_index >= 2
                   and lines[ran_index - 2:ran_index] == ["xpcshell", "~~~~~~~~"]
                   and expected_index == ran_index + 1 and expected == f"Expected results: {checks}"
                   and unexpected_index == ran_index + 2 and unexpected == "Unexpected results: 0"
                   and ok == [ran_index + 3], "Incomplete, duplicate, or unsuccessful Places final summary")
    return {"checks": checks, "subtests": subtests, "tests": 1, "expected": checks,
            "unexpected": 0, "passed": 1, "failed": 0, "todo": 0}


def _derive(evidence: Path, runtime: Path) -> dict:
    for directory in (evidence, runtime):
        native.require(directory.is_dir() and not native.is_link_or_reparse(directory),
                       "Places evidence and runtime must be real directories")
    manifest_data = _read(evidence, MANIFEST, native.MAX_JSON_BYTES)
    manifest = _object(manifest_data)
    native.require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1
                   and manifest.get("build_kind") == "full-source-build"
                   and manifest.get("source_compiled") is True and manifest.get("official_release") is False
                   and manifest.get("target") == "windows" and manifest.get("native_binary") == native.BINARY,
                   "Places requires an unpublished Windows source-build manifest")
    expected_commands = [{"argv": ["./mach", phase], "exit_code": 0} for phase in ("build", "package")]
    native.require(_canonical(manifest.get("commands")) == _canonical(expected_commands),
                   "Places build manifest must contain only successful build/package commands")
    commit, workflow = manifest.get("commit"), manifest.get("workflow_run")
    native.require(isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit) is not None,
                   "Invalid Places source commit")
    native.require(isinstance(workflow, str) and WORKFLOW.fullmatch(workflow) is not None,
                   "Invalid Places workflow-run identity")
    files = manifest.get("files")
    native.require(isinstance(files, dict) and bool(files), "Missing Places build file inventory")
    for name, checksum in files.items():
        native.safe_relative(name)
        native.require(isinstance(checksum, str) and SHA256.fullmatch(checksum) is not None,
                       "Invalid Places build inventory SHA-256")
    binary = native.regular_file(runtime, native.BINARY)
    native.require(0 < binary.stat().st_size <= native.MAX_PE_BYTES, "Missing or oversized Places native binary")
    binary_hash = native.digest(binary)
    native.require(files.get(native.BINARY) == binary_hash, "Places native binary differs from build inventory")

    log_data = _read(evidence, LOG, MAX_LOG)
    exit_data = _read(evidence, EXIT, MAX_JSON)
    exit_result = _object(exit_data)
    expected_exit = {"schema_version": 1, "phase": "xpcshell", "command_exit_code": 0, "tee_exit_code": 0}
    native.require(_canonical(exit_result) == _canonical(expected_exit),
                   "Places command or log capture failed, or exit evidence is malformed")
    results = _summary(log_data)
    return {
        "schema_version": 1, "passed": True, "release_eligible": False,
        "broader_privacy_security_acceptance": False,
        "build_manifest": _record(MANIFEST, manifest_data), "commit": commit, "workflow_run": workflow,
        "native_binary_sha256": binary_hash, "command": list(COMMAND), "results": results,
        "records": {"xpcshell_log": _record(LOG, log_data), "xpcshell_exit": _record(EXIT, exit_data)},
        "limitation": LIMITATION,
    }


def record(work: Path) -> dict:
    """Validate all inputs, then exclusively write the separate immutable report.

    Deliberately portable for inert tests; only the CLI enforces the hosted
    Windows workspace. The caller, not this recorder, owns execution sequencing.
    """
    evidence = work / "evidence"
    report = _derive(evidence, work / "candidate/PrivacyBrowser")
    data = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    native.require(len(data) <= MAX_JSON, "Places report exceeds size bound")
    with (evidence / REPORT).open("xb") as stream:
        stream.write(data)
    return report


def validate_retained(evidence: Path, runtime: Path) -> dict:
    """Recheck original raw evidence and all bindings, without executing anything."""
    report = _object(_read(evidence, REPORT, MAX_JSON))
    expected = _derive(evidence, runtime)
    native.require(_canonical(report) == _canonical(expected),
                   "Places report differs from raw evidence, manifest, command, or native binary")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True)
    args = parser.parse_args()
    native.require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
                   and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
                   "Places recording is restricted to ephemeral GitHub-hosted Windows")
    native.require(PureWindowsPath(args.work_root) == PureWindowsPath(r"D:\pb"),
                   "Expected isolated D:\\pb workspace")
    work = Path(args.work_root)
    native.require(not native.is_link_or_reparse(work)
                   and PureWindowsPath(str(work.resolve())) == PureWindowsPath(r"D:\pb"),
                   "Expected real isolated D:\\pb workspace")
    record(work)


if __name__ == "__main__":
    main()
