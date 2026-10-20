"""Bounded installer outcome evidence; PowerShell execution uses mocks only."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/test_native_installer.ps1"


def bounded_function():
    source = SCRIPT.read_text(encoding="utf-8")
    return source[source.index("function Run-Bounded("):source.index("function Association-Snapshot")]


class InstallerDiagnosticTests(unittest.TestCase):
    def test_report_is_exclusive_bounded_and_has_no_command_or_user_data(self):
        source = bounded_function()
        for expected in ("finally {", "[IO.FileMode]::CreateNew",
                         "$bytes.Length -le 4096", "$script:InstallerOperationNumber -le 32",
                         "error_type = $null", "error_hresult = $null",
                         "$attempt.exit_code = $process.ExitCode",
                         "$attempt.timed_out = $true", "process_tree_kill_requested"):
            self.assertIn(expected, source)
        report = source[source.index("$attempt = [ordered]@{"):source.index("$timer =")]
        for forbidden in ("$File", "$Arguments", "$env:", "$installDir", "Get-Content",
                          "Get-ItemProperty", "Exception.Message"):
            self.assertNotIn(forbidden, report)
        self.assertNotIn("Exception.Message", source)
        self.assertNotIn("process_tree_terminated", source)
        self.assertLess(source.index("$process = Start-Process"), source.index("$attempt.started = $true"))
        self.assertLess(source.index("$process.Refresh()"), source.index("$attempt.exit_code"))

    def test_first_install_and_places_have_distinct_fixed_phase_labels(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("180 'initial-install'", source)
        self.assertIn("600 'places-xpcshell'", source)
        self.assertLess(source.index("$script:InstallerOperationNumber = 0"),
                        source.index("$exitCode = Run-Bounded $Installer"))
        self.assertLess(source.index("180 'initial-install'"),
                        source.index("'Silent per-user installation failed.'"))

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell mock contract runs on Windows CI")
    def test_mocked_success_nonzero_start_error_and_timeout_retain_truthful_outcomes(self):
        # Start-Process is shadowed below. No executable or installer is launched
        # by the extracted function; only the installed PowerShell interpreter
        # runs this self-authored mock test with profiles disabled.
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            output = str(folder / "final-report.json").replace("'", "''")
            script = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-True([bool]$Condition, [string]$Description) {
    if (-not $Condition) { throw $Description }
}
""" + bounded_function() + "\n$Output = '" + output + r"""'
$script:InstallerOperationNumber = 0
$script:StartFailure = $false
$script:Timeout = $false
$script:Killed = $false
$script:Fake = [pscustomobject]@{ ExitCode = 0; HasExited = $false }
$script:Fake | Add-Member ScriptMethod WaitForExit {
    param($Milliseconds)
    if ($script:Timeout -and -not $script:Killed) { return $false }
    $this.HasExited = $true
    return $true
}
$script:Fake | Add-Member ScriptMethod Refresh { }
$script:Fake | Add-Member ScriptMethod Kill {
    param($EntireProcessTree)
    Assert-True ($EntireProcessTree -eq $true) 'Only the created process tree is targeted.'
    $script:Killed = $true
    $this.HasExited = $true
}
function Start-Process {
    param([string]$FilePath, [string[]]$ArgumentList, [switch]$PassThru)
    if ($script:StartFailure) { throw 'private start-error text must not be retained' }
    return $script:Fake
}
Assert-True ((Run-Bounded 'never-executed-private-path' @('private-argument') 1 'initial-install') -eq 0) 'Success changed.'
$script:Fake.ExitCode = 7
Assert-True ((Run-Bounded 'never-executed-private-path' @('private-argument') 1) -eq 7) 'Failure exit changed.'
$script:StartFailure = $true
$caught = $false
try { Run-Bounded 'never-executed-private-path' @() 1 } catch { $caught = $true }
Assert-True $caught 'Start error was swallowed.'
$script:StartFailure = $false
$script:Timeout = $true
$script:Fake.HasExited = $false
$caught = $false
try { Run-Bounded 'never-executed-private-path' @() 1 'places-xpcshell' } catch { $caught = $true }
Assert-True ($caught -and $script:Killed) 'Timeout did not fail and request exact-tree termination.'
"""
            result = subprocess.run(
                [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            reports = sorted(folder.glob("windows-installer-operation-*.json"))
            self.assertEqual(len(reports), 4)
            values = [json.loads(path.read_text(encoding="utf-8")) for path in reports]
            for index, (path, value) in enumerate(zip(reports, values), 1):
                self.assertLessEqual(path.stat().st_size, 4096)
                self.assertEqual(value["sequence"], index)
                self.assertIn(type(value["elapsed_seconds"]), (int, float))
                self.assertGreaterEqual(value["elapsed_seconds"], 0)
                self.assertNotIn("private", path.read_text())
            self.assertEqual([value["exit_code"] for value in values], [0, 7, None, None])
            self.assertEqual([value["started"] for value in values], [True, True, False, True])
            self.assertEqual([value["timed_out"] for value in values], [False, False, False, True])
            self.assertEqual([value["process_tree_kill_requested"] for value in values],
                             [False, False, False, True])
            self.assertIsNone(values[0]["error_type"])
            self.assertIsNone(values[1]["error_type"])
            self.assertIsInstance(values[2]["error_type"], str)
            self.assertIsInstance(values[3]["error_type"], str)
            self.assertFalse((folder / "final-report.json").exists())


if __name__ == "__main__":
    unittest.main()
