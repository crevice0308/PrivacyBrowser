#!/usr/bin/env python3
"""Require official same-task CoT SHA256 before executing fetched Rust tools."""
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.request

import run_mach_bootstrap
import verify_windows_native as native

QUEUE = "https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/"
ARTIFACT = "public/build/rustc.tar.zst"
MANIFEST_URL = re.compile(re.escape(QUEUE) + r"([A-Za-z0-9_-]{22})/artifacts/public%2Fbuild%2Frustc\.tar\.zst\Z")
MAX_METADATA = 2 * 1024 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        native.require(key not in result, "Duplicate JSON member in Rust integrity evidence")
        result[key] = value
    return result


def cot_url(task):
    native.require(re.fullmatch(r"[A-Za-z0-9_-]{22}", task) is not None, "Invalid Rust task ID")
    return QUEUE + task + "/artifacts/public%2Fchain-of-trust.json"


def trusted_metadata_url(url, task):
    cdn = re.escape("https://firefoxci.taskcluster-artifacts.net/" + task) + r"/[0-9]+/public/chain-of-trust\.json\Z"
    native.require(url == cot_url(task) or re.fullmatch(cdn, url) is not None,
                   "Unexpected Rust CoT URL or redirect")


def fetch_metadata(task):
    class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
        redirects = 0

        def redirect_request(self, request, fp, code, message, headers, newurl):
            self.redirects += 1
            native.require(self.redirects <= 5, "Too many Rust CoT redirects")
            trusted_metadata_url(newurl, task)
            return super().redirect_request(request, fp, code, message, headers, newurl)

    opener = urllib.request.build_opener(RestrictedRedirect())
    with opener.open(cot_url(task), timeout=30) as response:
        trusted_metadata_url(response.geturl(), task)
        data = response.read(MAX_METADATA + 1)
    native.require(0 < len(data) <= MAX_METADATA, "Invalid Rust CoT metadata size")
    return data


def verify(evidence):
    manifest = native.regular_file(evidence, "rust-toolchain-artifacts.json")
    native.require(manifest.stat().st_size <= MAX_METADATA, "Rust manifest is too large")
    entries = json.loads(manifest.read_bytes(), object_pairs_hook=unique_object)
    native.require(isinstance(entries, dict) and len(entries) == 1, "Expected exactly one Rust toolchain artifact")
    url, measured = next(iter(entries.items()))
    match = MANIFEST_URL.fullmatch(url)
    native.require(match is not None, "Rust artifact must use the exact official Taskcluster queue URL")
    native.require(isinstance(measured, dict) and set(measured) == {"sha256"}
                   and isinstance(measured["sha256"], str)
                   and re.fullmatch(r"[0-9a-f]{64}", measured["sha256"]) is not None,
                   "Missing measured Rust archive SHA256")
    task = match.group(1)
    cot_path = evidence / "rust-toolchain-cot.json"
    report_path = evidence / "rust-toolchain-integrity.json"
    native.require(not any(p.exists() or p.is_symlink() for p in (cot_path, report_path)),
                   "Rust integrity output already exists")
    raw = fetch_metadata(task)
    cot = json.loads(raw, object_pairs_hook=unique_object)
    artifacts = cot.get("artifacts") if isinstance(cot, dict) else None
    expected = artifacts.get(ARTIFACT) if isinstance(artifacts, dict) else None
    native.require(isinstance(expected, dict) and isinstance(expected.get("sha256"), str)
                   and re.fullmatch(r"[0-9a-f]{64}", expected["sha256"]) is not None,
                   "Official CoT must contain the expected Rust artifact SHA256")
    native.require(expected["sha256"] == measured["sha256"], "Rust archive SHA256 does not match official CoT")
    with cot_path.open("xb") as output:
        output.write(raw)
    report = {"schema_version": 1, "verified": True, "task_id": task,
              "artifact_url": url, "archive_sha256": measured["sha256"],
              "cot_url": cot_url(task), "cot_sha256": hashlib.sha256(raw).hexdigest(),
              "metadata_trust": "official Mozilla Taskcluster HTTPS metadata",
              "detached_cot_signature_independently_verified": False,
              "executables_run_by_this_check": False, "release_eligible": False}
    with report_path.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    native.require(len(sys.argv) == 1, "Rust integrity check does not accept path overrides")
    run_mach_bootstrap.require_source()
    verify(Path(r"D:\pb\evidence"))
