"""Structural/model/mocked-process and real preamble tests, not Windows COM results."""
from copy import deepcopy
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import test_native_installer_synthetic as synthetic


def model_report():
    """Authored observation model only; must never be presented as COM evidence."""
    report = {"schema_version": 1, "passed": True, "fixture_root": synthetic.SHORTCUT_ROOT,
              "case_count": 3, "cases": [], "get_path_flags": 0, "persist_load_mode": 0,
              "error_type": None, "error_hresult": None}
    for name in ("browser_executed", "shortcut_executed", "resolve_called", "save_called",
                 "production_installer_tested", "release_eligible"):
        report[name] = False
    for identity, folder, profile in synthetic.SHORTCUT_CASES:
        directory = ntpath.join(synthetic.SHORTCUT_ROOT, folder)
        expected = {"target": ntpath.join(directory, "privacybrowser.exe"),
                    "arguments": '-no-remote -profile "' + ntpath.join(synthetic.SHORTCUT_ROOT, "profiles", profile) + '"',
                    "icon": ntpath.join(directory, "reference.ico"), "icon_index": 0,
                    "working_directory": directory}
        case = {"id": identity, "expected": expected, "passed": True, "files_unchanged": True,
                "copies_identical": True, "error_type": None, "error_hresult": None, "links": []}
        for kind, relative, wsh_required in synthetic.SHORTCUT_LINKS:
            wide = {**expected, "load_mode": 0, "path_flags": 0, "strings_within_bound": True,
                    "error_type": None, "error_hresult": None}
            for name in ("load_hresult", "target_hresult", "arguments_hresult", "icon_hresult", "working_directory_hresult"):
                wide[name] = 0
            wsh = {**expected, "strings_within_bound": True, "read_succeeded": True,
                   "error_type": None, "error_hresult": None, "icon_location": expected["icon"] + ",0"}
            case["links"].append({"kind": kind, "path": ntpath.join(directory, relative), "bytes": 256,
                                  "sha256_before": "a" * 64, "sha256_after": "a" * 64,
                                  "identity_before": "11111111:00000000:00000001", "identity_after": "11111111:00000000:00000001",
                                  "wide": wide, "wsh": wsh if wsh_required else None,
                                  "wsh_required": wsh_required, "passed": True})
        report["cases"].append(case)
    return report


class ShortcutReadbackModelTests(unittest.TestCase):
    def test_complete_model_is_accepted_but_not_a_windows_execution(self):
        synthetic.validate_shortcut_readback(model_report())

    def test_target_case_matches_existing_real_gate_but_aliases_do_not(self):
        for reader in ("wide", "wsh"):
            report = model_report()
            report["cases"][1]["links"][0][reader]["target"] = report["cases"][1]["expected"]["target"].upper()
            synthetic.validate_shortcut_readback(report)
            for value in ("privacybrowser.exe", "", r"D:\pb\wrong\privacybrowser.exe", r"D:\pb\PRIVAC~1\privacybrowser.exe"):
                with self.subTest(reader=reader, value=value), self.assertRaises(ValueError):
                    report["cases"][1]["links"][0][reader]["target"] = value
                    synthetic.validate_shortcut_readback(report)

    def test_neither_wsh_nor_wide_can_be_ignored_or_fallback_to_the_other(self):
        for reader in ("wide", "wsh"):
            for field, value in (("arguments", "wrong"), ("icon", "wrong"), ("icon_index", 1),
                                 ("working_directory", "wrong"), ("error_type", "COMException"),
                                 ("error_hresult", -1), ("strings_within_bound", False)):
                report = model_report()
                report["cases"][2]["links"][3][reader][field] = value
                with self.subTest(reader=reader, field=field), self.assertRaises(ValueError):
                    synthetic.validate_shortcut_readback(report)

    def test_hresult_flags_and_boolean_status_cannot_forge_success(self):
        for name in ("load_hresult", "target_hresult", "arguments_hresult", "icon_hresult", "working_directory_hresult"):
            for value in (1, -1, None, False):
                report = model_report()
                report["cases"][0]["links"][0]["wide"][name] = value
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    synthetic.validate_shortcut_readback(report)
        for name in ("load_mode", "path_flags"):
            report = model_report()
            report["cases"][0]["links"][1]["wide"][name] = 4
            with self.assertRaises(ValueError):
                synthetic.validate_shortcut_readback(report)

    def test_custom_extension_sources_are_read_wide_and_not_via_wsh(self):
        for mutation in (None, {"target": "not a reader"}):
            report = model_report()
            report["cases"][0]["links"][1]["wide"] = mutation
            with self.assertRaises(ValueError):
                synthetic.validate_shortcut_readback(report)
        report = model_report()
        report["cases"][0]["links"][1]["wsh"] = deepcopy(report["cases"][0]["links"][0]["wsh"])
        with self.assertRaises(ValueError):
            synthetic.validate_shortcut_readback(report)

    def test_hash_mutation_and_copy_mismatch_fail_even_when_report_claims_pass(self):
        for field, value in (("sha256_after", "b" * 64), ("sha256_before", "not a hash"), ("bytes", 65537),
                             ("identity_after", "11111111:00000000:00000002")):
            report = model_report()
            report["cases"][0]["links"][3][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                synthetic.validate_shortcut_readback(report)
        for field, value in (("sha256_before", "b" * 64), ("bytes", 255)):
            report = model_report()
            report["cases"][0]["links"][3][field] = value
            if field == "sha256_before":
                report["cases"][0]["links"][3]["sha256_after"] = value
            with self.assertRaises(ValueError):
                synthetic.validate_shortcut_readback(report)

    def test_fixed_counts_identities_expected_values_and_modes_cannot_change(self):
        mutations = (
            lambda r: r.update(case_count=2), lambda r: r.update(fixture_root=r"C:\elsewhere"),
            lambda r: r.update(resolve_called=True), lambda r: r.update(save_called=True),
            lambda r: r.update(passed=False), lambda r: r.update(cases=r["cases"][:2]),
            lambda r: r["cases"][1].update(expected=r["cases"][0]["expected"]),
            lambda r: r["cases"][1]["links"][1].update(path=r"C:\elsewhere.lnk"),
            lambda r: r["cases"][1].update(links=r["cases"][1]["links"][:4]),
            lambda r: r["cases"][0]["links"][0]["wsh"].update(read_succeeded=False),
        )
        for mutate in mutations:
            report = model_report()
            mutate(report)
            with self.assertRaises(ValueError):
                synthetic.validate_shortcut_readback(report)


class ShortcutReadbackRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.root = self.work / synthetic.EXECUTION_RELATIVE
        self.root.mkdir(parents=True)
        (self.work / "evidence").mkdir()
        self.output = self.work / "evidence" / synthetic.SHORTCUT_OUTPUT
        self.engine = self.work / "pwsh.exe"
        self.engine.write_bytes(b"inert mocked PowerShell engine; never execute")
        self.commands = []
        original = synthetic.native.regular_file
        def regular(root, relative):
            if root == Path(r"C:\Program Files\PowerShell\7") and relative == "pwsh.exe":
                return self.engine
            return original(root, relative)
        self.enterContext(mock.patch.object(synthetic, "require_host"))
        self.enterContext(mock.patch.object(synthetic.native, "regular_file", side_effect=regular))
        self.run = self.enterContext(mock.patch.object(synthetic.subprocess, "run"))

    def invoke(self):
        with (self.work / "log").open("ab") as log:
            return synthetic.run_shortcut_readback(self.work, self.root, {}, log, self.commands)

    def test_fixed_noninteractive_bounded_command_and_success_evidence(self):
        def process(command, **options):
            self.assertEqual(command, [str(self.engine), "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                                       str(ROOT / "tools/test_windows_shortcut_readback.ps1")])
            self.assertEqual(options["timeout"], 60)
            self.assertEqual(options["cwd"], self.root)
            self.assertNotIn("shell", options)
            self.output.write_text(json.dumps(model_report()), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)
        self.run.side_effect = process
        result = self.invoke()
        self.assertEqual(result["path"], synthetic.SHORTCUT_OUTPUT)
        self.assertEqual(result["sha256"], synthetic.native.digest(self.output))
        self.assertEqual(self.commands[0]["exit_code"], 0)
        self.assertEqual(self.commands[0]["evidence"], result)

    def test_failed_process_retains_report_and_hash_without_claiming_success(self):
        retained = b'{"passed":false,"actual":"unexpected Unicode target"}'
        def process(command, **options):
            self.output.write_bytes(retained)
            return subprocess.CompletedProcess(command, 1)
        self.run.side_effect = process
        with self.assertRaisesRegex(ValueError, "readback failed"):
            self.invoke()
        self.assertEqual(self.output.read_bytes(), retained)
        self.assertEqual(self.commands[0]["exit_code"], 1)
        self.assertEqual(self.commands[0]["evidence"]["sha256"], synthetic.native.digest(self.output))

    def test_timeout_is_bounded_and_retains_available_diagnostics(self):
        def process(command, **options):
            self.output.write_text('{"passed":false}')
            raise subprocess.TimeoutExpired(command, options["timeout"])
        self.run.side_effect = process
        with self.assertRaises(subprocess.TimeoutExpired):
            self.invoke()
        self.assertTrue(self.commands[0]["timed_out"])
        self.assertIsNone(self.commands[0]["exit_code"])
        self.assertIsNotNone(self.commands[0]["evidence"])

    def test_existing_evidence_and_wrong_root_refuse_before_process(self):
        self.output.write_bytes(b"retain")
        with self.assertRaises(ValueError):
            self.invoke()
        self.run.assert_not_called()
        self.assertEqual(self.output.read_bytes(), b"retain")
        with self.assertRaises(ValueError):
            synthetic.run_shortcut_readback(self.work, self.work, {}, mock.Mock(), [])
        self.run.assert_not_called()

    def test_zero_exit_with_missing_corrupt_oversized_or_false_report_is_not_pass(self):
        for payload in (None, b"not JSON", b" " * (synthetic.SHORTCUT_MAX_BYTES + 1), b'{"passed":true}'):
            def process(command, **options):
                if payload is not None:
                    self.output.write_bytes(payload)
                return subprocess.CompletedProcess(command, 0)
            self.run.side_effect = process
            with self.subTest(payload_size=None if payload is None else len(payload)), self.assertRaises(ValueError):
                self.invoke()
            if self.output.exists():
                self.output.unlink()


@unittest.skipUnless(shutil.which("pwsh"), "Real advanced-script startup/binding needs installed pwsh")
class ShortcutReadbackStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.preamble = Path(self.temp.name) / "shortcut-readback-preamble.ps1"
        reader = (ROOT / "tools/test_windows_shortcut_readback.ps1").read_text(encoding="utf-8")
        prefix, boundary, _ = reader.partition("Add-Type -TypeDefinition @'")
        self.assertTrue(boundary)
        # Execute the exact source preamble as a script, not a function/model.
        # Keep its real host guard; omit all COM/native/file operations below it.
        self.marker = "SHORTCUT_READBACK_PREAMBLE_COMPLETE"
        self.preamble.write_text(prefix + "\nWrite-Output '" + self.marker + "'\n", encoding="utf-8")

    @unittest.skipUnless(sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
                         and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
                         "Zero-input preamble requires its genuine hosted Windows guard")
    def test_zero_arguments_succeed_and_common_parameters_fail_real_preamble(self):
        command = [shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive",
                   "-File", str(self.preamble)]
        result = subprocess.run(command, cwd=self.temp.name,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), self.marker)
        for arguments in (["-Verbose"], ["-Debug:$false"], ["-ErrorAction", "Continue"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(command + arguments, cwd=self.temp.name,
                                        capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Synthetic shortcut invariant failed.", result.stderr)
                self.assertNotIn(self.marker, result.stdout)

    def test_unexpected_arguments_fail_real_binding_before_script_body(self):
        path = str(self.preamble).replace("'", "''")
        for arguments, error_id in (("'unexpected'", "PositionalParameterNotFound"),
                                    ("-Unexpected", "NamedParameterNotFound"),
                                    ("-Root 'D:\\unexpected'", "NamedParameterNotFound"),
                                    ("-Output 'D:\\unexpected.json'", "NamedParameterNotFound")):
            with self.subTest(arguments=arguments):
                # Catch only to expose the stable binding error ID, independent
                # of localized error text. This still invokes the real .ps1.
                script = ("try { & '" + path + "' " + arguments + "; exit 99 } catch { "
                          "[Console]::Error.WriteLine($_.FullyQualifiedErrorId); exit 1 }")
                result = subprocess.run([shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive",
                                         "-Command", script], cwd=self.temp.name,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(result.stderr.strip().split(",", 1)[0], error_id)
                self.assertNotIn(self.marker, result.stdout)


class ShortcutReadbackStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = (ROOT / "tools/test_windows_shortcut_readback.ps1").read_text(encoding="utf-8")
        cls.fixture = (ROOT / "packaging/windows/native-safety-fixture.nsh").read_text(encoding="utf-8")
        cls.commands = "\n".join(line for line in cls.reader.splitlines() if not line.lstrip().startswith(("#", "//")))

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell parser/C# declaration/comparison mock needs installed pwsh")
    def test_actual_parser_csharp_declarations_and_mocked_comparison(self):
        # Parse the complete production reader, but do NOT execute it. Compile
        # declarations only, then test pure comparisons with authored fields.
        # No host guard is bypassed and no Windows/COM/filesystem method runs.
        path = str(ROOT / "tools/test_windows_shortcut_readback.ps1").replace("'", "''")
        declarations = self.reader.split("Add-Type -TypeDefinition @'", 1)[1].split("\n'@", 1)[0]
        helpers = self.reader[self.reader.index("function Bounded-String"):self.reader.index("$report = [ordered]@{")]
        script = "$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n" + r"""
$tokens = $null
$parseErrors = $null
$null = [Management.Automation.Language.Parser]::ParseFile('""" + path + r"""', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw ($parseErrors | Out-String) }
""" + "Add-Type -TypeDefinition @'" + declarations + "\n'@\n" + helpers + r"""
$expected = @{ target = 'D:\fixed\日本語 Space\privacybrowser.exe'; arguments = '-profile "D:\fixed\profile"';
               icon = 'D:\fixed\reference.ico'; icon_index = 0; working_directory = 'D:\fixed\日本語 Space' }
$actual = [PrivacyBrowserSynthetic.WideResult]::new()
foreach ($name in $expected.Keys) { $actual.$name = $expected[$name] }
$actual.load_hresult = 0
if (-not (Matches-Expected $actual $expected)) { throw 'Matching model failed.' }
$actual.target = $actual.target.ToUpperInvariant()
if (-not (Matches-Expected $actual $expected)) { throw 'Case-insensitive target model failed.' }
$actual.arguments = 'wrong'
if (Matches-Expected $actual $expected) { throw 'Wrong arguments model passed.' }
$actual.arguments = $expected.arguments
$actual.error_type = 'SyntheticFailure'
if (Matches-Expected $actual $expected) { throw 'Error model passed.' }
$encoded = $actual | ConvertTo-Json -Compress
$decoded = $encoded | ConvertFrom-Json
if ($decoded.load_hresult -ne 0 -or $decoded.target -ine $expected.target) { throw 'Public-field serialization failed.' }
"""
        result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fixed_hosted_only_paths_no_inputs_or_launch_persistence(self):
        for expected in ("[CmdletBinding()]", "param()", "Set-StrictMode -Version Latest",
                         "$PSBoundParameters.Count -eq 0",
                         "$IsWindows", "$env:GITHUB_ACTIONS -ceq 'true'",
                         "$env:RUNNER_ENVIRONMENT -ceq 'github-hosted'", synthetic.SHORTCUT_ROOT,
                         r"D:\pb\evidence\installer-synthetic-shortcuts.json", "[IO.FileMode]::CreateNew",
                         "[IO.FileShare]::None", "$bytes.Length -le $MaxReportBytes"):
            self.assertIn(expected, self.commands)
        self.assertNotIn("$args", self.commands)
        for forbidden in ("Start-Process", "Invoke-Expression", "ShellExecute", "Resolve(", "Save(",
                          "$env:APPDATA", "$env:LOCALAPPDATA", "HKCU:", "HKLM:", "Remove-Item"):
            self.assertNotIn(forbidden, self.commands)
        self.assertNotIn(".Exception.Message", self.reader)
        self.assertIn("$case.error_hresult = $_.Exception.HResult", self.reader)
        self.assertIn("$case.error_type = $_.Exception.GetType().FullName", self.reader)
        self.assertLess(self.reader.index("$outputStream.Write"), self.reader.index("exit $exitCode"))

    def test_explicit_wide_vtables_and_read_only_calls_are_not_ansi_or_resolving(self):
        self.assertIn('Guid("000214F9-0000-0000-C000-000000000046")', self.reader)
        self.assertIn('Guid("0000010B-0000-0000-C000-000000000046")', self.reader)
        self.assertIn('new Guid("00021401-0000-0000-C000-000000000046")', self.reader)
        interface = self.reader.split("interface IShellLinkW {", 1)[1].split("\n    }", 1)[0]
        self.assertEqual(re.findall(r"\[PreserveSig\] int (\w+)\(", interface),
                         ["GetPath", "GetIDList", "SetIDList", "GetDescription", "SetDescription", "GetWorkingDirectory",
                          "SetWorkingDirectory", "GetArguments", "SetArguments", "GetHotkey", "SetHotkey",
                          "GetShowCmd", "SetShowCmd", "GetIconLocation"])
        persist = self.reader.split("interface IPersistFileRead {", 1)[1].split("\n    }", 1)[0]
        self.assertEqual(re.findall(r"\[PreserveSig\] int (\w+)\(", persist), ["GetClassID", "IsDirty", "Load"])
        self.assertIn("persist.Load(path, 0)", self.reader)
        self.assertIn("link.GetPath(text, text.Capacity, IntPtr.Zero, 0)", self.reader)
        self.assertIn("$shell.CreateShortcut($Path)", self.reader)
        self.assertIn("$link.passed = $wideOk -and $wshOk", self.reader)

    def test_safe_read_pins_bounds_hashes_and_all_fixed_cases(self):
        for _, folder, profile in synthetic.SHORTCUT_CASES:
            self.assertIn(folder, self.reader)
            self.assertIn(profile, self.reader)
            self.assertIn('!insertmacro SyntheticShortcutCase "' + folder + '" "' + profile + '"', self.fixture)
        for guard in ("0x02200000", "0x00200000", "(info.Attributes & 0x410)", "info.Links != 1",
                      "info.SizeLow > 65536", "const int Limit = 1024", "$MaxReportBytes = 1048576"):
            self.assertIn(guard, self.reader)
        self.assertIn("sha256_before", self.reader)
        self.assertIn("sha256_after", self.reader)
        self.assertIn("identity_before", self.reader)
        self.assertIn("identity_after", self.reader)
        self.assertIn("CreateFileW(path, 0x81, 3,", self.reader)
        self.assertIn("using (var stream = PinFile(path))", self.reader)
        self.assertNotIn("$files.Add", self.reader)
        self.assertIn("before any COM load", self.reader)
        self.assertIn("$case.links[1].sha256_before -ceq $case.links[3].sha256_before", self.reader)
        self.assertIn("$case.links[2].sha256_before -ceq $case.links[4].sha256_before", self.reader)
        self.assertIn("A passing probe does not explain the earlier real installer failure", self.reader)

    def test_fixture_is_owned_fixed_inert_new_only_and_uses_production_primitives(self):
        main = self.fixture.split("Function NativeSafetyFixtureMain", 1)[1].split("FunctionEnd", 1)[0]
        for earlier in ("Call CreateFreshInstallDirectory", "Call EnsureOwnedRoot", "WriteUninstaller"):
            self.assertLess(main.index(earlier), main.index('"create-shortcut-roundtrip-fixtures"'))
        self.assertLess(main.index('"create-shortcut-roundtrip-fixtures"'), main.index('"initialization-complete"'))
        self.assertEqual(main.count("!insertmacro SyntheticShortcutCase"), 3)
        macro = self.fixture.split("!macro SyntheticShortcutCase", 1)[1].split("!macroend", 1)[0]
        self.assertEqual(macro.count("Call WriteNewAsciiFile"), 2)
        self.assertEqual(macro.count("!insertmacro SyntheticNewShortcut "), 3)
        self.assertEqual(macro.count("Call CopyNewShortcut"), 2)
        self.assertIn(r"\relocated\desktop.lnk", macro)
        self.assertIn(r"\relocated\program.lnk", macro)
        create = self.fixture.split("!macro SyntheticNewShortcut PATH", 1)[1].split("!macroend", 1)[0]
        self.assertIn("${OrIf} $NativeError != 2", create)
        self.assertLess(create.index("Call OpenNoReparse"), create.index("CreateShortcut"))
        self.assertIn("IfErrors 0 +2\n    Call DataRefusal", create)
        for forbidden in ("$DESKTOP", "$SMPROGRAMS", "$APPDATA", "$LOCALAPPDATA", "ExecShell", "ExecWait", "WriteReg"):
            self.assertNotIn(forbidden, self.fixture)
        self.assertIn("absence-check/write race", self.fixture)


if __name__ == "__main__":
    unittest.main()
