"""Mocked cross-platform GPG argument handling and unchanged trust refusals."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import provision_upstream_key as provision
import update_esr

PRIMARY = "A" * 40
SUBKEY = "B" * 40


def public_listing(primary=PRIMARY, subkey=SUBKEY, validity="u", expiration="0"):
    return (f"pub:{validity}:4096:1:key:1:{expiration}:::\n"
            f"fpr:::::::::{primary}:\n"
            f"sub:{validity}:4096:1:key:1:{expiration}:::\n"
            f"fpr:::::::::{subkey}:\n")


def valid_signature(primary=PRIMARY, subkey=SUBKEY):
    return f"[GNUPG:] VALIDSIG {subkey} 2026-10-19 1234 0 4 0 1 8 00 {primary}\n"


class GpgInteropTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.trust = self.root / "trust.json"
        self.trust.write_text(json.dumps({"mozilla_release_key_fingerprints": [PRIMARY],
                                         "mozilla_release_signing_subkey_fingerprints": [SUBKEY]}))

    def assert_isolated_command(self, command, kwargs):
        self.assertEqual(command[1:6], ["--no-options", "--batch", "--no-tty", "--homedir", "."])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 60)
        self.assertFalse(kwargs["check"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertTrue(Path(kwargs["cwd"]).is_dir())
        for value in command[1:]:
            self.assertNotIn("\\", value)
            self.assertNotIn(":", value)

    def fake_download(self, url, destination, maximum):
        self.assertEqual(url, provision.KEY_URL)
        self.assertEqual(maximum, 2 * 1024 * 1024)
        destination.write_bytes(b"public-key fixture only\r\n")

    def test_provision_uses_relative_paths_and_exports_only_after_both_pins(self):
        calls = []
        binary = b"\x00pinned binary public-key fixture\xff\r\n"

        def run(command, **kwargs):
            self.assert_isolated_command(command, kwargs)
            home = Path(kwargs["cwd"])
            self.assertEqual((home / "KEY").read_bytes(), b"public-key fixture only\r\n")
            calls.append(command)
            if "--show-keys" in command:
                self.assertEqual(command[-3:], ["--with-colons", "--show-keys", "KEY"])
                return subprocess.CompletedProcess(command, 0, public_listing(), "")
            self.assertEqual(command[-4:], ["--dearmor", "--output", "verified-public-key.gpg", "KEY"])
            (home / "verified-public-key.gpg").write_bytes(binary)
            return subprocess.CompletedProcess(command, 0, "", "")

        output = self.root / "verified.gpg"
        with mock.patch.object(provision, "download", side_effect=self.fake_download), \
             mock.patch.object(update_esr.subprocess, "run", side_effect=run):
            provision.provision(output, self.trust)
        self.assertEqual(len(calls), 2)
        self.assertEqual(output.read_bytes(), binary)

    def test_provision_mismatched_expired_and_revoked_keys_never_export(self):
        cases = [public_listing(primary="C" * 40), public_listing(subkey="C" * 40),
                 public_listing(validity="r"), public_listing(validity="e"), public_listing(validity="d"),
                 public_listing(expiration="1")]
        for listing in cases:
            with self.subTest(listing=listing):
                result = subprocess.CompletedProcess([], 0, listing, "")
                output = self.root / "refused.gpg"
                with mock.patch.object(provision, "download", side_effect=self.fake_download), \
                     mock.patch.object(update_esr.subprocess, "run", return_value=result) as run, \
                     self.assertRaisesRegex(ValueError, "reviewed identity pins"):
                    provision.provision(output, self.trust)
                self.assertEqual(run.call_count, 1)
                self.assertFalse(output.exists())

    def test_signature_inputs_are_copied_byte_identically_to_relative_names(self):
        directory = self.root / "input 日本語 with spaces"
        directory.mkdir()
        sources = {"checksums.txt": b"checksums\r\n\x00\xff", "checksums.sig": b"public signature\r\n",
                   "trusted.gpg": b"public keyring\x00\xfe"}
        for name, data in sources.items():
            (directory / name).write_bytes(data)

        def run(command, **kwargs):
            self.assert_isolated_command(command, kwargs)
            home = Path(kwargs["cwd"])
            self.assertNotEqual(home, directory)
            for name, data in sources.items():
                self.assertEqual((home / name).read_bytes(), data)
            self.assertEqual(command[6:], ["--no-default-keyring", "--keyring", "./trusted.gpg", "--status-fd", "1",
                                          "--verify", "checksums.sig", "checksums.txt"])
            return subprocess.CompletedProcess(command, 0, valid_signature(), "")

        with mock.patch.object(update_esr.subprocess, "run", side_effect=run):
            actual = update_esr.verify_signature(directory / "checksums.txt", directory / "checksums.sig",
                                                 directory / "trusted.gpg", [PRIMARY], [SUBKEY])
        self.assertEqual(actual, PRIMARY)
        self.assertEqual({name: (directory / name).read_bytes() for name in sources}, sources)

    def test_signature_refusals_remain_fail_closed(self):
        inputs = [self.root / name for name in ("sums", "sig", "key")]
        for path in inputs:
            path.write_bytes(b"public fixture")
        cases = [(1, valid_signature()), (0, valid_signature(primary="C" * 40)),
                 (0, valid_signature(subkey="C" * 40))]
        for status in ("BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "KEYREVOKED", "SIGEXPIRED", "NO_PUBKEY"):
            cases.append((0, valid_signature() + f"[GNUPG:] {status} refused\n"))
        for code, output in cases:
            with self.subTest(code=code, output=output), \
                 mock.patch.object(update_esr.subprocess, "run", return_value=subprocess.CompletedProcess([], code, output, "refused")), \
                 self.assertRaises(ValueError):
                update_esr.verify_signature(*inputs, [PRIMARY], [SUBKEY])

    def test_nonzero_gpg_error_has_bounded_escaped_diagnostics(self):
        stderr = "gpg: cannot open file\n::error::not a runner command\x1b" + "x" * 6000
        result = subprocess.CompletedProcess([], 2, "not logged", stderr)
        with mock.patch.object(update_esr.subprocess, "run", return_value=result), \
             self.assertRaises(ValueError) as caught:
            update_esr.run_gpg(["--show-keys", "KEY"], self.root, "inspect-official-public-key")
        message = str(caught.exception)
        self.assertNotIn("\n", message)
        detail = json.loads(message.split(": ", 1)[1])
        self.assertEqual(detail["exit_code"], 2)
        self.assertEqual(detail["stderr"], stderr[:4096])
        self.assertTrue(detail["stderr_truncated"])
        self.assertNotIn("not logged", message)

    def test_start_failure_and_timeout_are_bounded_and_fail_closed(self):
        for error in (OSError("unrelated environment text"), subprocess.TimeoutExpired("gpg", 60)):
            with self.subTest(error=type(error).__name__), \
                 mock.patch.object(update_esr.subprocess, "run", side_effect=error), \
                 self.assertRaises(ValueError) as caught:
                update_esr.run_gpg(["--show-keys", "KEY"], self.root, "inspect-official-public-key")
            self.assertNotIn("unrelated environment text", str(caught.exception))
            detail = json.loads(str(caught.exception).split(": ", 1)[1])
            self.assertEqual(detail["timed_out"], isinstance(error, subprocess.TimeoutExpired))


if __name__ == "__main__":
    unittest.main()
