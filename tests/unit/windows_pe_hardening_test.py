"""Static inspector contract tests; no Windows executable is launched."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import windows_pe_hardening as hardening


class PEHardeningTests(unittest.TestCase):
    def test_reviewed_source_and_binary_pins_are_explicit(self):
        # setUp replaces the executable digest with inert fixture bytes; inspect
        # the checked-in constant without running or downloading the real tool.
        source = (ROOT / "tools/windows_pe_hardening.py").read_text(encoding="utf-8")
        self.assertIn('TOOL_SHA256 = "e63d6ccac4402d17494c60826a52051b50c7a2861b60742e3ed090a41f8a9277"', source)
        self.assertEqual(hardening.TOOL_SOURCE_REVISION, "93ffe67dbfc757bf6f440d80b8acf88e652ed60a")
        evidence = json.loads((ROOT / "tests/results/winchecksec-2026-10-28-review.json").read_text())
        self.assertIn('TOOL_SHA256 = "' + evidence["binary_sha256"] + '"', source)
        self.assertEqual(evidence["source_revision"], hardening.TOOL_SOURCE_REVISION)
        self.assertTrue(evidence["cot_matches_archive"])
        self.assertTrue(evidence["cot_matches_task_definition"])
        self.assertFalse(evidence["binary_executed"])
        self.assertFalse(evidence["release_eligible"])
        self.assertEqual(set(evidence["hardening_checks_unchanged"]), set(hardening.CHECKS))

        self.assertEqual(set(hardening.CHECKS), {"aslr", "cfg", "dynamicBase", "gs", "isolation", "nx", "seh", "highEntropyVA"})

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.evidence = self.work / "evidence"
        self.evidence.mkdir()
        self.runtime = self.work / "candidate/PrivacyBrowser"
        self.runtime.mkdir(parents=True)
        for name in hardening.BINARIES:
            (self.runtime / name).write_bytes(("inert " + name).encode())
        self.tool = self.work / "mozbuild/winchecksec/winchecksec.exe"
        self.tool.parent.mkdir(parents=True)
        self.tool.write_bytes(b"inert mocked inspection tool")
        self.pin = mock.patch.object(hardening, "TOOL_SHA256", hardening.native.digest(self.tool))
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def output(self, name):
        return {"path": "D:/pb/candidate/PrivacyBrowser/" + name,
                **{key: True for key in hardening.CHECKS},
                "authenticode": False, "safeSEH": False, "rfg": False}

    def fake_run(self, argv, **options):
        name = Path(argv[2]).name
        options["stdout"].write(json.dumps(self.output(name)).encode())
        return SimpleNamespace(returncode=0)

    def collect(self):
        with mock.patch.object(hardening.subprocess, "run", side_effect=self.fake_run) as run:
            hardening.collect(self.work)
        return run

    def test_pinned_tool_inspects_exact_two_candidates_without_executing_them(self):
        run = self.collect()
        self.assertEqual(run.call_count, 2)
        for call, name in zip(run.call_args_list, hardening.BINARIES):
            self.assertEqual(call.args[0], [str(self.tool), "-j", str(self.runtime / name)])
            self.assertEqual(call.kwargs["timeout"], 60)
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
            self.assertNotIn("shell", call.kwargs)
        report = hardening.validate_retained(self.evidence, self.runtime)
        self.assertFalse(report["candidate_executed"])
        self.assertTrue(report["passed"])

    def test_cli_rejects_unreviewed_arguments_before_inspection(self):
        for args in (["--verify-tool", "other"], ["--path", "other"], ["--skip-integrity"]):
            with self.subTest(args=args), mock.patch.object(sys, "argv", ["hardening", *args]), \
                    mock.patch.object(hardening, "collect") as collect, \
                    mock.patch.object(hardening, "verified_tool") as verify:
                with self.assertRaises(ValueError):
                    hardening.main()
                collect.assert_not_called()
                verify.assert_not_called()

    def test_hash_only_preflight_never_executes_tool_or_candidate(self):
        with mock.patch.object(hardening.subprocess, "run") as run:
            self.assertEqual(hardening.verified_tool(self.work), self.tool)
            self.tool.write_bytes(b"changed inert tool")
            with self.assertRaises(ValueError):
                hardening.verified_tool(self.work)
            run.assert_not_called()
        self.assertEqual(list(self.evidence.iterdir()), [])
        shell = (ROOT / "tools/build_windows_native.sh").read_text()
        self.assertIn('run_logged winchecksec-integrity python3 "$script_dir/windows_pe_hardening.py" --verify-tool', shell)
        self.assertLess(shell.index("run_logged winchecksec-integrity"), shell.index("run_logged build ./mach"))

    def test_changed_tool_is_rejected_before_execution(self):
        self.tool.write_bytes(b"different inert tool")
        with mock.patch.object(hardening.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                hardening.collect(self.work)
            run.assert_not_called()

    def test_required_checks_reject_missing_false_null_and_coercible_values(self):
        for key in hardening.CHECKS:
            for value in (False, None, 1, "true"):
                data = self.output("privacybrowser.exe")
                data[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    hardening.validate_output(data, "privacybrowser.exe")
            data.pop(key)
            with self.assertRaises(ValueError):
                hardening.validate_output(data, "privacybrowser.exe")

    def test_unsigned_and_x64_inapplicable_flags_are_not_required(self):
        hardening.validate_output(self.output("xul.dll"), "xul.dll")
        self.assertNotIn("safeSEH", hardening.CHECKS)
        self.assertNotIn("authenticode", hardening.CHECKS)

    def test_unexpected_binary_or_modern_schema_is_rejected(self):
        data = self.output("privacybrowser.exe")
        data["path"] = "D:/pb/candidate/PrivacyBrowser/xul.dll"
        for value in (data, [self.output("privacybrowser.exe")], {"mitigations": self.output("privacybrowser.exe")}):
            with self.assertRaises(ValueError):
                hardening.validate_output(value, "privacybrowser.exe")

    def test_nonzero_and_timeout_never_create_success_report(self):
        for mode in ("nonzero", "timeout"):
            with self.subTest(mode=mode):
                def fail(argv, **options):
                    if mode == "timeout":
                        raise subprocess.TimeoutExpired(argv, 60)
                    return SimpleNamespace(returncode=3)
                with mock.patch.object(hardening.subprocess, "run", side_effect=fail), self.assertRaises(ValueError):
                    hardening.collect(self.work)
                self.assertFalse((self.evidence / "windows-pe-hardening.json").exists())
                command = json.loads((self.evidence / "pe-hardening-privacybrowser.exe-command.json").read_text(encoding="utf-8"))
                self.assertEqual(command["timed_out"], mode == "timeout")
                for path in self.evidence.iterdir():
                    path.unlink()

    def test_invalid_duplicate_and_oversized_output_never_passes(self):
        for raw in (b"not json", b"\xff", b'{"nx":true,"nx":false}', b"x" * (hardening.MAX_OUTPUT + 1)):
            def invalid(argv, **options):
                options["stdout"].write(raw)
                return SimpleNamespace(returncode=0)
            with self.subTest(raw=raw[:20]), mock.patch.object(hardening.subprocess, "run", side_effect=invalid):
                with self.assertRaises((ValueError, UnicodeError)):
                    hardening.collect(self.work)
                self.assertFalse((self.evidence / "windows-pe-hardening.json").exists())
                for path in self.evidence.iterdir():
                    path.unlink()

    def test_tampered_payload_or_retained_raw_output_is_rejected(self):
        self.collect()
        path = self.runtime / "xul.dll"
        original = path.read_bytes()
        path.write_bytes(b"changed payload")
        with self.assertRaises(ValueError):
            hardening.validate_retained(self.evidence, self.runtime)
        path.write_bytes(original)
        raw = self.evidence / "pe-hardening-xul.dll.json"
        raw.write_text(json.dumps(dict(self.output("xul.dll"), cfg=False)), encoding="utf-8")
        with self.assertRaises(ValueError):
            hardening.validate_retained(self.evidence, self.runtime)
        report_path = self.evidence / "windows-pe-hardening.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["binaries"]["xul.dll"]["stdout"]["sha256"] = hardening.native.digest(raw)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaises(ValueError):
            hardening.validate_retained(self.evidence, self.runtime)

    def test_missing_report_blocks_staging_and_publication(self):
        with self.assertRaises(OSError):
            hardening.validate_retained(self.evidence, self.runtime)
        for filename in ("windows_native_evidence.py", "windows_prerelease.py"):
            self.assertIn("windows_pe_hardening.validate_retained(", (ROOT / "tools" / filename).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
