# Diagnostic-only reads of three fixed, freshly authored synthetic link cases.
# No arguments, user paths, shell folders, registry, resolution, saving or launch.
[CmdletBinding()]
param()
# Binding rejects undeclared inputs; reject bound common parameters below too.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = 'D:\pb\installer-syntax\execution\app\shortcut-probe'
$Output = 'D:\pb\evidence\installer-synthetic-shortcuts.json'
$MaxString = 1024
$MaxReportBytes = 1048576

function Assert-True([bool]$Condition) {
    if (-not $Condition) { throw [InvalidOperationException]::new('Synthetic shortcut invariant failed.') }
}
Assert-True ($IsWindows -and $env:GITHUB_ACTIONS -ceq 'true' -and $env:RUNNER_ENVIRONMENT -ceq 'github-hosted')
Assert-True ($PSBoundParameters.Count -eq 0)

# One shared authoritative reader for synthetic and actual installer acceptance.
. (Join-Path $PSScriptRoot 'windows_shortcut_reader.ps1')

$report = [ordered]@{
    schema_version = 2; authoritative_reader = 'IShellLinkW'; wsh_role = 'non-authoritative diagnostic'; purpose = 'isolated synthetic shortcut round-trip readback'
    fixture_root = $Root; case_count = 3; passed = $false; cases = @()
    error_type = $null; error_hresult = $null
    get_path_flags = 0; persist_load_mode = 0; process_bits = [IntPtr]::Size * 8
    powershell_version = $PSVersionTable.PSVersion.ToString(); browser_executed = $false
    shortcut_executed = $false; resolve_called = $false; save_called = $false
    production_installer_tested = $false; release_eligible = $false
    limitations = 'Synthetic link semantics only. No icon rendering, browser execution or concurrent-writer proof. A passing probe does not explain the earlier real installer failure.'
}
$directories = [Collections.Generic.List[IDisposable]]::new()
$outputStream = $null
$exitCode = 1
try {
    # Pin every existing ancestor top-down before opening its descendants. The
    # evidence directory must already exist; this reader never creates folders.
    foreach ($path in @('D:\', 'D:\pb', 'D:\pb\evidence')) {
        $directories.Add([PrivacyBrowserShortcut.FileSafety]::PinDirectory($path))
    }
    $outputStream = [IO.File]::Open($Output, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    foreach ($path in @('D:\pb\installer-syntax', 'D:\pb\installer-syntax\execution',
                       'D:\pb\installer-syntax\execution\app', $Root)) {
        $directories.Add([PrivacyBrowserShortcut.FileSafety]::PinDirectory($path))
    }
    $definitions = @(
        @{ id = 'ascii'; folder = 'PrivacyBrowser-ASCII'; profile = 'ascii profile' },
        @{ id = 'unicode'; folder = 'PrivacyBrowser-日本語'; profile = '日本語 profile' },
        @{ id = 'unicode-space'; folder = 'PrivacyBrowser 日本語 Space'; profile = '日本語 space profile' }
    )
    foreach ($definition in $definitions) {
        $caseRoot = $Root + '\' + $definition.folder
        $asciiCopyRoot = $Root + '\ascii-link-copies-' + $definition.id
        $case = [ordered]@{
            id = $definition.id; passed = $false; error_type = $null; error_hresult = $null
            expected = [ordered]@{
                target = $caseRoot + '\privacybrowser.exe'
                arguments = '-no-remote -profile "' + $Root + '\profiles\' + $definition.profile + '"'
                icon = $caseRoot + '\reference.ico'; icon_index = 0; working_directory = $caseRoot
            }
            links = @(); copies_identical = $false; files_unchanged = $false
            ascii_copy_directory = $asciiCopyRoot; ascii_copies_identical = $false
        }
        $report.cases += $case
        try {
            $directories.Add([PrivacyBrowserShortcut.FileSafety]::PinDirectory($caseRoot))
            $directories.Add([PrivacyBrowserShortcut.FileSafety]::PinDirectory($caseRoot + '\relocated'))
            Assert-True ($asciiCopyRoot -cmatch '^[\x00-\x7f]+$')
            $directories.Add([PrivacyBrowserShortcut.FileSafety]::PinDirectory($asciiCopyRoot))
            # Validate inert references without holding file locks during COM.
            foreach ($path in @($case.expected.target, $case.expected.icon)) {
                [void][PrivacyBrowserShortcut.FileSafety]::ObserveFile($path)
            }
            $entries = @(
                @{ kind = 'direct'; relative = 'direct.lnk'; wsh = $true },
                @{ kind = 'desktop-snapshot'; relative = 'PrivacyBrowser.desktop-link'; wsh = $false },
                @{ kind = 'program-snapshot'; relative = 'PrivacyBrowser.program-link'; wsh = $false },
                @{ kind = 'desktop-copy'; relative = 'relocated\desktop.lnk'; wsh = $true },
                @{ kind = 'program-copy'; relative = 'relocated\program.lnk'; wsh = $true }
            )
            # Append controls without replacing any original observation. The
            # copied bytes still describe the same case target and arguments.
            $asciiEntries = @(
                @{ kind = 'desktop-ascii-copy'; relative = 'desktop.lnk'; wsh = $true },
                @{ kind = 'program-ascii-copy'; relative = 'program.lnk'; wsh = $true }
            )
            foreach ($entry in $entries) { $entry.path = $caseRoot + '\' + $entry.relative }
            foreach ($entry in $asciiEntries) { $entry.path = $asciiCopyRoot + '\' + $entry.relative }
            $entries += $asciiEntries
            foreach ($entry in $entries) {
                $path = $entry.path
                $before = [PrivacyBrowserShortcut.FileSafety]::ObserveFile($path)
                $case.links += [ordered]@{
                    kind = $entry.kind; path = $path; bytes = $before.bytes
                    identity_before = $before.identity; identity_after = $null; bytes_after = $null
                    sha256_before = $before.sha256; sha256_after = $null
                    wide = $null; wsh = $null; wsh_observed = $entry.wsh; passed = $false
                }
            }
            foreach ($link in $case.links) {
                $link.wide = [PrivacyBrowserShortcut.LinkReader]::Read($link.path)
                $link.passed = Test-WideShortcut $link.wide $case.expected
                if ($link.wsh_observed) { $link.wsh = Read-Wsh $link.path }
            }
            # Readback must not modify either custom-extension snapshots or any
            # direct/copied .lnk. Reopen without reparses and retain identity too.
            foreach ($link in $case.links) {
                $after = [PrivacyBrowserShortcut.FileSafety]::ObserveFile($link.path)
                $link.bytes_after = $after.bytes
                $link.sha256_after = $after.sha256
                $link.identity_after = $after.identity
                $link.passed = ($link.passed -and $link.sha256_before -ceq $link.sha256_after -and
                                $link.identity_before -ceq $link.identity_after -and $link.bytes -eq $after.bytes)
            }
            $case.files_unchanged = @($case.links | Where-Object {
                $_.sha256_before -cne $_.sha256_after -or $_.identity_before -cne $_.identity_after
            }).Count -eq 0
            $case.copies_identical = ($case.links[1].sha256_before -ceq $case.links[3].sha256_before -and
                                     $case.links[2].sha256_before -ceq $case.links[4].sha256_before)
            $case.ascii_copies_identical = ($case.links[1].sha256_before -ceq $case.links[5].sha256_before -and
                                          $case.links[1].bytes -eq $case.links[5].bytes -and
                                          $case.links[2].sha256_before -ceq $case.links[6].sha256_before -and
                                          $case.links[2].bytes -eq $case.links[6].bytes)
            $case.passed = ($case.files_unchanged -and $case.copies_identical -and $case.ascii_copies_identical -and
                           @($case.links | Where-Object { -not $_.passed }).Count -eq 0)
        } catch {
            $case.error_type = $_.Exception.GetType().FullName
            $case.error_hresult = $_.Exception.HResult
        }
    }
    $report.passed = ($report.cases.Count -eq 3 -and @($report.cases | Where-Object { -not $_.passed }).Count -eq 0)
    if ($report.passed) { $exitCode = 0 }
} catch {
    $report.error_type = $_.Exception.GetType().FullName
    $report.error_hresult = $_.Exception.HResult
} finally {
    try {
        if ($null -ne $outputStream) {
            $bytes = [Text.Encoding]::UTF8.GetBytes(($report | ConvertTo-Json -Depth 12 -Compress) + [Environment]::NewLine)
            Assert-True ($bytes.Length -le $MaxReportBytes)
            $outputStream.Write($bytes, 0, $bytes.Length)
            $outputStream.Flush($true)
        }
    } finally {
        if ($null -ne $outputStream) { $outputStream.Dispose() }
        for ($index = $directories.Count - 1; $index -ge 0; $index--) { $directories[$index].Dispose() }
    }
}
if ($exitCode -ne 0) { [Console]::Error.WriteLine('Synthetic shortcut readback failed; retain the fixed diagnostic JSON.') }
exit $exitCode
