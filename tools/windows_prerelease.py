#!/usr/bin/env python3
"""One-shot, unsigned development publication; never opens the production gate.

Only repository code executes. Downloaded Windows files are inspected as data.
Writes are intentionally not retried: a partial draft is left for inspection.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import struct
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
import urllib.request
import zipfile

import verify_windows_native as native
import windows_native_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]
REPO = "crevice0308/PrivacyBrowser"
REQUEST = "config/native-prerelease-request.json"
SETUP = evidence.SETUP_NAME
PORTABLE = "PrivacyBrowser-0.1.0-dev-win64-UNSIGNED.zip"
EVIDENCE = "PrivacyBrowser-0.1.0-dev-win64-evidence.zip"
ASSETS = tuple(sorted((SETUP, PORTABLE, EVIDENCE, "publication.json", "RELEASE-NOTES.txt", "SHA256SUMS")))
SHA = re.compile(r"[a-f0-9]{40}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
MAX_ARCHIVE = 4 * 1024**3
EXPECTED_COMMANDS = [{"argv": argv, "exit_code": 0} for argv in (
    ["./mach", "build"], ["./mach", "package"],
    ["./mach", "xpcshell-test", "toolkit/components/places/tests/unit/test_isURIVisited.js"])]


def require(value, message):
    native.require(value, message)


def integer(value, label):
    require(type(value) is int and value > 0, "Invalid " + label)
    return value


def checksum(value):
    require(isinstance(value, str) and HASH.fullmatch(value), "Invalid SHA-256")
    return value


def sha(value):
    require(isinstance(value, str) and SHA.fullmatch(value), "Invalid full commit SHA")
    return value


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def request_parent(commit):
    """Return the parent only for a one-parent, request-only commit; else None."""
    sha(commit)
    require(git("rev-parse", "HEAD").decode().strip() == commit, "Checkout is not the requested commit")
    parents = git("show", "-s", "--format=%P", commit).decode().strip().split()
    if len(parents) != 1:
        return None
    parent = sha(parents[0])
    changed = git("diff-tree", "--no-commit-id", "--no-renames", "--name-only", "-r", "-z", parent, commit)
    return parent if changed == (REQUEST + "\0").encode() else None


def read_request():
    value = native.load_json(ROOT / REQUEST)
    require(type(value.get("schema_version")) is int and value["schema_version"] == 1
            and type(value.get("enabled")) is bool, "Invalid publication request schema")
    if not value["enabled"]:
        require(set(value) == {"schema_version", "enabled"}, "Disabled request has unexpected fields")
        return value
    fields = {"schema_version", "enabled", "source_commit", "quality_run_id", "quality_run_attempt",
              "native_run_id", "native_run_attempt", "artifact_id", "artifact_sha256",
              "installer_sha256", "custom_home_review"}
    require(set(value) == fields, "Publication request fields do not match schema")
    sha(value["source_commit"])
    for field in ("quality_run_id", "quality_run_attempt", "native_run_id", "native_run_attempt", "artifact_id"):
        integer(value[field], field)
    for field in ("artifact_sha256", "installer_sha256"):
        checksum(value[field])
    review = value["custom_home_review"]
    require(isinstance(review, dict) and set(review) == {"passed", "screenshot_sha256"}
            and review["passed"] is True, "Actual custom-home visual review is required")
    checksum(review["screenshot_sha256"])
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class GitHub:
    """Fixed official API origins; no token is sent to artifact storage redirects."""
    def __init__(self, token):
        require(bool(token), "Missing built-in GitHub token")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, payload=None, upload=None, missing=False):
        require(path.startswith("/repos/" + REPO + "/") or path == "/repos/" + REPO,
                "Unexpected GitHub API path")
        origin = "https://uploads.github.com" if upload is not None else "https://api.github.com"
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "PrivacyBrowser-development-publisher"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        stream = None
        if upload is not None:
            stream = upload.open("rb")
            data = stream
            headers.update({"Content-Type": "application/octet-stream", "Content-Length": str(upload.stat().st_size)})
        try:
            request = urllib.request.Request(origin + path, data=data, headers=headers, method=method)
            with self.opener.open(request, timeout=300) as response:
                raw = response.read(native.MAX_JSON_BYTES + 1)
                require(len(raw) <= native.MAX_JSON_BYTES, "Oversized GitHub response")
                return json.loads(raw)
        except HTTPError as error:
            if missing and method == "GET" and error.code == 404:
                return None
            raise ValueError(f"GitHub {method} failed with HTTP {error.code}; inspect partial writes before retrying") from None
        finally:
            if stream is not None:
                stream.close()

    def get(self, path, missing=False):
        return self.request("GET", "/repos/" + REPO + path, missing=missing)

    def write(self, method, path, payload=None, upload=None):
        require(method in ("POST", "PATCH"), "Deletion/ref overwrites are not supported")
        return self.request(method, "/repos/" + REPO + path, payload, upload)

    def download(self, artifact_id, destination):
        integer(artifact_id, "artifact ID")
        url = f"https://api.github.com/repos/{REPO}/actions/artifacts/{artifact_id}/zip"
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        try:
            self.opener.open(request, timeout=60).close()
            raise ValueError("Expected GitHub's artifact-storage redirect")
        except HTTPError as error:
            require(error.code == 302, "Artifact download did not return a storage redirect")
            location = error.headers.get("Location", "")
        parsed = urlsplit(location)
        require(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and not parsed.fragment, "Unsafe artifact-storage URL")
        # GitHub supplied this signed URL. No authentication header follows it,
        # and further redirects are refused rather than forwarding credentials.
        with self.opener.open(urllib.request.Request(location), timeout=300) as response, destination.open("xb") as output:
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                require(total <= MAX_ARCHIVE, "Oversized artifact archive")
                output.write(block)
        require(total > 0, "Empty artifact archive")


def main_at(api, commit):
    require(api.get("/git/ref/heads/main")["object"]["sha"] == commit, "main changed; publication request is stale")


def check_run(api, run_id, attempt, commit, workflow, event, repository_id):
    integer(run_id, "run ID")
    integer(attempt, "run attempt")
    expected_path = ".github/workflows/" + workflow
    run = api.get(f"/actions/runs/{run_id}")
    # Check latest state first, so a newer retry cannot reuse an older success.
    # Then bind evidence timestamps to the explicit attempt endpoint.
    for attempt_path in (None, f"/actions/runs/{run_id}/attempts/{attempt}"):
        if attempt_path is not None:
            run = api.get(attempt_path)
        require(run.get("id") == run_id and run.get("run_attempt") == attempt
                and run.get("status") == "completed" and run.get("conclusion") == "success"
                and run.get("head_sha") == commit and run.get("head_branch") == "main"
                and run.get("path") in (expected_path, expected_path + "@main") and run.get("event") == event
                and run.get("repository", {}).get("id") == repository_id
                and run.get("head_repository", {}).get("id") == repository_id,
                "Wrong, stale, foreign, or unsuccessful workflow run/attempt")
    jobs = api.get(f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    entries = jobs.get("jobs", [])
    require(0 < len(entries) <= 100 and len(entries) == jobs.get("total_count"), "Incomplete workflow job inventory")
    expected = ("unit (ubuntu-24.04)", "unit (windows-2025)") if workflow == "quality.yml" else ("build",)
    for name in expected:
        matches = [job for job in entries if job.get("name") == name]
        require(len(matches) == 1 and matches[0].get("status") == "completed"
                and matches[0].get("conclusion") == "success", "Required job did not pass: " + name)
        if name == "build":
            for step in ("Compile, inspect, and smoke-test an unpublished native candidate",
                         "Collect only the successfully verified unpublished candidate"):
                found = [entry for entry in matches[0].get("steps", []) if entry.get("name") == step]
                require(len(found) == 1 and found[0].get("conclusion") == "success", "Required native step did not pass")
    return run


def artifact(api, artifact_id, run_id, commit, name, digest, repository_id):
    record = api.get(f"/actions/artifacts/{integer(artifact_id, 'artifact ID')}")
    owner = record.get("workflow_run", {})
    require(record.get("id") == artifact_id and record.get("expired") is False and record.get("name") == name
            and record.get("digest") == "sha256:" + checksum(digest)
            and owner.get("id") == run_id and owner.get("head_sha") == commit and owner.get("head_branch") == "main"
            and owner.get("repository_id") == repository_id and owner.get("head_repository_id") == repository_id,
            "Artifact identity, provenance, expiry, or digest mismatch")
    return record


def checked_inventory(directory):
    """SHA256SUMS is a closed file inventory, not shell input."""
    actual = native.inventory(directory)
    require("SHA256SUMS" in actual, "Missing SHA256SUMS")
    declared = {}
    sums = native.regular_file(directory, "SHA256SUMS")
    require(sums.stat().st_size <= 16 * 1024**2, "Oversized checksum inventory")
    for line in sums.read_text(encoding="utf-8").splitlines():
        require(len(line) > 66 and line[64:66] == "  ", "Malformed checksum line")
        digest, name = checksum(line[:64]), line[66:]
        native.safe_relative(name)
        require(name != "SHA256SUMS" and name not in declared, "Duplicate/self-referencing checksum")
        declared[name] = digest
    del actual["SHA256SUMS"]
    require(declared == actual, "Artifact files differ from complete checksum inventory")
    return actual


def retained_record(directory, record, expected):
    require(isinstance(record, dict) and record.get("path") == expected, "Unexpected retained evidence path")
    path = native.regular_file(directory, expected)
    require(record.get("sha256") == native.digest(path), "Retained evidence digest mismatch")
    return path


def validate_payload(root, request):
    checked_inventory(root)
    source = native.load_json(root / "evidence/build-evidence.json")
    require(source.get("commit") == request["source_commit"]
            and source.get("workflow_run") == f"https://github.com/{REPO}/actions/runs/{request['native_run_id']}"
            and source.get("commands") == EXPECTED_COMMANDS
            and all(type(command.get("exit_code")) is int for command in source["commands"]),
            "Source commit/run/command evidence mismatch")
    require(native.load_json(root / "evidence/commands.json").get("commands") == EXPECTED_COMMANDS,
            "Missing genuine native build/package/xpcshell results")
    retained_record(root / "evidence", source.get("xpcshell_log"), "xpcshell.log")
    unpacked = root.parent / "runtime-unpacked"
    evidence.safe_extract_zip(root / PORTABLE, unpacked)
    require(sorted(path.name for path in unpacked.iterdir()) == ["PrivacyBrowser"], "Unexpected runtime ZIP layout")
    runtime = unpacked / "PrivacyBrowser"
    report = native.validate_candidate(runtime, root / "evidence/build-evidence.json")
    binary_hash = report["native_binary_sha256"]
    for name in ("LICENSE", "NOTICE"):
        require(native.regular_file(runtime, name).read_bytes() == (ROOT / name).read_bytes(), "Project legal notice mismatch")
    require(native.regular_file(runtime, "NSIS-COPYING.txt").stat().st_size > 0, "Missing NSIS component notices")
    setup = native.regular_file(root, SETUP)
    require(native.digest(setup) == request["installer_sha256"], "Requested installer digest mismatch")
    native.validate_installer(setup)
    package = native.load_json(root / (SETUP + ".json"))
    require(type(package.get("schema_version")) is int and package["schema_version"] == 1 and package.get("build_kind") == "full-source-build-candidate-installer"
            and package.get("artifact") == SETUP and package.get("sha256") == request["installer_sha256"]
            and package.get("artifact_bytes") == setup.stat().st_size and package.get("native_binary_sha256") == binary_hash
            and package.get("source_build_evidence", {}).get("manifest_sha256") == native.digest(root / "evidence/build-evidence.json")
            and package.get("installer_compiled") is True and package.get("authenticode_signed") is False
            and package.get("official_release") is False and package.get("release_eligible") is False,
            "Installer packaging proof mismatch")
    evidence.validate_installer_test(native.load_json(root / "evidence/windows-installer-test.json"),
                                    request["installer_sha256"], binary_hash)
    static = native.load_json(root / "evidence/native-static-checks.json")
    require(static.get("static_candidate_checks_passed") is True and static.get("native_binary_sha256") == binary_hash,
            "Native static report mismatch")
    smoke = native.load_json(root / "evidence/windows-runtime-smoke.json")
    require(type(smoke.get("schema_version")) is int and smoke["schema_version"] == 1 and smoke.get("windows_smoke_checks_passed") is True
            and smoke.get("native_binary_sha256") == binary_hash and smoke.get("release_eligible") is False
            and smoke.get("interactive_windows_ui_tested") is False and smoke.get("privacy_security_acceptance_tested") is False,
            "Windows runtime report mismatch")
    commands = smoke.get("commands", [])
    require(len(commands) == 3, "Missing runtime smoke commands")
    for command, label in zip(commands, ("windows-version", "windows-local-render", "windows-privacybrowser-home")):
        require(type(command.get("exit_code")) is int and command["exit_code"] == 0
                and isinstance(command.get("argv"), list) and command["argv"], "Failed/missing runtime smoke command")
        argv = command["argv"]
        require(all(isinstance(value, str) for value in argv)
                and PureWindowsPath(argv[0]) == PureWindowsPath(r"D:\pb\candidate\PrivacyBrowser\privacybrowser.exe"),
                "Runtime smoke did not use the source-built candidate")
        if label == "windows-version":
            require(argv[1:] == ["--version"], "Unexpected version smoke command")
        else:
            expected_url = ("file:///D:/pb/evidence/rendering-smoke.html" if label == "windows-local-render"
                            else "chrome://privacybrowser/content/home.html")
            require(len(argv) == 10 and argv[1:4] == ["--headless", "--no-remote", "--profile"]
                    and PureWindowsPath(argv[4]) == PureWindowsPath("D:/pb/" + label + "-profile")
                    and argv[5] == "--screenshot"
                    and PureWindowsPath(argv[6]) == PureWindowsPath("D:/pb/evidence/" + label + ".png")
                    and argv[7:] == ["--window-size", "800,600", expected_url], "Unexpected rendering smoke command")
        retained_record(root / "evidence", command.get("log"), label + ".log")
    screenshots = smoke.get("screenshots", [])
    require(len(screenshots) == 2, "Missing runtime screenshots")
    for record, label in zip(screenshots, ("windows-local-render", "windows-privacybrowser-home")):
        path = retained_record(root / "evidence", record, label + ".png")
        with path.open("rb") as stream:
            header = stream.read(24)
        require(header[:16] == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" and len(header) == 24
                and struct.unpack(">II", header[16:24]) == (800, 600), "Unexpected smoke screenshot header")
    require(native.digest(root / "evidence/windows-privacybrowser-home.png") == request["custom_home_review"]["screenshot_sha256"],
            "Visual review does not match this candidate's home screenshot")
    toolchain = native.load_json(root / "evidence/toolchain.json")
    require(toolchain.get("commit") == request["source_commit"] and str(toolchain.get("run_id")) == str(request["native_run_id"])
            and toolchain.get("ephemeral_github_runner") is True, "Hosted toolchain evidence mismatch")


def release_notes(request):
    source = request["source_commit"]
    return (
        "PrivacyBrowser 0.1.0-dev / Unsigned Windows development prerelease\n\n"
        "未署名の開発用 Pre-release です。固定した Gecko ソースからビルドし、ネイティブ名・アイコン、"
        "限定的な描画試験、ユーザー単位のインストール／アンインストール試験を実施しました。"
        "独自ホームの記録画像は目視確認済みです。通常の対話操作、Explorer 表示、広範なプライバシー／"
        "セキュリティ要件、クラッシュ時の履歴消去は未検証です。自動更新は無効です。Windows 11 の"
        "実機検証済みとは扱わず、機密データでの利用は避けてください。正式版の公開ゲートは閉じたままです。\n\n"
        "Unsigned development prerelease built from pinned Gecko source. Native identity/icon checks, bounded rendering "
        "smoke tests, and per-user install/uninstall tests passed on a hosted Windows runner. The retained custom-home "
        "screenshot was visually reviewed. Normal interactive browsing, Explorer display, broader privacy/security "
        "requirements, and crash-safe history cleanup remain unverified. Automatic updates are disabled. This is not "
        "Windows 11 hardware validation; avoid sensitive browsing. The production release gate remains closed. "
        "No project Authenticode signature or independent reproducible-build attestation is claimed.\n\n"
        f"Source commit: https://github.com/{REPO}/tree/{source}\n"
        f"Source build and retained tests: https://github.com/{REPO}/actions/runs/{request['native_run_id']}\n"
        f"Source quality tests: https://github.com/{REPO}/actions/runs/{request['quality_run_id']}\n"
        f"Exact upstream source and reconstruction instructions: https://github.com/{REPO}/blob/{source}/README.md\n"
        "LICENSE, NOTICE, upstream resources and NSIS component notices remain in the runtime. "
        "UNPUBLISHED-CANDIDATE.txt and false release_eligible flags in the evidence describe the original staging checks; "
        "this separate gate authorizes only the explicitly requested development prerelease.\n")


def stage_assets(root, output, request, commit):
    require(not output.exists(), "Publication staging directory already exists")
    output.mkdir()
    for name in (SETUP, PORTABLE):
        shutil.copyfile(root / name, output / name)
    with zipfile.ZipFile(output / EVIDENCE, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in native.inventory(root):
            if name not in (SETUP, PORTABLE):
                archive.write(root / name, name)
    manifest = {"schema_version": 1, "repository": REPO, "request_commit": commit,
                "request_sha256": native.digest(ROOT / REQUEST), "source_commit": request["source_commit"],
                "tag": "v0.1.0-dev-" + request["source_commit"][:12], "assets": list(ASSETS),
                "original_artifact_id": request["artifact_id"], "original_artifact_sha256": request["artifact_sha256"],
                "development_prerelease_eligible": True, "production_release_eligible": False}
    evidence.write_json(output / "publication.json", manifest)
    (output / "RELEASE-NOTES.txt").write_text(release_notes(request), encoding="utf-8")
    (output / "SHA256SUMS").write_text("".join(digest + "  " + name + "\n" for name, digest in native.inventory(output).items()), encoding="utf-8")


def verify(api, event, commit, output):
    repository = api.get("")
    require(repository.get("full_name") == REPO and repository.get("default_branch") == "main", "Unexpected repository")
    repository_id = integer(repository.get("id"), "repository ID")
    trigger = event.get("workflow_run", {})
    check_run(api, trigger.get("id"), trigger.get("run_attempt"), commit, "quality.yml", "push", repository_id)
    parent = request_parent(commit)
    if parent is None:
        return False
    request = read_request()
    if not request["enabled"]:
        return False
    require(parent == request["source_commit"], "Request must directly follow the exact verified source commit")
    require(native.load_json(ROOT / "config/esr.json").get("release_allowed") is False, "Production release switch must remain closed")
    main_at(api, commit)
    check_run(api, request["quality_run_id"], request["quality_run_attempt"], parent, "quality.yml", "push", repository_id)
    run = check_run(api, request["native_run_id"], request["native_run_attempt"], parent, "windows-native.yml", "workflow_run", repository_id)
    record = artifact(api, request["artifact_id"], request["native_run_id"], parent,
                      "UNSIGNED-UNPUBLISHED-PrivacyBrowser-windows-" + parent, request["artifact_sha256"], repository_id)
    require(datetime.fromisoformat(run["run_started_at"]) <= datetime.fromisoformat(record["created_at"])
            <= datetime.fromisoformat(run["updated_at"]), "Artifact is not from the requested native attempt")
    with tempfile.TemporaryDirectory(prefix="privacybrowser-prerelease-verify-") as temporary:
        work = Path(temporary)
        archive = work / "candidate.zip"
        api.download(request["artifact_id"], archive)
        require(native.digest(archive) == request["artifact_sha256"], "Downloaded candidate archive digest mismatch")
        root = work / "candidate"
        evidence.safe_extract_zip(archive, root)
        validate_payload(root, request)
        stage_assets(root, output, request, commit)
    return True


def publish(api, commit, bundle_id, bundle_digest, own_run_id, own_attempt):
    request = read_request()
    require(request["enabled"] and request_parent(commit) == request["source_commit"], "Invalid explicit publication request")
    require(native.load_json(ROOT / "config/esr.json").get("release_allowed") is False, "Production release gate must remain closed")
    repository_id = integer(api.get("").get("id"), "repository ID")
    artifact(api, bundle_id, own_run_id, commit, f"approved-development-prerelease-{own_run_id}-{own_attempt}", bundle_digest, repository_id)
    with tempfile.TemporaryDirectory(prefix="privacybrowser-prerelease-publish-") as temporary:
        work = Path(temporary)
        archive = work / "approved.zip"
        api.download(bundle_id, archive)
        require(native.digest(archive) == bundle_digest, "Approved publication bundle digest mismatch")
        root = work / "approved"
        evidence.safe_extract_zip(archive, root)
        checked_inventory(root)
        require(tuple(sorted(native.inventory(root))) == ASSETS, "Unexpected publication asset inventory")
        manifest = native.load_json(root / "publication.json")
        tag = "v0.1.0-dev-" + request["source_commit"][:12]
        require(manifest == {"schema_version": 1, "repository": REPO, "request_commit": commit,
                "request_sha256": native.digest(ROOT / REQUEST), "source_commit": request["source_commit"],
                "tag": tag, "assets": list(ASSETS), "original_artifact_id": request["artifact_id"],
                "original_artifact_sha256": request["artifact_sha256"], "development_prerelease_eligible": True,
                "production_release_eligible": False}, "Publication manifest mismatch")
        notes = (root / "RELEASE-NOTES.txt").read_text(encoding="utf-8")
        require(notes == release_notes(request), "Publication limitations changed")
        main_at(api, commit)
        require(api.get("/git/ref/tags/" + tag, missing=True) is None, "Tag already exists; no overwrite")
        require(api.get("/releases/tags/" + tag, missing=True) is None, "Release already exists; no overwrite")
        # The only mutations are create-new-tag, create-draft, append new assets,
        # and publish that same draft. Never patch refs or delete/replace assets.
        api.write("POST", "/git/refs", {"ref": "refs/tags/" + tag, "sha": request["source_commit"]})
        require(api.get("/git/ref/tags/" + tag)["object"]["sha"] == request["source_commit"], "New tag target mismatch")
        release = api.write("POST", "/releases", {"tag_name": tag, "target_commitish": request["source_commit"],
            "name": "PrivacyBrowser 0.1.0-dev (UNSIGNED) " + request["source_commit"][:12], "body": notes,
            "draft": True, "prerelease": True, "make_latest": "false", "generate_release_notes": False})
        release_id = integer(release.get("id"), "new release ID")
        require(release.get("draft") is True and release.get("prerelease") is True and release.get("tag_name") == tag,
                "Unexpected draft response; stop and inspect")
        expected_upload = f"https://uploads.github.com/repos/{REPO}/releases/{release_id}/assets{{?name,label}}"
        require(release.get("upload_url") == expected_upload, "Unexpected release upload destination")
        expected = {name: (root / name).stat().st_size for name in ASSETS}
        hashes = native.inventory(root)
        for name in ASSETS:
            item = api.write("POST", f"/releases/{release_id}/assets?name=" + quote(name, safe=""), upload=root / name)
            require(item.get("name") == name and item.get("state") == "uploaded" and item.get("size") == expected[name]
                    and item.get("digest") == "sha256:" + hashes[name], "Uploaded asset mismatch; draft remains unpublished")
        items = api.get(f"/releases/{release_id}/assets?per_page=100")
        require(isinstance(items, list) and len(items) == len(ASSETS) and {item.get("name") for item in items} == set(ASSETS),
                "Draft asset inventory mismatch")
        for item in items:
            require(item.get("state") == "uploaded" and item.get("size") == expected[item["name"]]
                    and item.get("digest") == "sha256:" + hashes[item["name"]], "Draft asset verification failed")
        main_at(api, commit)
        require(api.get("/git/ref/tags/" + tag)["object"]["sha"] == request["source_commit"], "Tag changed before publication")
        api.write("PATCH", f"/releases/{release_id}", {"draft": False, "prerelease": True, "make_latest": "false"})
        final = api.get(f"/releases/{release_id}")
        final_assets = api.get(f"/releases/{release_id}/assets?per_page=100")
        require(isinstance(final_assets, list) and len(final_assets) == len(ASSETS)
                and {item.get("name") for item in final_assets} == set(ASSETS)
                and all(item.get("state") == "uploaded" and item.get("size") == expected[item["name"]]
                        and item.get("digest") == "sha256:" + hashes[item["name"]] for item in final_assets),
                "Published asset verification failed; inspect before retrying")
        require(final.get("draft") is False and final.get("prerelease") is True and final.get("tag_name") == tag
                and api.get("/git/ref/tags/" + tag)["object"]["sha"] == request["source_commit"],
                "Publication outcome is uncertain; inspect before retrying")
        return {"url": f"https://github.com/{REPO}/releases/tag/{tag}", "immutable": final.get("immutable") is True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("classify", "verify", "publish"))
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bundle-id", type=int)
    parser.add_argument("--bundle-sha256")
    args = parser.parse_args()
    sha(args.commit)
    if args.mode == "classify":
        print("build_required=" + str(request_parent(args.commit) is None).lower())
        return
    require(os.environ.get("GITHUB_REPOSITORY") == REPO and os.environ.get("GITHUB_EVENT_NAME") == "workflow_run"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted", "Only this repository's hosted publication workflow is supported")
    api = GitHub(os.environ.get("GH_TOKEN", ""))
    if args.mode == "verify":
        require(args.output is not None, "Missing staging output")
        ready = verify(api, native.load_json(Path(os.environ["GITHUB_EVENT_PATH"])), args.commit, args.output)
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write("ready=" + str(ready).lower() + "\n")
    else:
        print(json.dumps(publish(api, args.commit, integer(args.bundle_id, "bundle ID"), checksum(args.bundle_sha256),
                                 int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"])), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        raise SystemExit("Development prerelease stopped: " + str(error))
