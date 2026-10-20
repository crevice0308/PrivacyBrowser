"""Offline publication boundary tests: inert bytes and fake GitHub APIs only.

These tests do not compile Gecko, execute Windows files, upload, tag, or publish.
"""
from pathlib import Path
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import windows_prerelease as publisher
from windows_autoconfig_smoke_test import make_retained_fixture
from windows_places_smoke_test import make_retained_fixture as make_places_fixture

C, R = "c" * 40, "d" * 40
RID, NID, QID, TRIGGER, BUNDLE = 71, 72, 73, 74, 75


def hashed(data):
    return hashlib.sha256(data).hexdigest()


def request():
    return {"schema_version": 1, "enabled": True, "source_commit": C, "quality_run_id": QID,
            "quality_run_attempt": 1, "native_run_id": NID, "native_run_attempt": 1,
            "artifact_id": 70, "artifact_sha256": "a" * 64, "installer_sha256": hashed(b"inert setup"),
            "custom_home_review": {"passed": True, "screenshot_sha256": "b" * 64}}


def json_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def sums(root):
    values = publisher.native.inventory(root)
    values.pop("SHA256SUMS", None)
    (root / "SHA256SUMS").write_text("".join(digest + "  " + name + "\n" for name, digest in values.items()))


def make_zip(root, destination):
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())


class FakeAPI:
    def __init__(self, archive, digest, own_run=RID):
        self.archive = archive
        self.digest = digest
        self.own_run = own_run
        self.writes = []
        self.assets = []
        self.tag = None
        self.release = None
        self.existing_release = False
        self.fail_asset = False
        self.tamper_asset = False
        self.stale_main = False
        self.foreign_artifact = False
        self.asset_meta = None

    def get(self, path, missing=False):
        if path == "":
            return {"id": 31, "full_name": publisher.REPO, "default_branch": "main"}
        if path == "/git/ref/heads/main":
            return {"object": {"sha": C if self.stale_main else R}}
        if path.startswith("/actions/artifacts/"):
            if self.asset_meta is not None:
                return copy.deepcopy(self.asset_meta)
            return {"id": BUNDLE, "name": f"approved-development-prerelease-{self.own_run}-1", "expired": False,
                    "digest": "sha256:" + self.digest, "workflow_run": {"id": self.own_run, "head_sha": R,
                    "head_branch": "main", "repository_id": 999 if self.foreign_artifact else 31, "head_repository_id": 31}}
        if path.startswith("/git/ref/tags/"):
            return {"object": {"sha": self.tag}} if self.tag else None
        if path.startswith("/releases/tags/"):
            return {"id": 10} if self.existing_release else None
        if path == "/releases/10/assets?per_page=100":
            return copy.deepcopy(self.assets)
        if path == "/releases/10":
            return copy.deepcopy(self.release)
        raise AssertionError("Unexpected fake GET: " + path)

    def download(self, artifact_id, destination):
        destination.write_bytes(self.archive.read_bytes())

    def write(self, method, path, payload=None, upload=None):
        self.writes.append((method, path, copy.deepcopy(payload)))
        if path == "/git/refs":
            if self.tag:
                raise ValueError("Tag already exists")
            self.tag = payload["sha"]
            return {"object": {"sha": self.tag}}
        if method == "POST" and path == "/releases":
            self.release = dict(payload, id=10, immutable=False,
                upload_url=f"https://uploads.github.com/repos/{publisher.REPO}/releases/10/assets{{?name,label}}")
            return copy.deepcopy(self.release)
        if method == "POST" and path.startswith("/releases/10/assets?name="):
            if self.fail_asset:
                raise TimeoutError("simulated uncertain upload")
            item = {"name": upload.name, "state": "uploaded", "size": upload.stat().st_size,
                    "digest": "sha256:" + publisher.native.digest(upload)}
            if self.tamper_asset:
                item["digest"] = "sha256:" + "e" * 64
            self.assets.append(item)
            return copy.deepcopy(item)
        if method == "PATCH" and path == "/releases/10":
            self.release.update(payload)
            return copy.deepcopy(self.release)
        raise AssertionError("Unexpected fake write: " + method + " " + path)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.repo = self.work / "repo"
        self.repo.mkdir()
        self.request = request()
        json_file(self.repo / publisher.REQUEST, self.request)
        json_file(self.repo / "config/esr.json", {"release_allowed": False})
        self.patch = mock.patch.object(publisher, "ROOT", self.repo)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def bundle(self):
        candidate = self.work / "inert-candidate"
        candidate.mkdir()
        (candidate / publisher.SETUP).write_bytes(b"inert setup")
        (candidate / publisher.PORTABLE).write_bytes(b"inert runtime ZIP stand-in")
        (candidate / "UNPUBLISHED-CANDIDATE.txt").write_text("inert retained original note")
        sums(candidate)
        staged = self.work / "approved"
        publisher.stage_assets(candidate, staged, self.request, R)
        archive = self.work / "approved.zip"
        make_zip(staged, archive)
        return staged, archive, publisher.native.digest(archive)

    def call_publish(self, api, digest):
        with mock.patch.object(publisher, "request_parent", return_value=C):
            return publisher.publish(api, R, BUNDLE, digest, RID, 1)

    def test_disabled_request_has_no_activation_fields(self):
        json_file(self.repo / publisher.REQUEST, {"schema_version": 1, "enabled": False})
        self.assertFalse(publisher.read_request()["enabled"])
        json_file(self.repo / publisher.REQUEST, {"schema_version": 1, "enabled": False, "source_commit": C})
        with self.assertRaises(ValueError):
            publisher.read_request()

    def test_request_rejects_unknown_fields_and_unsafe_types(self):
        for field, value in (("artifact_id", True), ("source_commit", "main"), ("artifact_sha256", "bad"),
                             ("enabled", "true"), ("schema_version", True), ("extra", "ignored")):
            changed = dict(self.request, **{field: value})
            json_file(self.repo / publisher.REQUEST, changed)
            with self.subTest(field=field), self.assertRaises(ValueError):
                publisher.read_request()

    def test_complete_explicit_enabled_request_remains_valid_for_quality_ci(self):
        self.assertEqual(publisher.read_request(), self.request)
        json_file(self.repo / publisher.REQUEST, {"schema_version": 1, "enabled": False})
        self.assertEqual(publisher.read_request(), {"schema_version": 1, "enabled": False})

    def test_visual_review_must_explicitly_pass_and_pin_a_hash(self):
        for review in ({"passed": False, "screenshot_sha256": "b" * 64}, {"passed": True},
                       {"passed": True, "screenshot_sha256": "wrong"}):
            json_file(self.repo / publisher.REQUEST, dict(self.request, custom_home_review=review))
            with self.assertRaises(ValueError):
                publisher.read_request()

    def test_only_single_parent_exact_request_diff_skips_build(self):
        for parents, diff, expected in ((C, publisher.REQUEST + "\0", C),
            (C, publisher.REQUEST + "\0README.md\0", None), (C + " " + R, publisher.REQUEST + "\0", None),
            ("", "", None), (C, "", None), (C, "config/native-prerelease-request.json.bak\0", None)):
            responses = [(R + "\n").encode(), (parents + "\n").encode(), diff.encode()]
            with self.subTest(parents=parents, diff=diff), mock.patch.object(publisher, "git", side_effect=responses):
                self.assertEqual(publisher.request_parent(R), expected)

    def test_classifier_rejects_checkout_mismatch_and_bad_commit(self):
        with mock.patch.object(publisher, "git", return_value=(C + "\n").encode()), self.assertRaises(ValueError):
            publisher.request_parent(R)
        with self.assertRaises(ValueError):
            publisher.request_parent("main")

    def test_manifest_rejects_extra_missing_changed_and_unsafe_files(self):
        root = self.work / "checks"
        root.mkdir()
        (root / "one.txt").write_bytes(b"one")
        sums(root)
        self.assertEqual(publisher.checked_inventory(root), {"one.txt": hashed(b"one")})
        (root / "extra.txt").write_bytes(b"extra")
        with self.assertRaises(ValueError):
            publisher.checked_inventory(root)
        (root / "extra.txt").unlink()
        (root / "one.txt").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            publisher.checked_inventory(root)
        (root / "SHA256SUMS").write_text("a" * 64 + "  ../escape\n")
        with self.assertRaises(ValueError):
            publisher.checked_inventory(root)

    def test_publish_creates_new_tag_draft_assets_then_publishes(self):
        _, archive, digest = self.bundle()
        api = FakeAPI(archive, digest)
        result = self.call_publish(api, digest)
        self.assertFalse(result["immutable"])
        self.assertIn("v0.1.0-dev-" + C[:12], result["url"])
        self.assertEqual(api.writes[0], ("POST", "/git/refs", {"ref": "refs/tags/v0.1.0-dev-" + C[:12], "sha": C}))
        self.assertTrue(api.writes[1][2]["draft"])
        self.assertTrue(api.writes[1][2]["prerelease"])
        self.assertEqual(api.writes[1][2]["make_latest"], "false")
        self.assertEqual(api.writes[-1], ("PATCH", "/releases/10", {"draft": False, "prerelease": True, "make_latest": "false"}))
        self.assertEqual(len(api.assets), len(publisher.ASSETS))
        self.assertFalse(any(method == "DELETE" or path.startswith("/git/refs/") for method, path, _ in api.writes))

    def test_existing_tag_or_release_never_writes(self):
        _, archive, digest = self.bundle()
        for kind in ("tag", "release"):
            api = FakeAPI(archive, digest)
            if kind == "tag":
                api.tag = C
            else:
                api.existing_release = True
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.call_publish(api, digest)
            self.assertEqual(api.writes, [])

    def test_stale_main_and_foreign_artifact_never_write(self):
        _, archive, digest = self.bundle()
        for flag in ("stale_main", "foreign_artifact"):
            api = FakeAPI(archive, digest)
            setattr(api, flag, True)
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.call_publish(api, digest)
            self.assertEqual(api.writes, [])

    def test_digest_mismatch_never_writes(self):
        _, archive, digest = self.bundle()
        api = FakeAPI(archive, "f" * 64)
        with self.assertRaises(ValueError):
            self.call_publish(api, "f" * 64)
        self.assertEqual(api.writes, [])

    def test_uncertain_or_mismatched_asset_leaves_unpublished_draft(self):
        _, archive, digest = self.bundle()
        for flag in ("fail_asset", "tamper_asset"):
            api = FakeAPI(archive, digest)
            setattr(api, flag, True)
            with self.subTest(flag=flag), self.assertRaises((ValueError, TimeoutError)):
                self.call_publish(api, digest)
            self.assertTrue(api.release["draft"])
            self.assertFalse(any(method == "PATCH" for method, _, _ in api.writes))
            self.assertEqual(len(api.writes), 3)  # no automatic retry/delete

    def test_changed_review_request_after_verification_never_writes(self):
        _, archive, digest = self.bundle()
        changed = copy.deepcopy(self.request)
        changed["custom_home_review"]["screenshot_sha256"] = "e" * 64
        json_file(self.repo / publisher.REQUEST, changed)
        api = FakeAPI(archive, digest)
        with self.assertRaises(ValueError):
            self.call_publish(api, digest)
        self.assertEqual(api.writes, [])

    def test_wrong_run_attempt_or_missing_quality_matrix_fails(self):
        run = {"id": QID, "run_attempt": 1, "status": "completed", "conclusion": "success", "head_sha": C,
               "head_branch": "main", "path": ".github/workflows/quality.yml", "event": "push",
               "repository": {"id": 31}, "head_repository": {"id": 31}}
        jobs = {"total_count": 2, "jobs": [{"name": "unit (" + osname + ")", "status": "completed", "conclusion": "success"}
                for osname in ("ubuntu-24.04", "windows-2025")]}
        api = mock.Mock()
        api.get.side_effect = [run, run, jobs]
        self.assertEqual(publisher.check_run(api, QID, 1, C, "quality.yml", "push", 31), run)
        for changed in (dict(run, run_attempt=2), dict(run, head_sha=R), dict(run, conclusion="failure")):
            api.get.side_effect = [changed, run, jobs]
            with self.assertRaises(ValueError):
                publisher.check_run(api, QID, 1, C, "quality.yml", "push", 31)
        api.get.side_effect = [run, run, {"total_count": 1, "jobs": jobs["jobs"][:1]}]
        with self.assertRaises(ValueError):
            publisher.check_run(api, QID, 1, C, "quality.yml", "push", 31)

    def test_documented_main_path_suffix_and_explicit_attempt_metadata(self):
        run = {"id": QID, "run_attempt": 1, "status": "completed", "conclusion": "success", "head_sha": C,
               "head_branch": "main", "path": ".github/workflows/quality.yml@main", "event": "push",
               "repository": {"id": 31}, "head_repository": {"id": 31}}
        attempt = dict(run, run_started_at="2026-10-19T09:00:00Z", updated_at="2026-10-19T10:01:00Z")
        jobs = {"total_count": 2, "jobs": [{"name": "unit (" + osname + ")", "status": "completed", "conclusion": "success"}
                for osname in ("ubuntu-24.04", "windows-2025")]}
        api = mock.Mock()
        api.get.side_effect = [run, attempt, jobs]
        self.assertEqual(publisher.check_run(api, QID, 1, C, "quality.yml", "push", 31), attempt)
        self.assertIn(mock.call(f"/actions/runs/{QID}/attempts/1"), api.get.call_args_list)
        for bad in (dict(attempt, path=".github/workflows/quality.yml@other"), dict(attempt, run_attempt=2),
                    dict(attempt, head_sha=R), dict(attempt, conclusion="failure")):
            api.get.side_effect = [run, bad, jobs]
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                publisher.check_run(api, QID, 1, C, "quality.yml", "push", 31)

    def test_native_success_without_build_and_upload_steps_fails(self):
        run = {"id": NID, "run_attempt": 1, "status": "completed", "conclusion": "success", "head_sha": C,
               "head_branch": "main", "path": ".github/workflows/windows-native.yml", "event": "workflow_run",
               "repository": {"id": 31}, "head_repository": {"id": 31}}
        api = mock.Mock()
        api.get.side_effect = [run, run, {"total_count": 1, "jobs": [{"name": "build", "status": "completed", "conclusion": "success", "steps": []}]}]
        with self.assertRaises(ValueError):
            publisher.check_run(api, NID, 1, C, "windows-native.yml", "workflow_run", 31)

    def test_disabled_or_non_request_commit_cannot_verify_for_publication(self):
        api = mock.Mock()
        api.get.return_value = {"id": 31, "full_name": publisher.REPO, "default_branch": "main"}
        with mock.patch.object(publisher, "check_run"), mock.patch.object(publisher, "request_parent", return_value=None):
            self.assertFalse(publisher.verify(api, {"workflow_run": {}}, R, self.work / "out"))
        json_file(self.repo / publisher.REQUEST, {"schema_version": 1, "enabled": False})
        with mock.patch.object(publisher, "check_run"), mock.patch.object(publisher, "request_parent", return_value=C):
            self.assertFalse(publisher.verify(api, {"workflow_run": {}}, R, self.work / "out"))
        api.download.assert_not_called()
        api.write.assert_not_called()

    def test_api_refuses_nonofficial_targets_and_delete(self):
        api = publisher.GitHub("inert-unit-test-token")
        with self.assertRaises(ValueError):
            api.request("GET", "https://example.invalid")
        with self.assertRaises(ValueError):
            api.write("DELETE", "/releases/10")

    def test_download_does_not_forward_token_to_storage(self):
        api = publisher.GitHub("inert-unit-test-token")
        response = io.BytesIO(b"inert archive")
        redirect = HTTPError("https://api.github.com/", 302, "redirect", {"Location": "https://storage.example.invalid/signed?x=1"}, None)
        with mock.patch.object(api.opener, "open", side_effect=[redirect, response]) as opened:
            api.download(10, self.work / "download.zip")
        self.assertEqual(opened.call_args_list[0].args[0].get_header("Authorization"), "Bearer inert-unit-test-token")
        self.assertIsNone(opened.call_args_list[1].args[0].get_header("Authorization"))
        self.assertEqual((self.work / "download.zip").read_bytes(), b"inert archive")

    def payload_fixture(self):
        root = self.work / "candidate"
        root.mkdir()
        retained = root / "evidence"
        retained.mkdir()
        binary_hash = hashed(b"inert candidate executable")
        with zipfile.ZipFile(root / publisher.PORTABLE, "x") as archive:
            for name in ("LICENSE", "NOTICE"):
                (self.repo / name).write_bytes(("inert " + name).encode())
                archive.writestr("PrivacyBrowser/" + name, (self.repo / name).read_bytes())
            archive.writestr("PrivacyBrowser/NSIS-COPYING.txt", b"inert complete notice")
            archive.writestr("PrivacyBrowser/privacybrowser.exe", b"inert candidate executable")
            archive.writestr("PrivacyBrowser/xul.dll", b"inert candidate DLL")
        hardening = publisher.windows_pe_hardening
        hardening_records = {}
        for name, data in (("privacybrowser.exe", b"inert candidate executable"), ("xul.dll", b"inert candidate DLL")):
            raw = retained / ("pe-hardening-" + name + ".json")
            error = retained / ("pe-hardening-" + name + ".stderr.log")
            command = retained / ("pe-hardening-" + name + "-command.json")
            json_file(raw, {"path": "D:/pb/candidate/PrivacyBrowser/" + name,
                            **{key: True for key in hardening.CHECKS}})
            error.write_bytes(b"")
            json_file(command, {"exit_code": 0, "timed_out": False,
                                "tool_sha256": hardening.TOOL_SHA256, "binary_sha256": hashed(data)})
            hardening_records[name] = {"sha256": hashed(data), **{
                role: {"path": path.name, "sha256": publisher.native.digest(path)}
                for role, path in (("stdout", raw), ("stderr", error), ("command", command))}}
        json_file(retained / "windows-pe-hardening.json", {"schema_version": 1, "passed": True,
            "candidate_executed": False, "tool_sha256": hardening.TOOL_SHA256,
            "tool_source_revision": hardening.TOOL_SOURCE_REVISION, "binaries": hardening_records})
        inert_runtime = self.work / "autoconfig-inert-runtime"
        inert_runtime.mkdir()
        (inert_runtime / "privacybrowser.exe").write_bytes(b"inert candidate executable")
        make_retained_fixture(retained, inert_runtime)
        (root / publisher.SETUP).write_bytes(b"inert setup")
        (root / (publisher.SETUP + ".build.log")).write_text("inert NSIS log")
        proof = {"schema_version": 1, "build_kind": "full-source-build", "source_compiled": True,
                 "official_release": False, "target": "windows", "native_binary": "privacybrowser.exe",
                 "files": {"privacybrowser.exe": binary_hash},
                 "commit": C, "workflow_run": f"https://github.com/{publisher.REPO}/actions/runs/{NID}",
                 "commands": publisher.EXPECTED_COMMANDS}
        json_file(retained / "build-evidence.json", proof)
        make_places_fixture(retained, inert_runtime)
        json_file(retained / "commands.json", {"commands": publisher.EXPECTED_COMMANDS})
        json_file(retained / "native-static-checks.json", {"static_candidate_checks_passed": True, "native_binary_sha256": binary_hash})
        json_file(root / (publisher.SETUP + ".json"), {"schema_version": 1, "build_kind": "full-source-build-candidate-installer",
            "artifact": publisher.SETUP, "sha256": self.request["installer_sha256"], "artifact_bytes": len(b"inert setup"),
            "native_binary_sha256": binary_hash, "source_build_evidence": {"manifest_sha256": publisher.native.digest(retained / "build-evidence.json")},
            "installer_compiled": True, "authenticode_signed": False, "official_release": False, "release_eligible": False})
        json_file(retained / "windows-installer-test.json", {"schema_version": 1, "passed": True,
            "installer_sha256": self.request["installer_sha256"], "native_binary_sha256": binary_hash, "release_eligible": False,
            "checks": {name: True for name in publisher.evidence.REQUIRED_INSTALLER_CHECKS}})
        json_file(retained / "toolchain.json", {"commit": C, "run_id": str(NID), "ephemeral_github_runner": True})
        commands, screenshots = [], []
        binary = "D:/pb/candidate/PrivacyBrowser/privacybrowser.exe"
        for label in ("windows-version", "windows-local-render", "windows-privacybrowser-home"):
            (retained / (label + ".log")).write_text("inert " + label + " log")
            argv = [binary, "--version"]
            if label != "windows-version":
                url = "file:///D:/pb/evidence/rendering-smoke.html" if label == "windows-local-render" else "chrome://privacybrowser/content/home.html"
                argv = [binary, "--headless", "--no-remote", "--profile", "D:/pb/" + label + "-profile",
                        "--screenshot", "D:/pb/evidence/" + label + ".png", "--window-size", "800,600", url]
                png = retained / (label + ".png")
                png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 800, 600) + b"inert header fixture")
                screenshots.append({"path": png.name, "sha256": publisher.native.digest(png)})
            commands.append({"argv": argv, "exit_code": 0, "log": {"path": label + ".log", "sha256": publisher.native.digest(retained / (label + ".log"))}})
        json_file(retained / "windows-runtime-smoke.json", {"schema_version": 1, "windows_smoke_checks_passed": True,
            "native_binary_sha256": binary_hash, "release_eligible": False, "interactive_windows_ui_tested": False,
            "autoconfig_runtime": {"path": publisher.windows_autoconfig_smoke.REPORT,
                                   "sha256": publisher.native.digest(retained / publisher.windows_autoconfig_smoke.REPORT)},
            "privacy_security_acceptance_tested": False, "commands": commands, "screenshots": screenshots})
        self.request["custom_home_review"]["screenshot_sha256"] = screenshots[1]["sha256"]
        json_file(self.repo / publisher.REQUEST, self.request)
        sums(root)
        return root, binary_hash

    def test_retained_payload_checks_all_agree_with_mocked_native_pe_validation(self):
        root, binary_hash = self.payload_fixture()
        with mock.patch.object(publisher.native, "validate_candidate", return_value={"native_binary_sha256": binary_hash}) as candidate, \
             mock.patch.object(publisher.native, "validate_installer") as installer:
            publisher.validate_payload(root, self.request)
        candidate.assert_called_once()
        installer.assert_called_once()

    def test_each_missing_installer_check_blocks_even_with_positive_summaries(self):
        root, binary_hash = self.payload_fixture()
        report_path = root / "evidence/windows-installer-test.json"
        base = json.loads(report_path.read_text())
        for key in publisher.evidence.REQUIRED_INSTALLER_CHECKS:
            changed = copy.deepcopy(base)
            del changed["checks"][key]
            json_file(report_path, changed)
            sums(root)
            with self.subTest(key=key), mock.patch.object(publisher.evidence, "safe_extract_zip"), \
                 mock.patch.object(publisher.native, "validate_candidate", return_value={"native_binary_sha256": binary_hash}), \
                 mock.patch.object(publisher.native, "validate_installer"):
                # Supply the inert unpacked runtime once; subsequent iterations only change the report.
                unpacked = root.parent / "runtime-unpacked"
                if not unpacked.exists():
                    with zipfile.ZipFile(root / publisher.PORTABLE) as archive:
                        archive.extractall(unpacked)
                with self.assertRaisesRegex(ValueError, key):
                    publisher.validate_payload(root, self.request)

    def test_false_automatic_startup_blocks_even_with_rehashed_positive_summaries(self):
        root, binary_hash = self.payload_fixture()
        probe = publisher.windows_autoconfig_smoke
        retained = root / "evidence"
        observation = json.loads((retained / probe.OBSERVATION).read_text(encoding="utf-8"))
        observation["startup"]["glue"]["started"] = False
        json_file(retained / probe.OBSERVATION, observation)
        report = json.loads((retained / probe.REPORT).read_text(encoding="utf-8"))
        report["records"]["observation"]["sha256"] = publisher.native.digest(retained / probe.OBSERVATION)
        json_file(retained / probe.REPORT, report)
        smoke = json.loads((retained / "windows-runtime-smoke.json").read_text(encoding="utf-8"))
        smoke["autoconfig_runtime"]["sha256"] = publisher.native.digest(retained / probe.REPORT)
        json_file(retained / "windows-runtime-smoke.json", smoke)
        sums(root)
        with mock.patch.object(publisher.native, "validate_candidate", return_value={"native_binary_sha256": binary_hash}), \
             mock.patch.object(publisher.native, "validate_installer"), \
             self.assertRaisesRegex(ValueError, "Automatic-startup"):
            publisher.validate_payload(root, self.request)

    def test_source_commit_and_xpcshell_log_tampering_fail_before_pe_inspection(self):
        root, _ = self.payload_fixture()
        proof_path = root / "evidence/build-evidence.json"
        proof = json.loads(proof_path.read_text())
        for mutation in ("commit", "xpcshell"):
            changed = copy.deepcopy(proof)
            if mutation == "commit":
                changed["commit"] = R
            else:
                (root / "evidence/xpcshell.log").write_bytes(b"INERT tampered failed test")
            json_file(proof_path, changed)
            sums(root)
            with self.subTest(mutation=mutation), mock.patch.object(publisher.native, "validate_candidate") as native_check:
                with self.assertRaises(ValueError):
                    publisher.validate_payload(root, self.request)
                native_check.assert_not_called()

    def test_review_screenshot_and_runtime_command_mismatch_block(self):
        root, binary_hash = self.payload_fixture()
        path = root / "evidence/windows-runtime-smoke.json"
        smoke = json.loads(path.read_text())
        self.request["custom_home_review"]["screenshot_sha256"] = "e" * 64
        with mock.patch.object(publisher.native, "validate_candidate", return_value={"native_binary_sha256": binary_hash}), \
             mock.patch.object(publisher.native, "validate_installer"), self.assertRaisesRegex(ValueError, "Visual review"):
            publisher.validate_payload(root, self.request)
        smoke["commands"][0]["argv"][0] = "D:/old/firefox.exe"
        json_file(path, smoke)
        sums(root)
        with mock.patch.object(publisher.evidence, "safe_extract_zip"), \
             mock.patch.object(publisher.native, "validate_candidate", return_value={"native_binary_sha256": binary_hash}), \
             mock.patch.object(publisher.native, "validate_installer"), self.assertRaisesRegex(ValueError, "source-built candidate"):
            publisher.validate_payload(root, self.request)

    def test_verify_stages_only_hash_verified_candidate_from_requested_attempt(self):
        root, _ = self.payload_fixture()
        archive = self.work / "source-artifact.zip"
        make_zip(root, archive)
        digest = publisher.native.digest(archive)
        self.request["artifact_sha256"] = digest
        json_file(self.repo / publisher.REQUEST, self.request)
        api = FakeAPI(archive, digest)
        api.asset_meta = {"id": 70, "expired": False, "name": "UNSIGNED-UNPUBLISHED-PrivacyBrowser-windows-" + C,
            "digest": "sha256:" + digest, "created_at": "2026-10-19T10:00:00Z", "workflow_run": {
            "id": NID, "head_sha": C, "head_branch": "main", "repository_id": 31, "head_repository_id": 31}}
        run = {"run_started_at": "2026-10-19T09:00:00Z", "updated_at": "2026-10-19T10:01:00Z"}
        with mock.patch.object(publisher, "check_run", return_value=run), \
             mock.patch.object(publisher, "request_parent", return_value=C), \
             mock.patch.object(publisher, "validate_payload") as validate:
            self.assertTrue(publisher.verify(api, {"workflow_run": {"id": TRIGGER, "run_attempt": 1}}, R, self.work / "out"))
        validate.assert_called_once()
        self.assertEqual(api.writes, [])
        self.assertEqual(set(publisher.native.inventory(self.work / "out")), set(publisher.ASSETS))

    @unittest.skipUnless(shutil.which("git"), "Git is needed for the checkout-byte regression")
    def test_windows_style_git_checkout_keeps_legal_text_lf_and_artwork_binary(self):
        attributes = (ROOT / ".gitattributes").read_bytes()
        self.assertIn(b"* text=auto eol=lf\n", attributes)
        expected = {name: (ROOT / name).read_bytes() for name in (
            "LICENSE", "NOTICE", "packaging/windows/privacy-browser-native.nsi", "tools/build_windows_native.ps1")}
        for extension in ("png", "ico", "bmp"):
            self.assertIn(("*." + extension + " binary\n").encode(), attributes)
            # Include both LF and CRLF in inert binary data to catch conversion.
            expected["artwork." + extension] = b"inert\x00binary\r\nLF\nCRLF\r\n"
        (self.repo / ".gitattributes").write_bytes(attributes)
        for name, contents in expected.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        def run(*args):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        run("init", "-q")
        run("-c", "core.autocrlf=true", "add", "--", ".")
        for name in expected:
            (self.repo / name).unlink()
        run("-c", "core.autocrlf=true", "checkout-index", "--all", "--force")
        for name, contents in expected.items():
            with self.subTest(name=name):
                self.assertEqual((self.repo / name).read_bytes(), contents)
        for name in ("LICENSE", "NOTICE", "packaging/windows/privacy-browser-native.nsi", "tools/build_windows_native.ps1"):
            self.assertNotIn(b"\r\n", (self.repo / name).read_bytes())

    def test_workflow_write_scope_and_checked_in_request_schema(self):
        workflow = (ROOT / ".github/workflows/windows-prerelease.yml").read_text()
        ordinary = (ROOT / ".github/workflows/quality.yml").read_text()
        native = (ROOT / ".github/workflows/windows-native.yml").read_text()
        self.assertEqual(workflow.count("contents: write"), 1)
        self.assertIn("needs: verify", workflow)
        self.assertIn("if: needs.verify.outputs.ready == 'true'", workflow)
        self.assertIn("workflow_run:", workflow)
        self.assertNotIn("workflow_dispatch", workflow)
        self.assertNotIn("contents: write", native + ordinary)
        self.assertIn("needs.changes.outputs.build_required == 'true'", native)
        self.assertNotIn("id-token:", workflow)
        self.assertEqual(workflow.count("persist-credentials: false"), 2)
        # CI must also pass for a later intentional request-only activation.
        # Initial disabled state is a reviewed file property, not a permanent
        # assertion that would make the authorized activation impossible.
        with mock.patch.object(publisher, "ROOT", ROOT):
            publisher.read_request()


if __name__ == "__main__":
    unittest.main()
