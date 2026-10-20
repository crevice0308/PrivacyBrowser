#!/usr/bin/env python3
"""Fast hosted-Windows synthetic safety preflight, without Gecko or MozillaBuild.

Only the reviewed portable NSIS compiler and the isolated synthetic entry points
execute. The production-shaped syntax output and inert browser are never run.
This fixture-only toolchain provenance cannot attest a Gecko source build.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import check_native_installer_syntax as syntax
import test_native_installer_synthetic as synthetic
import verify_windows_native as native
import windows_test_roots
from windows_native_evidence import NSIS_BYTES, NSIS_SHA256, NSIS_URL, download_nsis, safe_extract_zip, write_json

ROOT = Path(__file__).resolve().parents[1]


def provision_fixture_nsis(work: Path, source_commit: str) -> Path:
    """Use the same reviewed immutable tool pins, without a Gecko-source claim."""
    archive = work / "downloads/nsis-3.07-signed.zip"
    native.require(not archive.exists() and not archive.is_symlink(), "Fixture NSIS archive must be new")
    download_nsis(archive)
    native.require(archive.stat().st_size == NSIS_BYTES and native.digest(archive) == NSIS_SHA256,
                   "Fixture NSIS archive integrity mismatch")
    safe_extract_zip(archive, work / "tools/nsis", 64 * 1024**2)
    compiler = native.regular_file(work, syntax.COMPILER_RELATIVE)
    native.require(native.digest(compiler) == syntax.COMPILER_SHA256, "Fixture NSIS compiler integrity mismatch")
    write_json(work / "evidence/nsis-toolchain.json", {
        "schema_version": 1, "purpose": "synthetic-fixture-only", "source_commit": source_commit,
        "source_url": NSIS_URL, "archive_sha256": NSIS_SHA256, "archive_bytes": NSIS_BYTES,
        "definition": "reviewed immutable fixture pins; no Gecko source archive was downloaded or verified",
        "gecko_source_definition_verified": False, "source_compiled": False,
        "compiler": syntax.COMPILER_RELATIVE, "compiler_sha256": syntax.COMPILER_SHA256,
        "nsis_directory": syntax.NSIS_DIRECTORY, "licenses_retained_with_toolchain": True,
        "official_release": False, "release_eligible": False,
    })
    return compiler


def require_host() -> None:
    native.require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
                   and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
                   "Fast synthetic preflight is restricted to the ephemeral GitHub-hosted Windows runner")


def source_identity() -> dict:
    expected = os.environ.get("PB_EXPECTED_COMMIT", "")
    quality_run = os.environ.get("PB_QUALITY_RUN_ID", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    native.require(re.fullmatch(r"[a-f0-9]{40}", expected) is not None, "Expected exact successful-quality source SHA")
    native.require(all(re.fullmatch(r"[1-9][0-9]{0,19}", value) for value in (quality_run, run, attempt)),
                   "Expected exact quality/current run identity")
    native.require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is not None,
                   "Expected GitHub repository identity")
    actual = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True,
                            text=True, timeout=30).stdout.strip()
    native.require(actual == expected, "Synthetic checkout does not match the quality-tested source SHA")
    return {"source_commit": actual, "quality_run_id": quality_run, "workflow_run_id": run,
            "workflow_run_attempt": attempt, "repository": repository,
            "quality_run_url": f"https://github.com/{repository}/actions/runs/{quality_run}",
            "workflow_run_url": f"https://github.com/{repository}/actions/runs/{run}"}


def run_preflight(work: Path, identity: dict) -> dict:
    # Parent enumeration catches even dangling/reparse entries without following
    # them. Nothing in an existing fixture root is reused, removed or overwritten.
    native.require(not any(path.name.casefold() == work.name.casefold() for path in work.parent.iterdir()),
                   "Fast synthetic preflight requires a completely fresh fixture root")
    native.require(shutil.disk_usage(work.parent).free >= 1024**3, "At least 1 GiB free is required for synthetic fixtures")
    work.mkdir()
    for name in ("downloads", "evidence", "tmp"):
        (work / name).mkdir()
    report = {"schema_version": 1, "purpose": "fast isolated synthetic installer-safety preflight",
              **identity, "passed": False, "gecko_downloaded": False, "gecko_source_verified": False,
              "browser_compiled": False, "browser_executed": False, "mozilla_build_installed": False,
              "production_syntax_installer_executed": False, "executables_uploaded": False,
              "official_release": False, "release_eligible": False}
    previous = {key: os.environ.get(key) for key in ("TEMP", "TMP")}
    os.environ.update(TEMP=str(work / "tmp"), TMP=str(work / "tmp"))
    try:
        windows_test_roots.check(work, "fast-before")
        compiler = provision_fixture_nsis(work, identity["source_commit"])
        syntax.compile_syntax(work, compiler)
        compilation = syntax.compile_synthetic(work, compiler)
        execution = synthetic.execute_synthetic(work, compilation)
        native.require(execution.get("passed") is True and execution.get("synthetic_fixture_executed") is True
                       and execution.get("production_syntax_installer_executed") is False
                       and execution.get("browser_executed") is False and execution.get("release_eligible") is False,
                       "Synthetic safety checks did not complete successfully")
        windows_test_roots.check(work, "fast-after")
        report["passed"] = True
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report["error_type"] = type(error).__name__
        report["error"] = str(error)[:4096]
        raise
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        report["evidence_files"] = {
            path.name: native.digest(path) for path in sorted((work / "evidence").iterdir())
            if path.is_file() and path.suffix in (".json", ".log") and not native.is_link_or_reparse(path)
        }
        write_json(work / "evidence/installer-safety-preflight.json", report)
    return report


def main() -> None:
    require_host()
    identity = source_identity()
    # Deliberately no arbitrary path, version, URL or compiler override input.
    print(json.dumps(run_preflight(Path(r"D:\pb"), identity), indent=2))


if __name__ == "__main__":
    main()
