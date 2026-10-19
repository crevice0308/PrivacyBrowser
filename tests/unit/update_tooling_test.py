"""Meaningful offline guard tests; no claims about a compiled native browser."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import build_dev
import release_gate
import session_patch
import update_esr
import extract_source


class UpdateTrustTests(unittest.TestCase):
    def test_only_official_https_urls_are_accepted(self):
        for value in ("http://archive.mozilla.org/x", "https://archive.mozilla.org.evil.test/x", "https://user@archive.mozilla.org/x", "file:///tmp/key", "https://archive.mozilla.org:8443/x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                update_esr.trusted_url(value)
        self.assertEqual(update_esr.trusted_url("https://archive.mozilla.org/pub/firefox/"), "https://archive.mozilla.org/pub/firefox/")

    def test_signed_checksum_selection_cannot_escape_or_select_ambiguous_entry(self):
        digest = "a" * 128
        path = "source/firefox-153.5.0esr.source.tar.xz"
        self.assertEqual(update_esr.checksum_for(digest + "  " + path, path), digest)
        windows_path = "win64/en-US/Firefox Setup 153.5.0esr.exe"
        self.assertEqual(update_esr.checksum_for(digest + "  " + windows_path, windows_path), digest)
        for data, name in ((digest + "  ../archive", "../archive"), (digest + "  " + path + "\n" + digest + "  " + path, path), ("abc  " + path, path), (digest + "  other", path)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                update_esr.checksum_for(data, name)

    def test_version_input_cannot_inject_archive_paths(self):
        for version in ("153.5.0", "../153.5.0esr", "153.5.0esr/evil", "153.5.0esr;echo"):
            with self.assertRaises(ValueError):
                update_esr.archive_path(version, "source")

    def test_signature_requires_pinned_primary_and_current_signing_subkey(self):
        primary = "A" * 40
        current = "B" * 40
        revoked_old = "C" * 40
        with tempfile.TemporaryDirectory() as temporary:
            keyring = Path(temporary) / "trusted.gpg"
            keyring.write_bytes(b"fixture")
            sums, signature = Path(temporary) / "sums", Path(temporary) / "sums.asc"
            sums.write_text("fixture")
            signature.write_text("fixture")
            result = subprocess.CompletedProcess([], 0, f"[GNUPG:] VALIDSIG {current} 2026-10-19 1234 0 4 0 1 8 00 {primary}\n", "")
            with patch.object(update_esr.subprocess, "run", return_value=result):
                self.assertEqual(update_esr.verify_signature(sums, signature, keyring, [primary], [current]), primary)
                with self.assertRaises(ValueError):
                    update_esr.verify_signature(sums, signature, keyring, [primary], [revoked_old])
                with self.assertRaises(ValueError):
                    update_esr.verify_signature(sums, signature, keyring, [], [current])
            bad = subprocess.CompletedProcess([], 1, result.stdout, "revoked or invalid signature")
            with patch.object(update_esr.subprocess, "run", return_value=bad), self.assertRaises(ValueError):
                update_esr.verify_signature(sums, signature, keyring, [primary], [current])
            expired = subprocess.CompletedProcess([], 0, result.stdout + f"[GNUPG:] EXPKEYSIG {current} expired\n", "")
            with patch.object(update_esr.subprocess, "run", return_value=expired), self.assertRaises(ValueError):
                update_esr.verify_signature(sums, signature, keyring, [primary], [current])

    def test_developer_artifact_cannot_become_release_by_renaming(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "PrivacyBrowser-release.exe"
            artifact.write_bytes(b"developer runtime")
            candidate = {"source_compiled": False, "native_branding_replaced": False, "build_kind": "developer-runtime-overlay",
                         "artifacts": [{"path": artifact.name, "sha512": hashlib.sha512(artifact.read_bytes()).hexdigest()}]}
            errors = release_gate.eligibility(candidate, {"release_allowed": False}, {}, Path(temporary))
            self.assertTrue(any("source build" in item for item in errors))
            self.assertTrue(any("publication is blocked" in item for item in errors))

    def test_source_extraction_checks_hash_flattens_root_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, member, safe in (("safe", "firefox-153.5.0/mach", True), ("unsafe", "../outside", False)):
                archive = root / (name + ".tar")
                with tarfile.open(archive, "w") as tar:
                    item = tarfile.TarInfo(member)
                    item.size = 1
                    tar.addfile(item, io.BytesIO(b"x"))
                evidence = root / (name + ".json")
                evidence.write_text(json.dumps({"signature_verified": True, "kind": "source", "archive": archive.name,
                                                "sha512": hashlib.sha512(archive.read_bytes()).hexdigest()}))
                destination = root / ("result-" + name)
                if safe:
                    extract_source.extract(archive, evidence, destination)
                    self.assertEqual((destination / "mach").read_bytes(), b"x")
                else:
                    with self.assertRaises(ValueError):
                        extract_source.extract(archive, evidence, destination)
                self.assertFalse((root / "outside").exists())


class DeveloperBuildTests(unittest.TestCase):
    def test_windows_developer_launcher_keeps_arguments_out_of_cmd(self):
        # Cross-platform fixture only: no Windows executable is run by this test.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upstream, overlay, output = root / "upstream", root / "overlay", root / "dev"
            upstream.mkdir()
            (upstream / "firefox.exe").write_bytes(b"Windows fixture")
            (upstream / "application.ini").write_text("[App]\nName=Firefox\n")
            overlay.mkdir()
            (overlay / "bootstrap.cfg").write_text("// cfg\n")
            (overlay / "chrome.manifest").write_text("content privacybrowser content/\n")
            build_dev.build(upstream, overlay, output)
            self.assertFalse((output / "privacy-browser-dev.cmd").exists())
            script = (output / "privacy-browser-dev.ps1").read_text()
            self.assertIn("@args", script)
            self.assertNotIn("Invoke-Expression", script)

    def test_installs_chrome_without_mutating_upstream_and_disables_wrong_updater(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upstream, overlay, output = root / "upstream", root / "overlay", root / "dev"
            upstream.mkdir()
            (upstream / "firefox").write_bytes(b"binary fixture")
            (upstream / "application.ini").write_text("[App]\nName=Firefox\n")
            overlay.mkdir()
            (overlay / "bootstrap.cfg").write_text("// cfg\n")
            (overlay / "chrome.manifest").write_text("content privacybrowser content/\n")
            result = build_dev.build(upstream, overlay, output)
            self.assertFalse((upstream / "privacybrowser.cfg").exists())
            self.assertEqual((output / "privacybrowser.cfg").read_text(), "// cfg\n")
            self.assertFalse(result["source_compiled"])
            self.assertFalse(result["official_release"])
            policies = json.loads((output / "distribution/policies.json").read_text())
            self.assertTrue(policies["policies"]["DisableAppUpdate"])
            with self.assertRaises(ValueError):
                build_dev.build(upstream, overlay, upstream / "nested-output")
            if sys.platform != "win32":
                outside = root / "external-config"
                outside.mkdir()
                (upstream / "defaults").symlink_to(outside, target_is_directory=True)
                with self.assertRaises(ValueError):
                    build_dev.build(upstream, overlay, root / "symlink-output")
                self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipIf(sys.platform == "win32", "POSIX launcher argument isolation test")
    def test_url_text_cannot_select_the_upstream_browser_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upstream, overlay, output = root / "upstream", root / "overlay", root / "dev"
            upstream.mkdir()
            binary = upstream / "firefox"
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
            binary.chmod(0o755)
            (upstream / "application.ini").write_text("[App]\nName=Firefox\n")
            overlay.mkdir()
            (overlay / "bootstrap.cfg").write_text("// cfg\n")
            (overlay / "chrome.manifest").write_text("content privacybrowser content/\n")
            build_dev.build(upstream, overlay, output)
            environment = os.environ.copy()
            environment.update({"CAPTURE": str(root / "arguments"), "XDG_CONFIG_HOME": str(root / "config")})
            url = "https://example.invalid/ --profile unrelated-profile"
            subprocess.run([str(output / "privacy-browser-dev"), url], env=environment, check=True)
            arguments = (root / "arguments").read_text().splitlines()
            self.assertIn("--profile", arguments)
            self.assertIn(str(root / "config/privacybrowser-dev/profile"), arguments)
            self.assertEqual(arguments[-1], url)


class SessionSourceGuardTests(unittest.TestCase):
    def test_unknown_upstream_layout_is_rejected_without_changing_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "SessionFile.sys.mjs"
            source.write_text("changed upstream source")
            with self.assertRaises(ValueError):
                session_patch.patch_session_file(source)
            self.assertEqual(source.read_text(), "changed upstream source")

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for native-guard semantic fixture")
    def test_disabled_guard_skips_native_io_and_notifies_final_shutdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "guard.mjs"
            # Real upstream anchors with observable stand-ins for native disk I/O.
            source.write_text('''let enabled=false, writes=0, reads=0, notified=0, closed=false, removed=[];
const Services={prefs:{getBoolPref(){return enabled;}},obs:{notifyObservers(){notified++;}}};
const IOUtils={async remove(path){removed.push(path);}};
const profileDir="profile",PathUtils={join(...x){return x.join("/");}};
const lazy={RunState:{isClosed:false,isClosing:true,setClosed(){closed=true;}}};
const guard={Paths:{clean:"clean",backups:"backups"},
  async read() {
    // Load session files with lz4 compression.
    reads++; return {origin:"loaded"};
  },
  write(aData) {
    if (lazy.RunState.isClosed) { throw Error("closed"); }
    writes++;return Promise.resolve(aData);
  }
};
const empty=await guard.read();await guard.write("sensitive");
if(empty.origin!=="empty"||reads||writes||!closed||notified!==1||removed.length!==3)throw Error("OFF guard failed");
enabled=true;lazy.RunState.isClosing=false;await guard.read();await guard.write("persistent");
if(reads!==1||writes!==1)throw Error("ON guard failed");
console.log("session native source guard fixture passed");
''')
            session_patch.patch_session_file(source)
            result = subprocess.run(["node", str(source)], check=True, capture_output=True, text=True)
            self.assertIn("guard fixture passed", result.stdout)
            with self.assertRaises(ValueError):
                session_patch.patch_session_file(source)


if __name__ == "__main__":
    unittest.main()
