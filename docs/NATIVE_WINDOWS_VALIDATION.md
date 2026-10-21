# Offline native Windows candidate validation

`tools/verify_windows_native.py` inspects an unpacked **source-built** Windows
candidate. It never loads or executes the browser, DLLs, or installer. It does
not modify the candidate and does not publish or sign anything.

This route is separate from `tools/package_windows.py`. That packager deliberately
requires `firefox.exe` and a `developer-runtime-overlay` manifest. Renaming that
binary or its installer is not an independently branded PrivacyBrowser build.

## Static checks

The candidate must contain `privacybrowser.exe`, the required Gecko runtime
files, and the integrated project overlay. A remaining `firefox.exe` anywhere in
the payload or a developer-overlay manifest is rejected.

The bounded, standard-library PE32+ parser checks AMD64 executable headers and
native resources directly from disk:

- Every VERSIONINFO string table must identify `PrivacyBrowser` in ProductName,
  FileDescription and InternalName; OriginalFilename must be `privacybrowser.exe`.
  CompanyName must identify PrivacyBrowser. Upstream legal notices may remain.
- Fixed numeric and textual file/product versions retain the pinned Gecko engine
  version. The project's `0.1.0` development version does not replace that version.
- Every RT_GROUP_ICON and referenced RT_ICON frame must match the supplied
  `browser/privacy/assets/privacybrowser.ico`, including exact image bytes.
  The parser accepts a resource compiler normalizing an ICO plane count of 0 to
  1. Missing, changed, extra or unreferenced icon frames are rejected. This is a
  byte-level check; Explorer presentation and icon-cache behavior are untested.
- `application.ini` must identify PrivacyBrowser as Name and Vendor, retain the
  compatible Gecko application ID and engine version, and use the independent
  remoting name when present. `platform.ini` must retain the engine milestone.
- All payload files have SHA-256 hashes. Case collisions, symlinks, Windows device
  names, traversal paths and unsafe Windows path characters are rejected.

A resource-only inert PE fixture can satisfy these static checks. That is
intentional: static identity validation does not prove that a program works.

## Retained source-build evidence

Keep evidence **outside** the candidate payload. The verifier expects the
following JSON shape. Hash placeholders are illustrative, not accepted values.

```json
{
  "schema_version": 1,
  "build_kind": "full-source-build",
  "source_compiled": true,
  "official_release": false,
  "target": "windows",
  "upstream_version": "153.5.0esr",
  "native_binary": "privacybrowser.exe",
  "files": {
    "privacybrowser.exe": "<SHA-256>",
    "application.ini": "<SHA-256>",
    "<every other payload path>": "<SHA-256>"
  },
  "commands": [
    {"argv": ["./mach", "build"], "exit_code": 0},
    {"argv": ["./mach", "package"], "exit_code": 0}
  ],
  "preparation": {"path": "preparation.json", "sha256": "<SHA-256>"},
  "source_verification": {"path": "source-verification.json", "sha256": "<SHA-256>"},
  "build_log": {"path": "build.log", "sha256": "<SHA-256>"},
  "package_log": {"path": "package.log", "sha256": "<SHA-256>"}
}
```

Use the current reviewed `config/esr.json` pin rather than copying an example
version. Only record successful commands actually run by the build host. The
`files` mapping must be an exact inventory, with POSIX-style relative paths;
`verify_windows_native.inventory(runtime)` returns the required mapping.

All four evidence paths must be distinct regular files underneath the evidence
JSON's parent, with no symlinks or `..` components. Logs must be nonempty. The
preparation artifact is the output of `prepare_source.py`, with `prepared:true`,
`compiled:false`, target `windows`, native binary `privacybrowser.exe`, app name
`PrivacyBrowser`, the upstream version and the reference icon's SHA-256. It
records preparation, not compilation.

The source-verification artifact is produced by `update_esr.py fetch --kind
source`. The verifier requires the expected archive/version, SHA-512, a positive
signature-verification result and a checksum signer in the configured Mozilla
pins. It does **not** reverify the upstream archive or its signature.

JSON, logs and hashes establish consistency of retained evidence. They do not
independently authenticate the build host, attest that compilation really ran,
or prove that a binary was built from the claimed source. The report therefore
always sets `compilation_independently_proven:false`. A reproducible build or
trusted build attestation would be a separate requirement.

## Run and interpret

```sh
python tools/verify_windows_native.py \
  --runtime candidate/privacybrowser \
  --evidence evidence/build-evidence.json \
  --output reports/native-static-checks.json
python -m unittest discover -s tests/unit -p native_package_test.py -v
```

The output path must be new and outside the runtime. The verifier refuses to
overwrite existing reports or input files. Failure exits nonzero without writing
a successful report.

A successful JSON report includes the native version strings, icon check,
complete payload hashes, evidence hashes and explicit limitations. It always
reports `official_release:false`, `release_eligible:false`, Windows execution and
installation/uninstallation untested, and Authenticode verification not performed.
Separate native-host runtime checks must have their own results. No real Windows
binary or source build is certified by the offline fixture tests.

The AutoConfig preference and dynamically registered chrome manifest must remain
loose files at their supported GRE-relative locations. The source preparation
exempts only these exact paths from the upstream omnijar/manifest transforms.
Static validation checks the bootstrap and manifest against the project source
and permits only LF/CRLF variation in the exact AutoConfig preference text. This
does not itself prove that AutoConfig ran.

This check does not replace `release_gate.py`, source review, signing, install and
uninstall testing, profile isolation, crash recovery, security tests or a reviewed
update service. Production publication remains blocked.

## Native installer wrapper

`validate_installer(path)` in the same module separately checks the project's
independent NSIS wrapper. Its fixed basename and OriginalFilename are
`PrivacyBrowser-0.1.0-dev-win64-setup-UNSIGNED.exe`. ProductName is
`PrivacyBrowser Setup`, FileDescription is `PrivacyBrowser Installer`,
InternalName is `PrivacyBrowser Setup`, and CompanyName is
`PrivacyBrowser contributors`. Installer FileVersion/ProductVersion strings are
`0.1.0-dev`, with numeric version `0.1.0.0`. These are wrapper versions; the
bundled browser must retain the Gecko engine version described above.

The fixed VERSIONINFO signature must always be `0xFEEF04BD`. The browser's
structure version remains exactly `0x00010000`. Only this pinned NSIS wrapper
requires structure version `0`: NSIS 3.07 zero-initializes `dwStrucVersion` and
exports it unchanged ([upstream implementation](https://github.com/nsis-dev/nsis/blob/f93a659006d720cbb6602371ade50935c9dd0e5e/Source/ResourceVersionInfo.cpp)).
This is a separate exact toolchain expectation, not a relaxed signature check.

NSIS uses an i386 PE32 launcher even for a Windows AMD64 payload. This architecture
is enabled only by this separate installer checker. Browser validation still
requires AMD64 PE32+. The same exact custom icon checks apply. PE section reads
are bounded and exclude the appended compressed NSIS archive; the reported
SHA-256 covers the entire file. `payload_extraction_verified:false` explicitly
records that compressed payload contents and the embedded uninstaller were not
inspected by this check. Static wrapper checks cannot replace retained build
inventory, actual installation/uninstallation, or payload validation.

## Hosted Windows execution / Windows 上の実行検証

`windows-native.yml` uses a successful main-branch quality run's exact commit.
`build_windows_native.ps1` verifies the approved MozillaBuild release, its license
bytes and Authenticode signature, then builds the pinned, verified Gecko source
with the independent branding. Neither the workflow nor its build job can write
repository contents or publish releases.

Before Gecko compilation, an isolated NSIS preflight compiles the installer
template with inert payload and applies the same static version, branding, icon
and architecture checks used for the real installer. A failed wrapper check
retains diagnostic text and stops before the expensive build. This generated
production-shaped fixture is never executed or uploaded. A separately isolated
synthetic executable exercises its shared file-safety routines against newly created
synthetic data inside the dedicated runner workspace. Production installation,
real profile paths, browser startup and publication are excluded from that
fixture. Its evidence is separate from the later genuine-browser installer and
runtime results; a fixture pass alone cannot qualify a candidate.

Read-only root checks run before build tooling, around native build/package, around the fast synthetic tests, and immediately before genuine
installation. Their bounded JSON records use fixed target labels and existence/
type facts, not user paths, profile contents, or registry values. Any existing
product root, link/reparse entry or inspection error still refuses the test;
the workflow never deletes or adopts an unknown root to make the test pass.

The genuine Places xpcshell test runs only after the real installer creates and
verifies previously absent owned data roots. The pinned upstream crash helper
uses the Windows profile known folder to create
`AppData/Roaming/PrivacyBrowser/PrivacyBrowser/Crash Reports`, ignoring Gecko
app-data overrides. This is inside the newly owned roaming root and remains
covered by complete-uninstall verification. A read-only known-folder comparison
also requires the profile-derived literal roaming path to match the actual shell
roaming folder, refusing redirected/reparse layouts instead of writing outside
the owned root. No crash reporting, sandbox feature,
or Windows known-folder setting is disabled or redirected. The earlier generic
refusal did not retain direct filesystem proof of the creator; the pinned source
provides the causal path, and new phase records distinguish earlier side effects.

Build/package evidence remains immutable before installer compilation. Places
has a separate mandatory raw log/exit record and report bound to the exact build
manifest, source commit/run and native binary. Runtime smoke, artifact staging
and prerelease validation all require it. Missing, failed or mismatched Places
evidence cannot be replaced with a successful build/package assertion.

Fresh application-directory creation is atomic: the installer refuses an
existing object, including an empty directory, rather than interpreting NSIS's
`directory\*.*` existence test as an emptiness check. The isolated synthetic
preflight exercises the same create-and-pin helper and preserves colliding
fixtures unchanged. Installer test commands retain bounded, exclusive phase,
exit/start-error and timeout records before success assertions; these records
contain no command paths, profile contents or registry values. They do not
claim to identify an internal NSIS branch by themselves.

The current shortcut-diagnostic revision deliberately pauses the expensive
full-build job after quality and synthetic validation. It exercises inert
ASCII/Unicode shortcut targets entirely inside the isolated fixture root,
without executing links or targets or changing Desktop/Start Menu entries.
Bounded readback evidence distinguishes explicit wide ShellLink retrieval from
WScript retrieval, and direct links from copied ownership snapshots. The
[ASCII-path control run](https://github.com/crevice0308/PrivacyBrowser/actions/runs/39694415558)
showed WScript replacing Japanese target/argument/icon text with question marks
while explicit `IShellLinkW` recovered the exact fields from unchanged bytes.
Acceptance therefore uses the explicit Unicode Shell API consistently, not a
fallback after WScript failure. WScript observations remain diagnostic only;
successful HRESULTs, exact target/profile/icon/working-directory semantics and
unchanged snapshot/copy bytes remain mandatory. This controlled result is not
proof that the previous actual installed links were correct: a fresh full
installer acceptance run and its retained link evidence are still required.
The full-build pause must be removed in a separately reviewed source change
after the diagnostic evidence is inspected; it does not authorize publication.

`test_native_installer.ps1` is restricted to a fresh directory on an ephemeral
GitHub-hosted runner. It checks all installed payload hashes, native metadata,
per-user shortcuts and uninstall registration. A locked executable and a real
running browser using a separate profile must both prevent uninstall. A later
ordinary silent uninstall must remove its exact payload and metadata while
retaining user data. A second same-path install and explicit `/PURGEUSERDATA`
uninstall must remove owned profile/cache roots and exact per-binary registry
values while preserving external custom profiles, downloads, unknown app files
and foreign/shared registry sentinels. Corrupt ownership markers, junctions and
locked data must fail safely; a later retry must recover. Selected default-browser and file
association values are compared without publishing their contents.

Cleanup is not an atomic transaction. Once explicit deletion has begun, a later
error can leave an already partially removed owned tree, with recovery markers
retained for retry. Existing profile locks are held during cleanup, but another
binary creating a new profile after preflight is not an exhaustively tested
concurrent-writer case. No crash-recovery or universal concurrency guarantee is
claimed.

These checks are configured tests, not a claim that a Windows run has passed.
After installer ownership checks, an additional bounded localhost-only Marionette
probe uses a new disposable profile to observe automatic overlay startup. It must
not call the overlay's startup function or override privacy settings. Automation
recommended preferences are disabled for this test profile so they cannot mask
the product's defaults. The probe checks already-loaded modules, effective
privacy settings and controller readiness, content-sandbox protection, and the
privileged home module's actual readiness. Its retained observations and hashes
are revalidated before staging and prerelease publication. This narrow startup
check does not establish complete privacy/security acceptance or signing.
Interactive installer pages, Explorer icon-cache presentation, Japanese IME,
normal browsing, memory targets and complete privacy/security acceptance need
separate evidence. Existing supplied `tests/results/` files do not establish
results for this revision. The production release gate remains closed.

Windows の実行検証は一時的な GitHub runner 内に限定します。インストールされた
全ファイルのハッシュ、アプリ表示情報、ショートカット、実行中のアンインストール
拒否、通常のサイレント削除でのデータ保持、明示的な完全削除での所有データ削除、
他ブラウザー・共有値・対象外ダウンロード・未知ファイルの保持を確認します。これは検証手順の説明であり、成功済みという意味ではありません。
GUI 操作、日本語 IME、通常閲覧、メモリ目標、完全な履歴非永続化は別途確認が必要です。

Complete cleanup permanently deletes eligible profile data only after GUI
confirmation or `/S /PURGEUSERDATA`. It cannot recover passwords or bookmarks
once deleted. Ownership is restricted to the two default app-data roots with
matching UTF-16 markers; environment overrides and arbitrary `--profile` paths
are never followed. No claim of erasing OS logs, filesystem remnants, backups
or service-side records is made.
