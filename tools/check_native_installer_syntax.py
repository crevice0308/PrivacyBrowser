#!/usr/bin/env python3
"""Compile the native NSIS template with inert input before building Gecko.

The production-shaped syntax output is never executed or staged for upload.
Optional --execute-fixtures compiles a separately isolated synthetic entry point
from the same template, then exercises only self-authored fixture data. Neither
test establishes real-browser correctness or release eligibility.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from package_windows_native import write_native_payload_include
import verify_windows_native as native
from windows_native_evidence import NSIS_SHA256, NSIS_URL, write_json
import test_native_installer_synthetic as synthetic

ROOT = Path(__file__).resolve().parents[1]
COMPILER_RELATIVE = "tools/nsis/nsis-3.07/Bin/makensis.exe"
COMPILER_SHA256 = "560e4a554093321420f65515c6f16082e879d8ca2149fb613beb24c864ded666"
NSIS_DIRECTORY = "tools/nsis/nsis-3.07"
FIXTURE_BYTES = b"INERT NSIS SYNTAX FIXTURE. This is text, not an executable browser. Never install or distribute.\n"


def check_compiler(work: Path, compiler: Path) -> Path:
    record = native.load_json(work / "evidence/nsis-toolchain.json")
    expected = native.regular_file(work, COMPILER_RELATIVE)
    native.require(compiler.resolve() == expected.resolve(), "Use only the exact pinned portable NSIS compiler")
    native.require(record.get("source_url") == NSIS_URL and record.get("archive_sha256") == NSIS_SHA256
                   and record.get("compiler") == COMPILER_RELATIVE
                   and record.get("nsis_directory") == NSIS_DIRECTORY
                   and record.get("compiler_sha256") == COMPILER_SHA256
                   and native.digest(expected) == COMPILER_SHA256,
                   "Pinned NSIS compiler evidence/hash mismatch")
    return expected


def compile_syntax(work: Path, compiler: Path) -> dict:
    """Bounded compiler-only preflight; this function never invokes its output."""
    compiler = check_compiler(work, compiler)
    fixture = work / "installer-syntax"
    evidence = work / "evidence"
    log_path = evidence / "installer-syntax.log"
    report_path = evidence / "installer-syntax.json"
    for path in (fixture, log_path, report_path):
        native.require(not path.exists() and not path.is_symlink(), "Syntax preflight path must be new: " + path.name)
    fixture.mkdir()
    runtime = fixture / "inert-runtime"
    icon = runtime / "distribution/privacybrowser/assets/privacybrowser.ico"
    icon.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "browser/privacy/assets/privacybrowser.ico", icon)
    for filename in ("LICENSE", "NOTICE"):
        shutil.copyfile(ROOT / filename, runtime / filename)
    (runtime / "privacybrowser.exe").write_bytes(FIXTURE_BYTES)
    include = fixture / "inert-payload.nsh"
    payload = write_native_payload_include(runtime, include)
    output = fixture / "DO-NOT-RUN-inert-installer-syntax.exe"
    template = ROOT / "packaging/windows/privacy-browser-native.nsi"
    prefix = "/" if os.name == "nt" else "-"
    command = [str(compiler), prefix + "V3", prefix + "NOCD",
               prefix + "DRUNTIME_DIR=" + str(runtime), prefix + "DPAYLOAD_INCLUDE=" + str(include),
               prefix + "DOUTPUT_FILE=" + str(output), str(template)]
    environment = os.environ.copy()
    environment["NSISDIR"] = str(work / NSIS_DIRECTORY)
    report = {"schema_version": 1, "purpose": "inert-payload NSIS compilation preflight only",
              "compiler_sha256": COMPILER_SHA256, "template_sha256": native.digest(template),
              "payload_include_sha256": native.digest(include), "fixture_payload": payload,
              "syntax_compiled": False, "generated_installer_executed": False,
              "generated_installer_uploaded": False, "browser_compiled": False,
              "official_release": False, "release_eligible": False, "command": command}
    with log_path.open("x", encoding="utf-8") as log:
        log.write("Inert payload syntax preflight. Generated setup must never be run or uploaded.\n")
        log.write(json.dumps({"argv": command}) + "\n")
        log.flush()
        try:
            result = subprocess.run(command, cwd=fixture, env=environment,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=120, check=False)
            report["exit_code"] = result.returncode
        except subprocess.TimeoutExpired:
            report["timed_out"] = True
            report["exit_code"] = None
        except OSError as error:
            report["compiler_start_error"] = str(error)
            report["exit_code"] = None
    passed = report.get("exit_code") == 0 and output.is_file() and not output.is_symlink()
    if passed:
        with output.open("rb") as stream:
            passed = stream.read(2) == b"MZ"
    report["syntax_compiled"] = passed
    report["log"] = {"path": log_path.name, "sha256": native.digest(log_path)}
    if passed:
        report["generated_installer_sha256"] = native.digest(output)
        report["generated_installer_bytes"] = output.stat().st_size
    write_json(report_path, report)
    native.require(passed, "Native NSIS syntax preflight failed; inspect installer-syntax.log before building Gecko")
    return report


def compile_synthetic(work: Path, compiler: Path) -> dict:
    """Compile a second, clearly identified nonproduction safety-only program."""
    compiler = check_compiler(work, compiler)
    fixture = work / "installer-syntax"
    production = native.load_json(work / "evidence/installer-syntax.json")
    native.require(production.get("syntax_compiled") is True
                   and production.get("generated_installer_executed") is False,
                   "Successful never-executed production syntax compilation must precede the synthetic harness")
    execution = work / synthetic.EXECUTION_RELATIVE
    log_path = work / "evidence/installer-synthetic-compile.log"
    report_path = work / "evidence/installer-synthetic-compilation.json"
    include = fixture / "synthetic-entry.nsh"
    for path in (execution, log_path, report_path, include):
        native.require(not path.exists() and not path.is_symlink(), "Synthetic compilation paths must be new")
    execution.mkdir()
    source = ROOT / "packaging/windows/native-safety-fixture.nsh"
    # Explicit UTF-8 BOM preserves the Japanese test path on every compiler locale.
    include.write_text(source.read_text(encoding="utf-8"), encoding="utf-8-sig")
    output = execution / synthetic.FIXTURE_EXE
    template = ROOT / "packaging/windows/privacy-browser-native.nsi"
    prefix = "/" if os.name == "nt" else "-"
    command = [str(compiler), prefix + "V3", prefix + "NOCD",
               prefix + "DRUNTIME_DIR=" + str(fixture / "inert-runtime"),
               prefix + "DPAYLOAD_INCLUDE=" + str(fixture / "inert-payload.nsh"),
               prefix + "DOUTPUT_FILE=" + str(output), prefix + "DPB_SYNTHETIC_FIXTURE=1",
               prefix + "DPB_SYNTHETIC_ROOT=" + synthetic.EXECUTION_WINDOWS,
               prefix + "DPB_SYNTHETIC_INCLUDE=" + str(include), str(template)]
    environment = os.environ.copy()
    environment["NSISDIR"] = str(work / NSIS_DIRECTORY)
    report = {"schema_version": 1, "purpose": "synthetic safety-only NSIS compilation",
              "compiler_sha256": COMPILER_SHA256, "template_sha256": native.digest(template),
              "fixture_entry_source_sha256": native.digest(source), "fixture_include_sha256": native.digest(include),
              "fixture_root": synthetic.EXECUTION_WINDOWS, "synthetic_fixture_compiled": False,
              "generated_installer_executed": False, "generated_installer_uploaded": False,
              "browser_compiled": False, "official_release": False, "release_eligible": False,
              "command": command}
    with log_path.open("x", encoding="utf-8") as log:
        log.write("Synthetic safety-only compilation. No browser or production installer execution.\n")
        log.write(json.dumps({"argv": command}) + "\n")
        log.flush()
        try:
            result = subprocess.run(command, cwd=fixture, env=environment,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=120, check=False)
            report["exit_code"] = result.returncode
        except subprocess.TimeoutExpired:
            report["timed_out"] = True
            report["exit_code"] = None
        except OSError as error:
            report["compiler_start_error"] = str(error)
            report["exit_code"] = None
    passed = report.get("exit_code") == 0 and output.is_file() and not output.is_symlink()
    if passed:
        with output.open("rb") as stream:
            passed = stream.read(2) == b"MZ"
    report["synthetic_fixture_compiled"] = passed
    report["log"] = {"path": log_path.name, "sha256": native.digest(log_path)}
    if passed:
        report["generated_installer_sha256"] = native.digest(output)
        report["generated_installer_bytes"] = output.stat().st_size
    write_json(report_path, report)
    native.require(passed, "Synthetic safety compilation failed; inspect installer-synthetic-compile.log")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--execute-fixtures", action="store_true",
                        help="Also compile and execute the separately isolated synthetic safety entry points")
    args = parser.parse_args()
    native.require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
                   and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
                   "Syntax preflight is restricted to the ephemeral GitHub-hosted Windows runner")
    work = args.work_root.resolve()
    native.require(str(work).lower() == "d:\\pb" and not work.is_symlink(), "Expected isolated D:\\pb workspace")
    print(json.dumps(compile_syntax(work, args.compiler), indent=2))
    if args.execute_fixtures:
        compilation = compile_synthetic(work, args.compiler)
        print(json.dumps(compilation, indent=2))
        print(json.dumps(synthetic.execute_synthetic(work, compilation), indent=2))


if __name__ == "__main__":
    main()
