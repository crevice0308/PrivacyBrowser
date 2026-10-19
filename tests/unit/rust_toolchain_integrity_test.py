"""Inert metadata fixtures only: no downloads, compiler or upstream execution."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import verify_rust_toolchain as rust

TASK = "a" * 22
URL = rust.QUEUE + TASK + "/artifacts/public%2Fbuild%2Frustc.tar.zst"
SHA = "b" * 64


class RustIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / "rust-toolchain-artifacts.json"
        self.manifest.write_text(json.dumps({URL: {"sha256": SHA}}))

    def test_exact_matching_metadata_is_retained_before_any_execution(self):
        raw = json.dumps({"artifacts": {rust.ARTIFACT: {"sha256": SHA}}}).encode()
        with mock.patch.object(rust, "fetch_metadata", return_value=raw) as fetch:
            rust.verify(self.root)
            fetch.assert_called_once_with(TASK)
        self.assertEqual((self.root / "rust-toolchain-cot.json").read_bytes(), raw)
        report = json.loads((self.root / "rust-toolchain-integrity.json").read_text())
        self.assertTrue(report["verified"])
        self.assertEqual(report["archive_sha256"], SHA)
        self.assertFalse(report["executables_run_by_this_check"])
        self.assertFalse(report["detached_cot_signature_independently_verified"])

    def test_missing_mismatched_or_wrong_algorithm_cot_refused(self):
        for payload in ({}, {"artifacts": {}}, {"artifacts": {rust.ARTIFACT: {}}},
                        {"artifacts": {rust.ARTIFACT: {"sha512": SHA}}},
                        {"artifacts": {rust.ARTIFACT: {"sha256": "c" * 64}}}):
            with self.subTest(payload=payload), \
                 mock.patch.object(rust, "fetch_metadata", return_value=json.dumps(payload).encode()):
                with self.assertRaises(ValueError):
                    rust.verify(self.root)
                self.assertFalse((self.root / "rust-toolchain-integrity.json").exists())

    def test_wrong_artifact_url_is_rejected_before_network(self):
        for url in (URL.replace("https:", "http:"), URL.replace("firefox-ci-tc.services.mozilla.com", "example.com"),
                    URL.replace("rustc.tar.zst", "other.tar.zst"), URL + "?override=1"):
            self.manifest.write_text(json.dumps({url: {"sha256": SHA}}))
            with self.subTest(url=url), mock.patch.object(rust, "fetch_metadata") as fetch:
                with self.assertRaises(ValueError):
                    rust.verify(self.root)
                fetch.assert_not_called()

    def test_redirects_require_exact_official_same_task_metadata_path(self):
        valid = f"https://firefoxci.taskcluster-artifacts.net/{TASK}/0/public/chain-of-trust.json"
        rust.trusted_metadata_url(valid, TASK)
        rust.trusted_metadata_url(rust.cot_url(TASK), TASK)
        for url in (valid.replace("https:", "http:"), valid.replace(TASK, "c" * 22),
                    valid.replace("firefoxci.taskcluster-artifacts.net", "example.com"),
                    valid + "?token=x", valid.replace("chain-of-trust.json", "other.json")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                rust.trusted_metadata_url(url, TASK)

    def test_duplicate_manifest_hash_and_existing_outputs_refused(self):
        self.manifest.write_text('{"' + URL + '":{"sha256":"' + SHA + '","sha256":"' + SHA + '"}}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            rust.verify(self.root)
        self.manifest.write_text(json.dumps({URL: {"sha256": SHA}}))
        (self.root / "rust-toolchain-integrity.json").write_text("existing evidence")
        with mock.patch.object(rust, "fetch_metadata") as fetch:
            with self.assertRaisesRegex(ValueError, "already exists"):
                rust.verify(self.root)
            fetch.assert_not_called()

    def test_fetch_is_bounded_and_checks_response_url(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = rust.cot_url(TASK)
        response.read.return_value = b"{}"
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(rust.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(rust.fetch_metadata(TASK), b"{}")
            redirect = build.call_args.args[0]
            request = rust.urllib.request.Request(rust.cot_url(TASK))
            with self.assertRaisesRegex(ValueError, "redirect"):
                redirect.redirect_request(request, None, 302, "Found", {}, "https://example.com/cot")
            opener.open.assert_called_with(rust.cot_url(TASK), timeout=30)
            response.read.assert_called_with(rust.MAX_METADATA + 1)
            response.read.return_value = b"x" * (rust.MAX_METADATA + 1)
            with self.assertRaisesRegex(ValueError, "size"):
                rust.fetch_metadata(TASK)
            response.geturl.return_value = "https://example.com/metadata"
            with self.assertRaisesRegex(ValueError, "redirect"):
                rust.fetch_metadata(TASK)


if __name__ == "__main__":
    unittest.main()
