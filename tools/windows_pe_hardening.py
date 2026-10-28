#!/usr/bin/env python3
"""Run the pinned static PE inspector; never execute the candidate binaries."""
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys

import verify_windows_native as native

# Reviewed against source-resolved official task/CoT and independent archive.
# See tests/results/winchecksec-2026-10-28-review.json; never accept runtime drift.
TOOL_SHA256 = "e63d6ccac4402d17494c60826a52051b50c7a2861b60742e3ed090a41f8a9277"
TOOL_SOURCE_REVISION = "93ffe67dbfc757bf6f440d80b8acf88e652ed60a"
# Exact x64 expectations in the pinned Gecko build/win32/autowinchecksec.py.
# Its pinned winchecksec 1.1.2 emits a flat boolean object, not the v3 schema.
CHECKS = ("aslr", "cfg", "dynamicBase", "gs", "isolation", "nx", "seh", "highEntropyVA")
BINARIES = ("privacybrowser.exe", "xul.dll")
MAX_OUTPUT = 64 * 1024


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def validate_output(data, name):
    native.require(isinstance(data, dict), "Expected flat winchecksec JSON")
    native.require(isinstance(data.get("path"), str)
                   and PureWindowsPath(data["path"]) == PureWindowsPath("D:/pb/candidate/PrivacyBrowser") / name,
                   "Winchecksec inspected an unexpected binary")
    for check in CHECKS:
        native.require(data.get(check) is True, "PE hardening check missing or failed: " + name + ":" + check)


def validate_retained(evidence, runtime):
    """Recheck raw results and exact payload hashes without executing any tool."""
    report = native.load_json(native.regular_file(evidence, "windows-pe-hardening.json"))
    native.require(type(report.get("schema_version")) is int and report["schema_version"] == 1
                   and report.get("passed") is True and report.get("candidate_executed") is False
                   and report.get("tool_sha256") == TOOL_SHA256
                   and report.get("tool_source_revision") == TOOL_SOURCE_REVISION,
                   "Missing matching pinned static PE hardening report")
    records = report.get("binaries")
    native.require(isinstance(records, dict) and set(records) == set(BINARIES), "Missing primary PE hardening results")
    for name in BINARIES:
        record = records[name]
        binary = native.regular_file(runtime, name)
        native.require(isinstance(record, dict) and record.get("sha256") == native.digest(binary),
                       "PE hardening payload digest mismatch")
        retained = {}
        for role, suffix in (("stdout", ".json"), ("stderr", ".stderr.log"), ("command", "-command.json")):
            metadata = record.get(role)
            expected = "pe-hardening-" + name + suffix
            native.require(isinstance(metadata, dict) and metadata.get("path") == expected,
                           "Unexpected PE hardening evidence path")
            path = native.regular_file(evidence, expected)
            native.require(path.stat().st_size <= MAX_OUTPUT and metadata.get("sha256") == native.digest(path),
                           "PE hardening evidence exceeds limit or digest mismatch")
            retained[role] = path
        command = native.load_json(retained["command"])
        native.require(type(command.get("exit_code")) is int and command["exit_code"] == 0
                       and command.get("timed_out") is False and command.get("tool_sha256") == TOOL_SHA256
                       and command.get("binary_sha256") == record["sha256"], "PE hardening command did not succeed")
        validate_output(native.load_json(retained["stdout"]), name)
    return report


def verified_tool(work):
    """Hash only, before compilation or inspection; never launch the tool."""
    tool = native.regular_file(work, "mozbuild/winchecksec/winchecksec.exe")
    native.require(native.digest(tool) == TOOL_SHA256, "Winchecksec binary differs from the reviewed tool")
    return tool


def collect(work):
    evidence, runtime = work / "evidence", work / "candidate/PrivacyBrowser"
    tool = verified_tool(work)
    records = {}
    for name in BINARIES:
        binary = native.regular_file(runtime, name)
        before = native.digest(binary)
        paths = {"stdout": evidence / ("pe-hardening-" + name + ".json"),
                 "stderr": evidence / ("pe-hardening-" + name + ".stderr.log"),
                 "command": evidence / ("pe-hardening-" + name + "-command.json")}
        code, timed_out = None, False
        with paths["stdout"].open("xb") as output, paths["stderr"].open("xb") as errors:
            try:
                result = subprocess.run([str(tool), "-j", str(binary)], stdin=subprocess.DEVNULL,
                                        stdout=output, stderr=errors, timeout=60, check=False)
                code = result.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
        write_json(paths["command"], {"exit_code": code, "timed_out": timed_out,
                                    "tool_sha256": TOOL_SHA256, "binary_sha256": before})
        native.require(code == 0 and not timed_out, "Pinned PE hardening inspection failed or timed out")
        native.require(native.digest(binary) == before, "Static inspection changed the candidate")
        for path in paths.values():
            native.require(path.stat().st_size <= MAX_OUTPUT, "PE hardening output exceeds bound")
        validate_output(native.load_json(paths["stdout"]), name)
        records[name] = {"sha256": before, **{role: {"path": path.name, "sha256": native.digest(path)}
                                             for role, path in paths.items()}}
    write_json(evidence / "windows-pe-hardening.json", {
        "schema_version": 1, "passed": True, "candidate_executed": False,
        "tool_sha256": TOOL_SHA256, "tool_source_revision": TOOL_SOURCE_REVISION, "binaries": records,
        "limitation": "Static PE flags/load-config inspection only, not runtime security acceptance or signing."})
    validate_retained(evidence, runtime)


def main():
    native.require(sys.argv[1:] in ([], ["--verify-tool"]), "PE hardening inspection does not accept path overrides")
    native.require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
                   and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
                   "PE inspection executes only on the ephemeral hosted Windows runner")
    work = Path(r"D:\pb")
    if sys.argv[1:] == ["--verify-tool"]:
        verified_tool(work)
        print(json.dumps({"schema_version": 1, "verified": True,
                          "tool_sha256": TOOL_SHA256, "tool_source_revision": TOOL_SOURCE_REVISION,
                          "tool_executed": False, "candidate_executed": False}))
    else:
        collect(work)


if __name__ == "__main__":
    main()
