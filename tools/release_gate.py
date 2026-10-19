#!/usr/bin/env python3
"""Offline release eligibility checks; this tool never publishes or installs.

This v0.1.0 project intentionally has no deployed release signing/update service.
Developer artifacts are rejected even if a caller changes a filename to 'release'.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CHECKS = {
    "linux_source_build", "windows_source_build", "linux_install_uninstall", "windows_install_uninstall",
    "linux_startup_shutdown", "windows_startup_shutdown", "unicode_search", "history_off_after_exit",
    "history_on_off", "bookmarks_persist", "session_native_guard", "task_manager_isolation", "crash_recovery",
    "update_failure_rollback", "memory_leak", "linux_1gb_ram", "security_review", "windows_authenticode",
    "signed_apt_repository", "signed_windows_update",
}


def eligibility(manifest: dict, config: dict, trust: dict, artifact_root: Path) -> list[str]:
    problems = []
    if config.get("release_allowed") is not True:
        problems.append("Project release switch is disabled pending source builds, independent signing and validation")
    if manifest.get("build_kind") != "full-source-build" or manifest.get("source_compiled") is not True:
        problems.append("An independently compiled Gecko source build is required")
    if manifest.get("native_branding_replaced") is not True:
        problems.append("Independent native application branding is required")
    if manifest.get("upstream_version") != config.get("upstream_version"):
        problems.append("Candidate ESR version differs from the reviewed pin")
    if not trust.get("privacy_browser_release_key_fingerprints") or not trust.get("windows_publisher_thumbprints"):
        problems.append("Independent project signing identities have not been provisioned")
    checks = manifest.get("checks", {})
    for name in sorted(REQUIRED_CHECKS):
        result = checks.get(name, {})
        if result.get("status") != "passed" or not result.get("evidence_sha256"):
            problems.append(f"Required test has not passed with retained evidence: {name}")
    artifacts = manifest.get("artifacts", [])
    if not artifacts:
        problems.append("No release artifacts supplied")
    for artifact in artifacts:
        relative = Path(artifact.get("path", ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            problems.append("Invalid artifact path")
            continue
        path = artifact_root / relative
        if not path.is_file() or artifact_root.resolve() not in path.resolve().parents:
            problems.append("Release artifact missing or outside artifact root")
            continue
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha512").hexdigest()
        if digest != artifact.get("sha512"):
            problems.append("Release artifact hash mismatch")
    # An evidence manifest alone cannot prove signatures. Do not let this initial
    # scaffold accidentally become a production publisher by toggling JSON flags.
    problems.append("Production signature verification/update deployment is not implemented in v0.1.0; publication is blocked")
    return problems


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/esr.json")
    parser.add_argument("--trust", type=Path, default=ROOT / "config/update-trust.json")
    args = parser.parse_args()
    failures = eligibility(json.loads(args.manifest.read_text()), json.loads(args.config.read_text()), json.loads(args.trust.read_text()), args.artifacts)
    print(json.dumps({"eligible": not failures, "failures": failures}, indent=2))
    raise SystemExit(1 if failures else 0)
