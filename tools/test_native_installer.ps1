# Execute only a validated, newly built installer on an ephemeral hosted runner.
# This test never changes default-browser choices. Destructive cases use only
# newly generated, manifest-owned fixture profiles in this disposable runner.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$Runtime,
    [Parameter(Mandatory=$true)][string]$WorkRoot,
    [Parameter(Mandatory=$true)][string]$Output
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-True([bool]$Condition, [string]$Description) {
    if (-not $Condition) { throw $Description }
}
function File-Hash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Run-Bounded([string]$File, [string[]]$Arguments, [int]$Seconds = 120) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -PassThru
    if (-not $process.WaitForExit($Seconds * 1000)) {
        # This is the exact process tree created by this test, never a name match.
        if (-not $process.HasExited) { $process.Kill($true) }
        throw 'The generated installer process exceeded the test timeout.'
    }
    $process.Refresh()
    return $process.ExitCode
}
function Association-Snapshot {
    # Compare selected default-browser/file-association state without logging it.
    $keys = @(
        'HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice',
        'HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.htm\UserChoice',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.html\UserChoice',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice',
        'HKCU:\Software\RegisteredApplications',
        'HKLM:\Software\RegisteredApplications'
    )
    $snapshot = [ordered]@{}
    foreach ($key in $keys) {
        if (Test-Path -LiteralPath $key) {
            $values = [ordered]@{}
            $item = Get-Item -LiteralPath $key
            foreach ($name in ($item.GetValueNames() | Sort-Object)) {
                $values[$name] = $item.GetValue($name)
            }
            $snapshot[$key] = $values
        } else { $snapshot[$key] = $null }
    }
    return ($snapshot | ConvertTo-Json -Depth 8 -Compress)
}

Assert-True ($env:GITHUB_ACTIONS -eq 'true' -and $env:RUNNER_ENVIRONMENT -eq 'github-hosted') 'Installer execution is restricted to ephemeral GitHub-hosted runners.'
Assert-True ($IsWindows) 'Windows is required.'
$WorkRoot = [IO.Path]::GetFullPath($WorkRoot)
$Installer = [IO.Path]::GetFullPath($Installer)
$Runtime = [IO.Path]::GetFullPath($Runtime)
$Output = [IO.Path]::GetFullPath($Output)
Assert-True ($WorkRoot -ceq 'D:\pb\installer-test') 'Use the dedicated fresh D:\pb\installer-test directory.'
foreach ($path in @($Installer, $Runtime, $Output)) {
    Assert-True ($path.StartsWith('D:\pb\', [StringComparison]::OrdinalIgnoreCase)) 'Test inputs and output must remain inside the dedicated build root.'
}
Assert-True (-not (Test-Path -LiteralPath $WorkRoot)) 'The installer-test directory already exists; it will not be overwritten.'
Assert-True (-not (Test-Path -LiteralPath $Output)) 'An installer-test report already exists; it will not be overwritten.'
Assert-True ([IO.Path]::GetFileName($Installer) -ceq 'PrivacyBrowser-0.1.0-dev-win64-setup-UNSIGNED.exe') 'Unexpected installer basename.'
$proof = Get-Content -LiteralPath ($Installer + '.json') -Raw | ConvertFrom-Json
$installerHash = File-Hash $Installer
$nativeHash = File-Hash (Join-Path $Runtime 'privacybrowser.exe')
Assert-True ($proof.sha256 -ceq $installerHash -and $proof.native_binary_sha256 -ceq $nativeHash) 'Installer proof hashes do not match the files under test.'
Assert-True ($proof.build_kind -ceq 'full-source-build-candidate-installer' -and $proof.installer_compiled -eq $true -and $proof.release_eligible -eq $false) 'A validated source-build candidate is required.'
Assert-True ($proof.installer_static_validation.installer_static_identity_passed -eq $true) 'Static native installer identity validation is required.'

# Keep the existing guard below; this shared read-only check adds target-labelled
# failure evidence and also rejects reparse entries and inherited test overrides.
& python (Join-Path $PSScriptRoot 'windows_test_roots.py') --phase 'before-installer'
Assert-True ($LASTEXITCODE -eq 0) 'Fresh product-root checks failed; inspect windows-test-roots-before-installer.json.'

# Exercise Unicode path round-tripping in ownership markers and native startup.
$installDir = Join-Path $WorkRoot 'PrivacyBrowser-日本語'
$exe = Join-Path $installDir 'privacybrowser.exe'
$uninstaller = Join-Path $installDir 'uninstall.exe'
$marker = Join-Path $installDir 'PrivacyBrowser.install-marker'
$profileRoot = Join-Path $env:LOCALAPPDATA 'PrivacyBrowser'
$roamingRoot = Join-Path $env:APPDATA 'PrivacyBrowser'
$profile = Join-Path $profileRoot 'Profile'
$desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'PrivacyBrowser.lnk'
$programFolder = Join-Path ([Environment]::GetFolderPath('Programs')) 'PrivacyBrowser'
$programLink = Join-Path $programFolder 'PrivacyBrowser.lnk'
$registry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser'
foreach ($path in @($profileRoot, $roamingRoot, $desktopLink, $programFolder, $registry)) {
    Assert-True (-not (Test-Path -LiteralPath $path)) 'A pre-existing PrivacyBrowser profile, shortcut or uninstall entry prevents this isolated test.'
}
$beforeAssociations = Association-Snapshot
New-Item -ItemType Directory -Path $WorkRoot | Out-Null
$profileFixture = Join-Path $profile 'retained-profile-fixture.txt'
$downloadFixture = Join-Path $WorkRoot 'retained-download-fixture.txt'
Set-Content -LiteralPath $downloadFixture -Value 'retain this generated download fixture' -NoNewline
$downloadHash = File-Hash $downloadFixture
$checks = [ordered]@{}

# A real Win32 fixture verifies that FILE_LIST_DIRECTORY|READ_ATTRIBUTES with
# read/write sharing, but no delete sharing, blocks directory renaming.
# This does not by itself prove resistance to in-place reparse mutation.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PrivacyBrowserDirectorySharingProbe {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    public static extern IntPtr CreateFileW(string path, uint access, uint sharing,
        IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool MoveFileExW(string existing, string destination, uint flags);
    [DllImport("kernel32.dll", ExactSpelling=true, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool CloseHandle(IntPtr handle);
}
'@
$sharingProbe = Join-Path $WorkRoot 'directory-sharing-probe'
$renamedProbe = Join-Path $WorkRoot 'directory-sharing-probe-renamed'
New-Item -ItemType Directory -Path $sharingProbe | Out-Null
$directoryHandle = [PrivacyBrowserDirectorySharingProbe]::CreateFileW($sharingProbe, 0x81, 3, [IntPtr]::Zero, 3, 0x02200000, [IntPtr]::Zero)
Assert-True ($directoryHandle -ne [IntPtr]::new(-1)) 'Cannot open the generated directory-sharing fixture.'
try {
    $renamed = [PrivacyBrowserDirectorySharingProbe]::MoveFileExW($sharingProbe, $renamedProbe, 0)
    $renameError = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    Assert-True (-not $renamed -and $renameError -eq 32) 'The directory handle did not block renaming with a sharing violation.'
} finally {
    Assert-True ([PrivacyBrowserDirectorySharingProbe]::CloseHandle($directoryHandle)) 'Directory fixture handle did not close.'
}
[IO.Directory]::Move($sharingProbe, $renamedProbe)
Assert-True (Test-Path -LiteralPath $renamedProbe -PathType Container) 'The unlocked fixture could not be renamed.'
$checks.directory_handle_blocks_rename = $true

$exitCode = Run-Bounded $Installer @('/S', ('/D=' + $installDir)) 180
Assert-True ($exitCode -eq 0) 'Silent per-user installation failed.'
Assert-True ((File-Hash $exe) -ceq $nativeHash) 'The installed executable differs from the validated native runtime.'
Assert-True ((Get-Content -LiteralPath $marker -Raw) -ceq 'PrivacyBrowser 0.1.0-dev') 'The generated installation marker is invalid.'
$checks.install_succeeded = $true
Assert-True ((Test-Path -LiteralPath $profileRoot -PathType Container) -and (Test-Path -LiteralPath $roamingRoot -PathType Container)) 'The installer did not create its isolated owned data roots.'
foreach ($root in @($profileRoot, $roamingRoot)) {
    $ownership = Join-Path $root 'PrivacyBrowser.userdata-owner'
    $expectedOwnership = "PrivacyBrowser-UserData-v1`r`n$installDir`r`n$root`r`n"
    Assert-True ([IO.File]::ReadAllText($ownership, [Text.Encoding]::Unicode) -ceq $expectedOwnership) 'The UTF-16 ownership marker does not bind the exact Unicode installation and data root.'
}
$checks.unicode_owned_root_markers = $true
Set-Content -LiteralPath $profileFixture -Value 'synthetic bookmarks and credentials; retain without explicit purge' -NoNewline
$profileHash = File-Hash $profileFixture
$cacheFixture = Join-Path $profileRoot 'cache-fixture.bin'
$roamingFixture = Join-Path $roamingRoot 'roaming-profile-fixture.txt'
Set-Content -LiteralPath $cacheFixture -Value 'synthetic local cache' -NoNewline
Set-Content -LiteralPath $roamingFixture -Value 'synthetic roaming profile' -NoNewline
$cacheHash = File-Hash $cacheFixture
$roamingHash = File-Hash $roamingFixture
$payload = @(Get-ChildItem -LiteralPath $Runtime -Recurse -File)
foreach ($file in $payload) {
    $relative = [IO.Path]::GetRelativePath($Runtime, $file.FullName)
    $installed = Join-Path $installDir $relative
    Assert-True ((Test-Path -LiteralPath $installed -PathType Leaf) -and (File-Hash $installed) -ceq (File-Hash $file.FullName)) ('Installed payload mismatch: ' + $relative)
}
$checks.exact_payload_hashes = $true
$version = (Get-Item -LiteralPath $exe).VersionInfo
Assert-True ($version.ProductName -ceq 'PrivacyBrowser' -and $version.FileDescription -ceq 'PrivacyBrowser') 'Installed executable metadata is not PrivacyBrowser.'
$checks.native_display_metadata = $true
$shell = New-Object -ComObject WScript.Shell
foreach ($link in @($desktopLink, $programLink)) {
    Assert-True (Test-Path -LiteralPath $link -PathType Leaf) 'An expected per-user shortcut is missing.'
    $shortcut = $shell.CreateShortcut($link) # Read only; Save is never called.
    Assert-True ($shortcut.TargetPath -ieq $exe) 'A shortcut points to the wrong native executable.'
    Assert-True ($shortcut.Arguments -ceq ('-no-remote -profile "' + $profile + '"')) 'A shortcut uses the wrong independent profile.'
    $expectedIcon = Join-Path $installDir 'distribution\privacybrowser\assets\privacybrowser.ico'
    Assert-True ($shortcut.IconLocation -ieq ($expectedIcon + ',0') -or $shortcut.IconLocation -ieq $expectedIcon) 'A shortcut uses the wrong icon.'
}
$checks.native_shortcuts = $true
$entry = Get-ItemProperty -LiteralPath $registry
Assert-True ($entry.DisplayName -ceq 'PrivacyBrowser' -and $entry.InstallLocation -ieq $installDir -and $entry.UninstallString -ceq ('"' + $uninstaller + '"')) 'The per-user uninstall registration is invalid.'
$checks.per_user_registration = $true
# Xpcshell's crash helper also uses the actual roaming profile folder. Run the
# genuine Places test only now: both roots were absent, then created by this
# installer, and their exact ownership/payload/registration were verified above.
$placesExit = Run-Bounded 'cmd.exe' @('/d', '/c', 'D:\pb\run-places-smoke.cmd') 600
Assert-True ($placesExit -eq 0) 'The genuine Places xpcshell test failed.'
& python (Join-Path $PSScriptRoot 'windows_places_smoke.py') --work-root 'D:\pb'
Assert-True ($LASTEXITCODE -eq 0) 'Matching retained Places test evidence is required.'
$checks.places_xpcshell_completed = $true
# Gecko's launcher can write a blocklist beneath its known RoamingAppData root,
# even with --profile. Establish installer ownership before native runtime smoke.
& python (Join-Path $PSScriptRoot 'windows_native_evidence.py') --work-root 'D:\pb' --smoke
Assert-True ($LASTEXITCODE -eq 0) 'Native version/local-render smoke failed.'
$checks.native_runtime_smoke_completed = $true
$unknown = Join-Path $installDir 'retained-unknown-fixture.txt'
Set-Content -LiteralPath $unknown -Value 'retain this unknown generated file' -NoNewline
$unknownHash = File-Hash $unknown

# Exercise a locked payload without relying on a particular browser profile.
$handle = [IO.File]::Open($exe, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
try {
    $exitCode = Run-Bounded $uninstaller @('/S', ('_?=' + $installDir)) 30
    Assert-True ($exitCode -ne 0) 'Uninstall did not reject a locked installed executable.'
    Assert-True ((Test-Path -LiteralPath $marker) -and (Test-Path -LiteralPath $registry) -and (File-Hash $exe) -ceq $nativeHash) 'A refused uninstall altered the installation.'
} finally { $handle.Dispose() }
$checks.locked_payload_uninstall_refused = $true

# A separate real browser process uses a different profile. No sandbox/security
# preferences are changed, and the test terminates only its own process tree.
$browserProfile = Join-Path $WorkRoot 'active-browser-profile'
New-Item -ItemType Directory -Path $browserProfile | Out-Null
# Retain only these two diagnostic streams with the report, including failure.
# Never upload the installer-test directory containing profiles and binaries.
$browserStdout = Join-Path (Split-Path -Parent $Output) 'active-browser.stdout.log'
$browserStderr = Join-Path (Split-Path -Parent $Output) 'active-browser.stderr.log'
foreach ($log in @($browserStdout, $browserStderr)) {
    Assert-True (-not (Test-Path -LiteralPath $log)) 'An active-browser diagnostic log already exists; it will not be overwritten.'
}
$browser = Start-Process -FilePath $exe -ArgumentList @('--headless', '--no-remote', '--profile', $browserProfile, 'about:blank') -RedirectStandardOutput $browserStdout -RedirectStandardError $browserStderr -PassThru
try {
    Start-Sleep -Seconds 5
    $browser.Refresh()
    Assert-True (-not $browser.HasExited) 'The installed browser exited before the active-browser uninstall check.'
    $exitCode = Run-Bounded $uninstaller @('/S', ('_?=' + $installDir)) 30
    Assert-True ($exitCode -ne 0) 'Uninstall did not reject a running installed browser with a different profile.'
    Assert-True ((Test-Path -LiteralPath $marker) -and (Test-Path -LiteralPath $registry) -and (File-Hash $exe) -ceq $nativeHash) 'A refused active-browser uninstall altered the installation.'
} finally {
    if (-not $browser.HasExited) { $browser.Kill($true); $browser.WaitForExit() }
}
$checks.active_browser_uninstall_refused = $true

$exitCode = Run-Bounded $uninstaller @('/S') 120
Assert-True ($exitCode -eq 0) 'Uninstaller launch failed.'
# NSIS copies itself to TEMP; wait for that normal child to remove its metadata.
$deadline = [DateTime]::UtcNow.AddSeconds(90)
while (((Test-Path -LiteralPath $marker) -or (Test-Path -LiteralPath $registry) -or (Test-Path -LiteralPath $uninstaller)) -and [DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 500
}
foreach ($file in $payload) {
    $relative = [IO.Path]::GetRelativePath($Runtime, $file.FullName)
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $installDir $relative))) ('Uninstall retained a known payload file: ' + $relative)
}
foreach ($path in @($marker, $uninstaller, $registry, $desktopLink, $programLink)) {
    Assert-True (-not (Test-Path -LiteralPath $path)) 'Uninstall did not remove its own generated metadata or shortcut.'
}
$checks.uninstall_exact_payload_and_metadata = $true
Assert-True ((File-Hash $unknown) -ceq $unknownHash -and (File-Hash $profileFixture) -ceq $profileHash -and (File-Hash $downloadFixture) -ceq $downloadHash) 'Uninstall changed an unknown file, profile or download fixture.'
$checks.profile_download_unknown_file_retention = $true
Assert-True ((File-Hash $cacheFixture) -ceq $cacheHash -and (File-Hash $roamingFixture) -ceq $roamingHash) 'Ordinary silent uninstall removed user data without explicit opt-in.'
$checks.default_silent_retains_user_data = $true

# Reinstall the same path, reusing only its own correctly bound ownership
# markers. Remove only this test's exact unknown fixture and now-empty folder.
[IO.File]::Delete($unknown)
[IO.Directory]::Delete($installDir)
$exitCode = Run-Bounded $Installer @('/S', ('/D=' + $installDir)) 180
Assert-True ($exitCode -eq 0 -and (File-Hash $exe) -ceq $nativeHash) 'Reinstallation with this install''s retained owned data failed.'
Assert-True ((File-Hash $profileFixture) -ceq $profileHash) 'Reinstall changed the retained profile fixture.'
Set-Content -LiteralPath $unknown -Value 'retain this unknown generated file' -NoNewline
$checks.reinstall_preserves_owned_profile = $true

# Run refusal probes from a separate identical copy, just as NSIS normally
# copies its uninstaller to TEMP. Executing the installed image in-place would
# lock uninstall.exe itself and falsely satisfy later file-safety refusals.
$negativeUninstaller = Join-Path $WorkRoot 'negative-case-uninstall.exe'
Assert-True (-not (Test-Path -LiteralPath $negativeUninstaller)) 'The negative-case uninstaller destination must be new.'
[IO.File]::Copy($uninstaller, $negativeUninstaller, $false)
$uninstallerHash = File-Hash $uninstaller
function Run-PurgeRefusal {
    Assert-True ((File-Hash $negativeUninstaller) -ceq $uninstallerHash -and (File-Hash $uninstaller) -ceq $uninstallerHash) 'The negative-case uninstaller differs from its installed original.'
    return (Run-Bounded $negativeUninstaller @('/S', '/PURGEUSERDATA', ('_?=' + $installDir)) 30)
}

# A corrupt ownership marker must be rejected before either user or app data
# is deleted. Restore only the exact marker bytes generated by this installer.
$ownerMarker = Join-Path $profileRoot 'PrivacyBrowser.userdata-owner'
$ownerBytes = [IO.File]::ReadAllBytes($ownerMarker)
try {
    [IO.File]::WriteAllBytes($ownerMarker, [Text.Encoding]::UTF8.GetBytes('foreign ownership fixture'))
    $exitCode = Run-PurgeRefusal
    Assert-True ($exitCode -ne 0 -and (Test-Path -LiteralPath $marker) -and (Test-Path -LiteralPath $registry)) 'Invalid ownership marker did not block destructive uninstall.'
    Assert-True ((File-Hash $exe) -ceq $nativeHash -and (File-Hash $profileFixture) -ceq $profileHash -and (File-Hash $roamingFixture) -ceq $roamingHash) 'Ownership refusal modified app or user data.'
} finally { [IO.File]::WriteAllBytes($ownerMarker, $ownerBytes) }
$checks.ownership_marker_purge_refused = $true

# Registry fixtures exercise per-binary deletion while preserving shared keys,
# a different PrivacyBrowser installation, and a separate Firefox namespace.
$launcherKey = 'HKCU:\Software\PrivacyBrowser\PrivacyBrowser\Launcher'
$skeletonKey = 'HKCU:\Software\PrivacyBrowser\PrivacyBrowser\PreXULSkeletonUISettings'
$firefoxKey = 'HKCU:\Software\Mozilla\Firefox\PrivacyBrowserUninstallFixture'
Assert-True (-not (Test-Path -LiteralPath $firefoxKey)) 'The foreign-registry fixture already exists.'
New-Item -Path $launcherKey, $skeletonKey, $firefoxKey -Force | Out-Null
$launcherSuffixes = @('|Launcher', '|Browser', '|Image', '|Telemetry', '|Blocklist', '|LauncherCrashTime')
$skeletonSuffixes = @('|Enabled', '|ScreenX', '|ScreenY', '|Width', '|Height', '|Maximized', '|UrlbarCSSSpan', '|CssToDevPixelScaling', '|SearchbarCSSSpan', '|SpringsCSSSpan', '|Theme', '|Flags', '|Progress')
foreach ($suffix in $launcherSuffixes) { New-ItemProperty -LiteralPath $launcherKey -Name ($exe + $suffix) -Value 'owned synthetic value' -PropertyType String -Force | Out-Null }
foreach ($suffix in $skeletonSuffixes) { New-ItemProperty -LiteralPath $skeletonKey -Name ($exe + $suffix) -Value 'owned synthetic value' -PropertyType String -Force | Out-Null }
$foreignName = 'D:\pb\foreign-browser\privacybrowser.exe|Launcher'
New-ItemProperty -LiteralPath $launcherKey -Name $foreignName -Value 'foreign installation' -PropertyType String -Force | Out-Null
New-ItemProperty -LiteralPath $launcherKey -Name 'UnrelatedSentinel' -Value 'shared value' -PropertyType String -Force | Out-Null
New-ItemProperty -LiteralPath $skeletonKey -Name 'UnrelatedSentinel' -Value 'shared value' -PropertyType String -Force | Out-Null
New-ItemProperty -LiteralPath $firefoxKey -Name 'Sentinel' -Value 'Firefox data must remain' -PropertyType String | Out-Null

# A junction to an outside download must cause a refusal before any purge.
$outside = Join-Path $WorkRoot 'foreign-downloads'
New-Item -ItemType Directory -Path $outside | Out-Null
$outsideFile = Join-Path $outside 'download.txt'
Set-Content -LiteralPath $outsideFile -Value 'external downloaded file must remain' -NoNewline
$outsideHash = File-Hash $outsideFile
$junction = Join-Path $profile 'outside-junction'
New-Item -ItemType Junction -Path $junction -Target $outside | Out-Null
try {
    $exitCode = Run-PurgeRefusal
    Assert-True ($exitCode -ne 0 -and (Test-Path -LiteralPath $marker) -and (Test-Path -LiteralPath $registry)) 'A reparse-point purge did not fail closed.'
    Assert-True ((File-Hash $exe) -ceq $nativeHash -and (File-Hash $profileFixture) -ceq $profileHash -and (File-Hash $outsideFile) -ceq $outsideHash) 'Reparse refusal modified owned or foreign files.'
} finally {
    # Delete the exact junction itself without traversing it; never recurse.
    [IO.Directory]::Delete($junction)
}
$checks.reparse_purge_refused = $true

# A locked synthetic cache file must retain the app/uninstaller for recovery.
$cacheHandle = [IO.File]::Open($cacheFixture, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
try {
    $exitCode = Run-PurgeRefusal
    Assert-True ($exitCode -ne 0 -and (Test-Path -LiteralPath $marker) -and (Test-Path -LiteralPath $registry) -and (Test-Path -LiteralPath $uninstaller)) 'A locked-data purge discarded recovery metadata.'
    Assert-True ((File-Hash $exe) -ceq $nativeHash -and (File-Hash $cacheFixture) -ceq $cacheHash) 'A refused purge changed the locked fixture or native executable.'
} finally { $cacheHandle.Dispose() }
$checks.locked_user_data_purge_retry_safe = $true

$exitCode = Run-Bounded $uninstaller @('/S', '/PURGEUSERDATA') 120
Assert-True ($exitCode -eq 0) 'Explicit complete-uninstall launch failed.'
$deadline = [DateTime]::UtcNow.AddSeconds(90)
while (((Test-Path -LiteralPath $marker) -or (Test-Path -LiteralPath $registry) -or (Test-Path -LiteralPath $uninstaller)) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 500 }
foreach ($path in @($profileRoot, $roamingRoot, $marker, $uninstaller, $registry, $desktopLink, $programLink)) {
    Assert-True (-not (Test-Path -LiteralPath $path)) 'Explicit complete uninstall retained owned data, metadata or shortcuts.'
}
foreach ($file in $payload) {
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $installDir ([IO.Path]::GetRelativePath($Runtime, $file.FullName))))) 'Explicit complete uninstall retained a known application file.'
}
foreach ($suffix in $launcherSuffixes) { Assert-True ($null -eq (Get-Item -LiteralPath $launcherKey).GetValue($exe + $suffix, $null)) 'An owned launcher registry value remains.' }
foreach ($suffix in $skeletonSuffixes) { Assert-True ($null -eq (Get-Item -LiteralPath $skeletonKey).GetValue($exe + $suffix, $null)) 'An owned skeleton registry value remains.' }
Assert-True ((Get-Item -LiteralPath $launcherKey).GetValue($foreignName) -ceq 'foreign installation' -and (Get-Item -LiteralPath $launcherKey).GetValue('UnrelatedSentinel') -ceq 'shared value' -and (Get-Item -LiteralPath $skeletonKey).GetValue('UnrelatedSentinel') -ceq 'shared value') 'Complete uninstall removed shared or foreign registry values.'
Assert-True ((Get-Item -LiteralPath $firefoxKey).GetValue('Sentinel') -ceq 'Firefox data must remain') 'Complete uninstall changed another browser''s registry fixture.'
Assert-True ((File-Hash $unknown) -ceq $unknownHash -and (File-Hash $downloadFixture) -ceq $downloadHash -and (File-Hash $outsideFile) -ceq $outsideHash -and (Test-Path -LiteralPath $browserProfile)) 'Complete uninstall removed unknown files, downloads or an external custom profile.'
$checks.explicit_purge_owned_profile_cache_and_registry = $true
$checks.foreign_files_profiles_downloads_and_registry_retained = $true
Assert-True ((Association-Snapshot) -ceq $beforeAssociations) 'Selected default-browser/file-association state changed.'
$checks.association_state_unchanged = $true
$report = [ordered]@{
    schema_version = 1
    passed = $true
    installer_sha256 = $installerHash
    native_binary_sha256 = $nativeHash
    payload_files_verified = $payload.Count
    checks = $checks
    environment = 'ephemeral GitHub-hosted Windows runner'
    interactive_installer_gui_tested = $false
    normal_browsing_ime_tested = $false
    release_eligible = $false
}
New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($Output)) -Force | Out-Null
$json = $report | ConvertTo-Json -Depth 8
$stream = [IO.File]::Open($Output, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($json + [Environment]::NewLine)
    $stream.Write($bytes, 0, $bytes.Length)
} finally { $stream.Dispose() }
Write-Output $json
