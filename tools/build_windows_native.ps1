# Full source compilation on a disposable GitHub-hosted Windows runner only.
# No signing credentials, publication, Defender/ExecutionPolicy changes, or
# deletion of preinstalled software. Never run this on a personal computer.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $IsWindows -or $env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'This script is authorized only on an ephemeral GitHub-hosted Windows runner.'
}
$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Work = 'D:\pb'
if (Test-Path $Work) { throw 'Refusing to reuse, overwrite, or remove an existing D:\pb directory.' }
$Drive = Get-PSDrive D -ErrorAction Stop
if ($Drive.Free -lt 140GB) { throw 'At least 140 GiB free on D: is required for this full source build.' }
# The approved 4.2.1 release resets INSTDIR in .onInit, after NSIS parses /D.
# Use its unchanged official default; Gecko, objects, downloads and caches stay D:.
$MozillaBuild = 'C:\mozilla-build'
function Assert-FreshMozillaBuildPath {
    # Enumerating the parent catches existing/dangling reparse entries too.
    if (@(Get-ChildItem -LiteralPath 'C:\' -Force -Filter 'mozilla-build' -ErrorAction Stop).Count -ne 0) {
        throw 'Refusing to reuse or overwrite any existing C:\mozilla-build entry.'
    }
}
Assert-FreshMozillaBuildPath
$SystemDrive = Get-PSDrive C -ErrorAction Stop
if ($SystemDrive.Free -lt 8GB) { throw 'At least 8 GiB free on C: is required for the isolated MozillaBuild tool installation.' }
$Pin = Get-Content (Join-Path $Repo 'config\windows-native-toolchain.json') -Raw | ConvertFrom-Json
$Config = Get-Content (Join-Path $Repo 'config\esr.json') -Raw | ConvertFrom-Json
if (-not $Pin.license_accepted_for_ephemeral_github_windows_runner) { throw 'MozillaBuild license acceptance is required.' }
if ($Config.upstream_version -cne $Pin.reviewed_esr) { throw 'The changed ESR bootstrap must be reviewed before running it.' }
$Commit = (& git -C $Repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $Commit -notmatch '^[a-f0-9]{40}$' -or $Commit -cne $env:PB_EXPECTED_COMMIT) {
    throw 'Checkout does not match the exact successful quality-workflow revision.'
}
foreach ($Name in @('python', 'gpg', 'git')) { Get-Command $Name -ErrorAction Stop | Out-Null }
New-Item -ItemType Directory -Path $Work | Out-Null
foreach ($Name in @('downloads', 'evidence', 'tmp', 'mozbuild', 'pip-cache', 'cargo', 'cargo-target', 'sccache')) {
    New-Item -ItemType Directory -Path (Join-Path $Work $Name) | Out-Null
}
$Evidence = Join-Path $Work 'evidence'
$env:TEMP = Join-Path $Work 'tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $Work 'pip-cache'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:MOZBUILD_STATE_PATH = Join-Path $Work 'mozbuild'
$env:MACH_TELEMETRY_NO_SUBMIT = '1'
# Runtime tests need the Windows launcher to wait for its browser child.
# The source-archive build shell explicitly clears this Mozilla-CI-only flag.
$env:MOZ_AUTOMATION = '1'
$env:MOZ_CRASHREPORTER_NO_REPORT = '1'

function Assert-Hash([string] $Path, [string] $Expected) {
    $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -cne $Expected) { throw "SHA-256 mismatch: $Path" }
}
function Invoke-Python([string[]] $Arguments) {
    & python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python step failed: $($Arguments[0])" }
}

# The exact approved release is downloaded as data, then independently checked
# for immutable content, OS-verified Authenticode, and the expected signer.
$Installer = Join-Path $Work 'downloads\MozillaBuildSetup-4.2.1.exe'
$License = Join-Path $Evidence 'MozillaBuild-4.2.1-license.rtf'
Invoke-WebRequest -Uri $Pin.installer_url -OutFile $Installer
Assert-Hash $Installer $Pin.installer_sha256
if ((Get-Item $Installer).Length -ne $Pin.installer_bytes) { throw 'MozillaBuild size mismatch.' }
$Signature = Get-AuthenticodeSignature -LiteralPath $Installer
if ($Signature.Status -ne 'Valid' -or $null -eq $Signature.SignerCertificate) { throw 'MozillaBuild Authenticode is not valid.' }
$CertificateHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Signature.SignerCertificate.RawData)).ToLowerInvariant()
if ($CertificateHash -cne $Pin.signer_certificate_sha256 -or
    $Signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) -cne $Pin.signer_common_name) {
    throw 'MozillaBuild has an unexpected signing certificate.'
}
Invoke-WebRequest -Uri $Pin.license_url -OutFile $License
Assert-Hash $License $Pin.license_sha256
# Exact upstream source: https://hg.mozilla.org/mozilla-build/raw-file/36d6e180a593b46d45db555cd44b2f9f80ef25ca/installit.nsi
# No /D override: the vendor's release callback would silently replace it.
Assert-FreshMozillaBuildPath
$Process = $null
$InstallExitCode = $null
$InstallErrorType = $null
$InstallErrorHResult = $null
$InstallTimedOut = $false
$InstallTimer = [Diagnostics.Stopwatch]::StartNew()
try {
    $Process = Start-Process -FilePath $Installer -ArgumentList @('/S') -PassThru
    if (-not $Process.WaitForExit(600000)) {
        $InstallTimedOut = $true
        # Terminate only this installation's process tree on the disposable runner.
        if (-not $Process.HasExited) { $Process.Kill($true) }
    } else {
        $Process.Refresh()
        $InstallExitCode = $Process.ExitCode
    }
} catch {
    # Retain bounded error identifiers, never arbitrary environment/exception text.
    $InstallErrorType = $_.Exception.GetType().FullName
    $InstallErrorHResult = $_.Exception.HResult
}
$InstallTimer.Stop()
$InstallRootEntries = @(Get-ChildItem -LiteralPath 'C:\' -Force -Filter 'mozilla-build' -ErrorAction Stop)
$InstallRootSafe = $InstallRootEntries.Count -eq 1 -and $InstallRootEntries[0].PSIsContainer -and
    (($InstallRootEntries[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0)
$ExpectedFiles = [ordered]@{}
foreach ($Relative in @('start-shell.bat', 'msys2\msys2_shell.cmd', 'msys2\usr\bin\bash.exe')) {
    $ExpectedFiles[$Relative] = $InstallRootSafe -and (Test-Path -LiteralPath (Join-Path $MozillaBuild $Relative) -PathType Leaf)
}
$TopLevelNames = @()
if ($InstallRootSafe) {
    $TopLevelNames = @(Get-ChildItem -LiteralPath $MozillaBuild -Force -ErrorAction Stop |
        Select-Object -First 32 -ExpandProperty Name)
}
$InstallAttempt = [ordered]@{
    schema_version = 1; installer_sha256 = $Pin.installer_sha256; installed_path = $MozillaBuild
    arguments = @('/S'); destination_absent_before_launch = $true
    exit_code = $InstallExitCode; timed_out = $InstallTimedOut
    start_error_type = $InstallErrorType; start_error_hresult = $InstallErrorHResult
    elapsed_seconds = [math]::Round($InstallTimer.Elapsed.TotalSeconds, 2)
    root_present = $InstallRootEntries.Count -ne 0; root_is_safe_directory = $InstallRootSafe
    expected_files = $ExpectedFiles; top_level_names = $TopLevelNames; top_level_names_limit = 32
    free_c_gib_before = [math]::Round($SystemDrive.Free / 1GB, 2)
}
$InstallAttempt | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Evidence 'mozilla-build-install-attempt.json') -Encoding utf8NoBOM
Write-Output ($InstallAttempt | ConvertTo-Json -Depth 6 -Compress)
if ($InstallExitCode -ne 0 -or $InstallTimedOut -or $null -ne $InstallErrorType -or
    @($ExpectedFiles.Values | Where-Object { $_ -ne $true }).Count -ne 0) {
    throw 'MozillaBuild installation failed; inspect mozilla-build-install-attempt.json for exit status and bounded layout evidence.'
}
$ToolchainEvidence = [ordered]@{
    schema_version = 1; mozilla_build_version = $Pin.mozilla_build_version
    installer_sha256 = $Pin.installer_sha256; authenticode_status = [string]$Signature.Status
    signer_certificate_sha256 = $CertificateHash; license_sha256 = $Pin.license_sha256
    license_url = $Pin.license_url; installed = $true; installed_path = $MozillaBuild
    ephemeral_github_runner = $true; commit = $Commit; run_id = $env:GITHUB_RUN_ID
    runner_image_version = $env:ImageVersion; free_d_gib_before = [math]::Round($Drive.Free / 1GB, 2)
    free_c_gib_before = [math]::Round($SystemDrive.Free / 1GB, 2)
}
$ToolchainEvidence | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Evidence 'toolchain.json') -Encoding utf8NoBOM
Write-Output 'MozillaBuild 4.2.1 was verified and installed in the disposable GitHub Windows runner.'
if ($env:GITHUB_STEP_SUMMARY) {
    'MozillaBuild 4.2.1: approved release license, SHA-256 and valid pinned-signer Authenticode checked; installed only on this disposable hosted runner.' | Add-Content $env:GITHUB_STEP_SUMMARY
}

Invoke-Python @('-m', 'pip', 'install', '--only-binary=:all:', '--require-hashes',
    '-r', (Join-Path $Repo 'requirements-build.txt'))
$Downloads = Join-Path $Work 'downloads'
$Keyring = Join-Path $Downloads 'mozilla-release.gpg'
# Record the actual preinstalled implementation selected before public-key use.
# Do not log its default home, environment, or any key material.
$GpgPath = (Get-Command gpg -CommandType Application -ErrorAction Stop).Source
$GpgVersionOutput = @(& $GpgPath --no-options --batch --version 2>&1)
$GpgVersionExit = $LASTEXITCODE
$GpgVersion = @($GpgVersionOutput | Select-Object -First 2 |
    ForEach-Object { ([string]$_).Substring(0, [math]::Min(256, ([string]$_).Length)) })
[ordered]@{ schema_version = 1; executable = $GpgPath; version_lines = $GpgVersion; exit_code = $GpgVersionExit } |
    ConvertTo-Json -Depth 3 | Set-Content (Join-Path $Evidence 'gpg-toolchain.json') -Encoding utf8NoBOM
if ($GpgVersionExit -ne 0) { throw 'Preinstalled GPG version check failed; inspect gpg-toolchain.json.' }
Invoke-Python @((Join-Path $Repo 'tools\provision_upstream_key.py'), '--output', $Keyring)
$Trust = Get-Content (Join-Path $Repo 'config\update-trust.json') -Raw | ConvertFrom-Json
$Fetch = @((Join-Path $Repo 'tools\update_esr.py'), 'fetch', '--version', $Config.upstream_version,
    '--kind', 'source', '--destination', $Downloads, '--keyring', $Keyring)
foreach ($Fingerprint in $Trust.mozilla_release_key_fingerprints) { $Fetch += @('--fingerprint', $Fingerprint) }
foreach ($Fingerprint in $Trust.mozilla_release_signing_subkey_fingerprints) { $Fetch += @('--signing-fingerprint', $Fingerprint) }
Invoke-Python $Fetch
$Archive = Join-Path $Downloads "firefox-$($Config.upstream_version).source.tar.xz"
$Source = Join-Path $Work 'source'
Invoke-Python @((Join-Path $Repo 'tools\extract_source.py'), '--archive', $Archive,
    '--verification', "$Archive.verification.json", '--destination', $Source)
# Fail closed if the safety behavior of the pinned release bootstrap changes.
Assert-Hash (Join-Path $Source 'python\mozboot\mozboot\bootstrap.py') $Pin.reviewed_bootstrap_sha256
Assert-Hash (Join-Path $Source 'python\mozboot\mozboot\mach_commands.py') $Pin.reviewed_mach_bootstrap_sha256
Assert-Hash (Join-Path $Source 'taskcluster\kinds\toolchain\rust.yml') $Pin.reviewed_rust_definition_sha256
$env:PB_RUST_TASK = $Pin.reviewed_rust_task
$env:PB_RUST_VERSION = $Pin.reviewed_rust_version
Invoke-Python @((Join-Path $Repo 'tools\prepare_source.py'), '--source', $Source, '--target', 'windows')
Copy-Item "$Archive.verification.json" (Join-Path $Evidence 'source-verification.json')
Copy-Item (Join-Path $Source 'PRIVACY_BROWSER_SOURCE_PREPARATION.json') (Join-Path $Evidence 'source-preparation.json')
Copy-Item (Join-Path $Repo 'config\mozconfig.windows') (Join-Path $Evidence 'mozconfig.submitted')

# Fail early on NSIS syntax before spending hours compiling Gecko. Gecko's own
# installer is disabled; this separately verified portable compiler introduces
# no extra installer or system configuration. The production-shaped inert setup
# is never run. A second compile-time safety-only harness runs self-authored data
# solely under D:\pb\installer-syntax\execution. Neither setup is uploaded.
Invoke-Python @((Join-Path $Repo 'tools\windows_native_evidence.py'), '--work-root', $Work, '--prepare-nsis')
$Nsis = Get-Content (Join-Path $Evidence 'nsis-toolchain.json') -Raw | ConvertFrom-Json
$NativeCompiler = Join-Path $Work $Nsis.compiler
Assert-Hash $NativeCompiler $Nsis.compiler_sha256
$env:NSISDIR = Join-Path $Work $Nsis.nsis_directory
Invoke-Python @((Join-Path $Repo 'tools\check_native_installer_syntax.py'), '--compiler', $NativeCompiler, '--work-root', $Work, '--execute-fixtures')

$Mozconfig = Join-Path $Source 'mozconfig'
$MozconfigText = Get-Content $Mozconfig -Raw
if ($MozconfigText -match '--enable-artifact-builds|--disable-sandbox|--disable-webextensions') {
    throw 'Unsupported artifact-build or security-downgrade option in mozconfig.'
}
if ($MozconfigText -notmatch '(?m)^ac_add_options --enable-bootstrap\s*$') {
    "`nac_add_options --enable-bootstrap" | Add-Content $Mozconfig
}
"`nmk_add_options MOZ_MAKE_FLAGS=-j4`nmk_add_options MOZ_AUTOMATION_PACKAGE=0" | Add-Content $Mozconfig

$env:PB_WORK_UNIX = '/d/pb'
$RepoUnix = '/' + $Repo.Substring(0, 1).ToLowerInvariant() + $Repo.Substring(2).Replace('\', '/')
if ($RepoUnix -notmatch '^/[a-z]/[A-Za-z0-9_./-]+$') { throw 'Unexpected checkout path for shell invocation.' }
$env:USE_MINTTY = '0'
$env:CHERE_INVOKING = '1'
$Launch = Join-Path $Work 'run-mozilla-build.cmd'
@"
@echo off
call "$MozillaBuild\start-shell.bat" -c "bash '$RepoUnix/tools/build_windows_native.sh'"
exit /b %ERRORLEVEL%
"@ | Set-Content $Launch -Encoding ascii
& cmd.exe /d /c $Launch
$ShellExitCode = $LASTEXITCODE
[ordered]@{ schema_version = 1; phase = 'mozilla-build-shell'; exit_code = $ShellExitCode } |
    ConvertTo-Json | Set-Content (Join-Path $Evidence 'mozilla-build-shell-exit.json') -Encoding utf8
if ($ShellExitCode -ne 0) { throw "MozillaBuild shell failed with exit code $ShellExitCode; inspect phase exit records and logs." }

# First unpack mach's new ZIP and perform static checks only. Browser startup
# must wait until the installer test has created verified owned profile roots;
# its --smoke callback then runs before uninstall verification. No Mozilla
# release package is used.
Invoke-Python @((Join-Path $Repo 'tools\windows_native_evidence.py'), '--work-root', $Work)
# Recheck the exact compiler immediately before the real, validated packaging.
Assert-Hash $NativeCompiler $Nsis.compiler_sha256
$Runtime = Join-Path $Work 'candidate\PrivacyBrowser'
$BuildEvidence = Join-Path $Evidence 'build-evidence.json'
$Setup = Join-Path $Work 'candidate\PrivacyBrowser-0.1.0-dev-win64-setup-UNSIGNED.exe'
Invoke-Python @((Join-Path $Repo 'tools\package_windows_native.py'), '--runtime', $Runtime,
    '--evidence', $BuildEvidence, '--output', $Setup, '--compiler', $NativeCompiler)
& (Join-Path $Repo 'tools\test_native_installer.ps1') -Installer $Setup -Runtime $Runtime `
    -WorkRoot (Join-Path $Work 'installer-test') -Output (Join-Path $Evidence 'windows-installer-test.json')
Invoke-Python @((Join-Path $Repo 'tools\windows_native_evidence.py'), '--work-root', $Work, '--stage')
if ($env:GITHUB_STEP_SUMMARY) {
    'Unpublished unsigned candidate: full Gecko build, package, Places xpcshell smoke, native resource/static checks, --version, headless rendering and bounded per-user install/uninstall checks passed. Interactive Windows UI and broader security/privacy acceptance remain unverified. No release was published.' | Add-Content $env:GITHUB_STEP_SUMMARY
}
