"""Offline orchestration tests: inert ZIPs and mocks, no Windows code is run."""
from pathlib import Path
import io
import inspect
import json
import os
import shutil
import subprocess
import re
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import windows_native_evidence as workflow


class NativeWorkflowTests(unittest.TestCase):
    def test_actual_roots_stay_guarded_and_places_waits_for_installer_ownership(self):
        shell = (ROOT / "tools/build_windows_native.sh").read_text()
        powershell = (ROOT / "tools/build_windows_native.ps1").read_text()
        installer = (ROOT / "tools/test_native_installer.ps1").read_text()
        import windows_autoconfig_smoke
        for name in ("MOZ_APP_DATA", "MOZ_LOCAL_APP_DATA"):
            self.assertNotIn("export " + name + "=", shell)
            self.assertNotIn("$env:" + name + " =", powershell + installer)
            self.assertIn(name, windows_autoconfig_smoke.FORBIDDEN_ENV)
        self.assertLess(powershell.index("'--phase', 'initial'"), powershell.index("Invoke-WebRequest"))
        for phase, command in (("before-build", "build"), ("before-package", "package")):
            self.assertLess(shell.index("--phase " + phase), shell.index("run_logged " + command + " ./mach"))
        self.assertLess(shell.index("run_logged package ./mach"), shell.index("--phase after-package"))
        self.assertIn("'windows_test_roots.py') --phase 'before-installer'", installer)
        self.assertIn("A pre-existing PrivacyBrowser profile, shortcut or uninstall entry prevents this isolated test.", installer)
        self.assertLess(installer.index("--phase 'before-installer'"), installer.index("$exitCode = Run-Bounded $Installer"))
        places = installer.index("$placesExit = Run-Bounded")
        for check in ("unicode_owned_root_markers", "exact_payload_hashes", "native_shortcuts", "per_user_registration"):
            self.assertLess(installer.index("$checks." + check + " = $true"), places)
        self.assertLess(places, installer.index("'windows_places_smoke.py')"))
        self.assertLess(installer.index("$checks.places_xpcshell_completed = $true"), installer.index("'windows_native_evidence.py')"))
        self.assertIn("'D:\\pb\\run-places-smoke.cmd') 600", installer)
        self.assertIn("--places-smoke", powershell)
        branch = shell[shell.index('case "${1:-}" in'):shell.index("# Preserve the exact")]
        self.assertIn("run_logged xpcshell ./mach xpcshell-test", branch)
        self.assertIn("exit 0", branch)
        self.assertNotIn("run_logged xpcshell", shell[shell.index("# Preserve the exact"):])
        self.assertNotIn('"xpcshell-test"', shell[shell.index("# Build/package proof"):])

    def test_places_evidence_is_required_by_runtime_stage_and_publication(self):
        import windows_prerelease
        for function in (workflow.smoke_candidate, workflow.stage, windows_prerelease.validate_payload):
            source = inspect.getsource(function)
            self.assertIn("windows_places_smoke.validate_retained", source)
        self.assertLess(inspect.getsource(workflow.smoke_candidate).index("windows_places_smoke.validate_retained"),
                        inspect.getsource(workflow.smoke_candidate).index("results = [run_smoke"))
        self.assertLess(inspect.getsource(workflow.stage).index("windows_places_smoke.validate_retained"),
                        inspect.getsource(workflow.stage).index("output.mkdir()"))

    @unittest.skipUnless(sys.platform == "linux" and shutil.which("bash"), "Inert POSIX shell-mode exercise")
    def test_places_shell_mode_runs_only_one_inert_test_and_never_overwrites(self):
        script = ROOT / "tools/build_windows_native.sh"
        for code, existing in ((0, False), (7, False), (0, True)):
            with self.subTest(code=code, existing=existing):
                work = self.root / f"inert-{code}-{existing}"
                for name in ("source", "evidence", "bin", "tmp"):
                    (work / name).mkdir(parents=True)
                converter = work / "bin/cygpath"
                converter.write_text('#!/bin/sh\n[ "$1" = -m ] || exit 91\nprintf "%s\\n" "$2"\n')
                converter.chmod(0o700)
                mach = work / "source/mach"
                mach.write_text('#!/bin/sh\n[ "$1" = xpcshell-test ] || exit 92\n'
                                '[ "$2" = toolkit/components/places/tests/unit/test_isURIVisited.js ] || exit 93\n'
                                'printf "INERT self-authored shell fixture\\n"\n'
                                'printf "called" > ../called\n' + f'exit {code}\n')
                mach.chmod(0o700)
                log = work / "evidence/xpcshell.log"
                if existing:
                    log.write_bytes(b"retain existing inert evidence")
                environment = dict(os.environ, PB_WORK_UNIX=str(work),
                                   PATH=str(work / "bin") + os.pathsep + os.environ.get("PATH", ""))
                environment.pop("PB_RUST_TASK", None)
                environment.pop("PB_RUST_VERSION", None)
                result = subprocess.run([shutil.which("bash"), str(script), "--places-smoke"],
                                        env=environment, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 2 if existing else code, result.stderr.decode())
                self.assertFalse((work / "evidence/commands.json").exists())
                self.assertFalse((work / "evidence/mozconfig.before-bootstrap").exists())
                if existing:
                    self.assertFalse((work / "called").exists())
                    self.assertEqual(log.read_bytes(), b"retain existing inert evidence")
                else:
                    self.assertTrue((work / "called").exists())
                    self.assertEqual(json.loads((work / "evidence/xpcshell-exit.json").read_text()),
                                     {"schema_version": 1, "phase": "xpcshell",
                                      "command_exit_code": code, "tee_exit_code": 0})

    def test_quality_pip_command_uses_yaml_block_scalar(self):
        # A plain YAML scalar cannot contain the colon-space in :all: followed
        # by another option. Keep the hash-pinned command in a literal block.
        quality = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")
        self.assertIn("run: |\n          python -m pip install --disable-pip-version-check "
                      "--only-binary=:all: --require-hashes -r requirements-build.txt", quality)
        self.assertNotIn("run: python -m pip install", quality)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def archive(self, entries):
        archive = self.root / "fixture.zip"
        with zipfile.ZipFile(archive, "x") as stream:
            for name, contents in entries:
                stream.writestr(name, contents)
        return archive

    def test_safe_archive_extracts_files(self):
        archive = self.archive([("PrivacyBrowser/", b""), ("PrivacyBrowser/notice.txt", b"inert")])
        destination = self.root / "unpacked"
        workflow.safe_extract_zip(archive, destination)
        self.assertEqual((destination / "PrivacyBrowser/notice.txt").read_bytes(), b"inert")

    def test_rejects_unsafe_names_before_any_output(self):
        for index, name in enumerate(("../escape", "/absolute", "C:/drive", "foo\\bar", "file.", "AUX.txt", "NUL", "x/../y")):
            with self.subTest(name=name):
                archive = self.root / ("bad" + str(index) + ".zip")
                with zipfile.ZipFile(archive, "x") as stream:
                    stream.writestr("first.txt", b"safe")
                    member = zipfile.ZipInfo(name)
                    # ZipInfo sanitizes os.sep during construction on Windows.
                    # Restore the exact hostile name in the bytes being written.
                    member.filename = name
                    stream.writestr(member, b"bad")
                with zipfile.ZipFile(archive) as stream:
                    # orig_filename is the untouched raw-name list on every
                    # host; namelist() itself normalizes Windows backslashes.
                    self.assertEqual([item.orig_filename for item in stream.infolist()], ["first.txt", name])
                destination = self.root / ("unpacked" + str(index))
                with self.assertRaises(ValueError):
                    workflow.safe_extract_zip(archive, destination)
                self.assertFalse(destination.exists())

    def test_rejects_windows_separator_normalization_before_any_output(self):
        member = zipfile.ZipInfo("foo\\bar")
        member.filename = "foo\\bar"
        archive = self.archive([("first.txt", b"safe"), (member, b"bad")])
        # Exercise the Windows ZipInfo reader on every host without executing
        # Windows code or weakening the extractor's production validation.
        with mock.patch.object(zipfile.os, "sep", "\\"), mock.patch.object(zipfile.os, "altsep", "/"):
            with zipfile.ZipFile(archive) as stream:
                self.assertEqual(stream.namelist(), ["first.txt", "foo/bar"])
                self.assertEqual([item.orig_filename for item in stream.infolist()], ["first.txt", "foo\\bar"])
            destination = self.root / "unpacked"
            with self.assertRaisesRegex(ValueError, "normalized or truncated"):
                workflow.safe_extract_zip(archive, destination)
            self.assertFalse(destination.exists())

    def test_rejects_case_collisions(self):
        archive = self.archive([("FILE", b"1"), ("file", b"2")])
        with self.assertRaises(ValueError):
            workflow.safe_extract_zip(archive, self.root / "unpacked")

    def test_rejects_symlink(self):
        member = zipfile.ZipInfo("link")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive = self.archive([(member, b"../outside")])
        with self.assertRaises(ValueError):
            workflow.safe_extract_zip(archive, self.root / "unpacked")

    def test_rejects_file_as_parent_in_both_orders(self):
        for entries in ([('parent', b'x'), ('parent/child', b'y')],
                        [('parent/child', b'y'), ('parent', b'x')]):
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as temporary:
                archive = Path(temporary) / "fixture.zip"
                with zipfile.ZipFile(archive, "x") as stream:
                    for name, contents in entries:
                        stream.writestr(name, contents)
                with self.assertRaises(ValueError):
                    workflow.safe_extract_zip(archive, Path(temporary) / "unpacked")

    def test_rejects_preexisting_destination(self):
        archive = self.archive([("file", b"safe")])
        destination = self.root / "unpacked"
        destination.mkdir()
        with self.assertRaises(ValueError):
            workflow.safe_extract_zip(archive, destination)

    def test_rejects_oversized_archive(self):
        archive = self.archive([("file", b"too big")])
        with self.assertRaises(ValueError):
            workflow.safe_extract_zip(archive, self.root / "unpacked", max_bytes=3)

    def test_json_never_overwrites(self):
        target = self.root / "evidence.json"
        workflow.write_json(target, {"test": True})
        with self.assertRaises(FileExistsError):
            workflow.write_json(target, {"test": False})
        self.assertTrue(json.loads(target.read_text())["test"])

    def test_nsis_definition_drift_blocks_before_network(self):
        source = self.root / "source/taskcluster/kinds/fetch/toolchains.yml"
        source.parent.mkdir(parents=True)
        source.write_text("nsis-3.07-win:\n    fetch:\n        url: https://example.invalid/bad.zip\n")
        with mock.patch.object(workflow, "download_nsis") as download:
            with self.assertRaises(ValueError):
                workflow.prepare_nsis(self.root)
            download.assert_not_called()

    def test_nsis_fetch_accepts_only_expected_final_host(self):
        response = io.BytesIO(b"inert data")
        response.url = "https://example.invalid/not-mozilla.zip"
        with mock.patch.object(workflow.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(ValueError):
                workflow.download_nsis(self.root / "bad.zip")

    def test_nsis_fetch_is_bounded(self):
        response = io.BytesIO(b"oversized")
        response.url = workflow.NSIS_URL
        with mock.patch.object(workflow.urllib.request, "build_opener") as opener, \
             mock.patch.object(workflow, "NSIS_BYTES", 2):
            opener.return_value.open.return_value = response
            with self.assertRaises(ValueError):
                workflow.download_nsis(self.root / "big.zip")

    def test_nsis_fetch_rejects_unapproved_redirects(self):
        for url in ("http://raw.githubusercontent.com/mozilla-releng/adhoc-signing-blobs/nsis/nsis-3.07-signed.zip",
                    "https://github.com/other-owner/other-repo/raw/nsis/nsis-3.07-signed.zip",
                    workflow.NSIS_URL + "?different=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                workflow.trusted_nsis_url(url)

    def test_workflow_retains_exact_revision_and_success_gates(self):
        source = (ROOT / ".github/workflows/windows-native.yml").read_text()
        self.assertIn("ref: ${{ github.event.workflow_run.head_sha }}", source)
        self.assertIn("persist-credentials: false", source)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", source)
        self.assertIn("head_repository.full_name == github.repository", source)
        self.assertIn("contents: read", source)
        self.assertIn("if: success()", source)
        self.assertNotIn("contents: write", source)

    def test_bootstrap_has_no_system_changes_and_no_artifact_mode(self):
        source = (ROOT / "tools/build_windows_native.sh").read_text()
        import run_mach_bootstrap
        self.assertEqual(run_mach_bootstrap.BOOTSTRAP_ARGS,
                         ["--no-interactive", "bootstrap", "--application-choice", "browser",
                          "--no-system-changes"])
        self.assertIn('run_logged bootstrap python3 "$script_dir/run_mach_bootstrap.py"', source)
        self.assertIn("./mach build", source)
        self.assertIn("./mach package", source)
        self.assertNotIn("--enable-artifact-builds", source)
        self.assertNotIn("Set-MpPreference", source)
        self.assertNotIn("Add-MpPreference", source)

    def test_hosted_memory_policy_preserves_release_security_and_tests(self):
        config = (ROOT / "config/mozconfig.windows").read_text()
        powershell = (ROOT / "tools/build_windows_native.ps1").read_text()
        shell = (ROOT / "tools/build_windows_native.sh").read_text()
        self.assertEqual(re.findall(r"MOZ_MAKE_FLAGS=([^`\s]+)", powershell), ["-j2"])
        for option in ("--disable-debug-symbols", "--enable-optimize", "--disable-debug",
                       "--enable-warnings-as-errors"):
            self.assertIn("ac_add_options " + option + "\n", config)
        for forbidden in ("--disable-release", "--disable-hardening", "--disable-tests",
                          "--disable-sandbox", "--disable-lto", "RUSTFLAGS=", "CARGO_RUSTCFLAGS="):
            self.assertNotIn(forbidden, config + shell)
        self.assertIn('run_logged winchecksec-hash sha256sum', shell)
        self.assertIn('run_logged xpcshell ./mach xpcshell-test', shell)
        self.assertIn("make_jobs = 2; debug_symbols = $false", powershell)
        self.assertIn("pagefile_settings_changed = $false", powershell)
        self.assertIn("'build-resource-policy.json'", powershell)

    def test_memory_evidence_is_bounded_read_only_and_not_peak_measurement(self):
        powershell = (ROOT / "tools/build_windows_native.ps1").read_text()
        start = powershell.index("function Write-MemorySnapshot(")
        end = powershell.index("Write-MemorySnapshot 'initial'", start)
        snapshot = powershell[start:end]
        self.assertIn("Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop", snapshot)
        for field in ("total_physical_kib", "free_physical_kib", "total_virtual_kib", "free_virtual_kib"):
            self.assertIn("$Snapshot." + field, snapshot)
        self.assertIn("instantaneous, not peak usage", snapshot)
        self.assertIn("$Snapshot.error_type = $_.Exception.GetType().FullName", snapshot)
        for forbidden in ("Set-CimInstance", "Invoke-CimMethod", "Set-ItemProperty", "Win32_PageFileSetting"):
            self.assertNotIn(forbidden, powershell)
        launch = powershell.index("& cmd.exe /d /c $Launch")
        capture = powershell.index("$ShellExitCode = $LASTEXITCODE", launch)
        after = powershell.index("Write-MemorySnapshot 'after-shell'", capture)
        self.assertLess(launch, capture)
        self.assertLess(capture, after)

    def test_phase_notices_preserve_original_exit_codes(self):
        shell = (ROOT / "tools/build_windows_native.sh").read_text()
        self.assertIn("::notice title=PrivacyBrowser phase::Started %s", shell)
        self.assertIn("::notice title=PrivacyBrowser phase::Ended %s; command=%s; tee=%s", shell)
        self.assertIn('statuses=("${PIPESTATUS[@]}")', shell)
        self.assertIn('if (( statuses[0] != 0 )); then return "${statuses[0]}"; fi', shell)
        self.assertIn('return "${statuses[1]}"', shell)

    def test_shell_launch_exit_is_recorded_before_failure(self):
        source = (ROOT / "tools/build_windows_native.ps1").read_text()
        launch = source.index("& cmd.exe /d /c $Launch")
        capture = source.index("$ShellExitCode = $LASTEXITCODE", launch)
        evidence = source.index("'mozilla-build-shell-exit.json'", capture)
        failure = source.index("if ($ShellExitCode -ne 0)", evidence)
        self.assertLess(launch, capture)
        self.assertLess(capture, evidence)
        self.assertLess(evidence, failure)

    def test_source_archive_build_clears_mozilla_ci_flags_only_in_child(self):
        shell = (ROOT / "tools/build_windows_native.sh").read_text()
        powershell = (ROOT / "tools/build_windows_native.ps1").read_text()
        self.assertIn("unset MOZ_AUTOMATION MOZ_FETCHES_DIR", shell)
        self.assertNotIn("export MOZ_AUTOMATION=", shell)
        self.assertNotIn("export MOZ_FETCHES_DIR=", shell)
        self.assertIn("$env:MOZ_AUTOMATION = '1'", powershell)
        self.assertIn('export MOZBUILD_STATE_PATH="$work_windows/mozbuild"', shell)
        self.assertIn("ac_add_options --enable-bootstrap", powershell)
        self.assertLess(shell.index("unset MOZ_AUTOMATION MOZ_FETCHES_DIR"),
                        shell.index("run_logged rust-toolchain"))
        self.assertNotIn("git init", shell + powershell)
        self.assertNotIn("hg init", shell + powershell)
        self.assertIn('run_logged winchecksec-hash sha256sum "$PB_WORK_UNIX/mozbuild/winchecksec/winchecksec.exe"', shell)
        self.assertIn('export WINCHECKSEC="$work_windows/mozbuild/winchecksec/winchecksec.exe"', shell)
        self.assertLess(shell.index("run_logged winchecksec-hash"), shell.index("run_logged build ./mach"))
        self.assertIn('"source_indexed_pdbs_claimed":false', shell)

    def test_rust_uses_exact_source_defined_download_not_hosted_path(self):
        shell = (ROOT / "tools/build_windows_native.sh").read_text()
        powershell = (ROOT / "tools/build_windows_native.ps1").read_text()
        pin = json.loads((ROOT / "config/windows-native-toolchain.json").read_text())
        self.assertEqual(pin["reviewed_rust_task"], "win64-rust-1.94")
        self.assertEqual(pin["reviewed_rust_version"], "1.94.1")
        self.assertEqual(pin["reviewed_rust_definition_sha256"],
                         "ee1a8a3cb24c9784b1f6764528100279c682a6aa326bbb983a2a81085a3bdd39")
        self.assertIn("Assert-Hash (Join-Path $Source 'taskcluster\\kinds\\toolchain\\rust.yml') "
                      "$Pin.reviewed_rust_definition_sha256", powershell)
        self.assertIn("$env:PB_RUST_TASK = $Pin.reviewed_rust_task", powershell)
        self.assertIn("$env:PB_RUST_VERSION = $Pin.reviewed_rust_version", powershell)
        self.assertIn('mkdir "$PB_WORK_UNIX/rust-toolchain" &&', shell)
        self.assertIn('--from-build "$PB_RUST_TASK"', shell)
        self.assertIn('--artifact-manifest "$work_windows/evidence/rust-toolchain-artifacts.json"', shell)
        self.assertIn('run_logged rust-integrity python3 "$script_dir/verify_rust_toolchain.py"', shell)
        self.assertLess(shell.index("run_logged rust-integrity"), shell.index("run_logged rustc-version"))
        self.assertLess(shell.index("run_logged rust-integrity"), shell.index("run_logged cargo-version"))
        self.assertIn('export RUSTC="$(cygpath -m "$rust_bin/rustc.exe")"', shell)
        self.assertIn('export CARGO="$(cygpath -m "$rust_bin/cargo.exe")"', shell)
        for phase in ("rust-binary-hashes", "rustc-version", "cargo-version", "rust-version-check", "rust-host-check"):
            self.assertLess(shell.index("run_logged " + phase), shell.index("run_logged bootstrap python3"))
        self.assertIn("host: x86_64-pc-windows-msvc", shell)
        self.assertNotIn("rustup update", shell)

    def test_mozillabuild_uses_fresh_vendor_default_and_retains_attempt_evidence(self):
        source = (ROOT / "tools/build_windows_native.ps1").read_text(encoding="utf-8")
        self.assertIn("$MozillaBuild = 'C:\\mozilla-build'", source)
        self.assertIn("$Work = 'D:\\pb'", source)
        self.assertIn("$SystemDrive.Free -lt 8GB", source)
        self.assertIn("Get-ChildItem -LiteralPath 'C:\\' -Force -Filter 'mozilla-build'", source)
        self.assertEqual(source.count("\nAssert-FreshMozillaBuildPath\n"), 2)
        launch = source.index("$Process = Start-Process -FilePath $Installer")
        self.assertLess(source.rindex("\nAssert-FreshMozillaBuildPath\n"), launch)
        self.assertIn("-ArgumentList @('/S') -PassThru", source)
        self.assertNotIn('"/D=$MozillaBuild"', source)
        self.assertIn("$Process.WaitForExit(600000)", source)
        self.assertIn("$Process.Refresh()", source)
        for field in ("exit_code = $InstallExitCode", "expected_files = $ExpectedFiles",
                      "top_level_names = $TopLevelNames", "root_is_safe_directory = $InstallRootSafe"):
            self.assertIn(field, source)
        self.assertIn("Select-Object -First 32 -ExpandProperty Name", source)
        evidence = source.index("'mozilla-build-install-attempt.json'")
        failure = source.index("throw 'MozillaBuild installation failed;")
        self.assertLess(evidence, failure)
        self.assertLess(source.index("Get-AuthenticodeSignature"), launch)
        self.assertLess(source.index("Assert-Hash $License $Pin.license_sha256"), launch)

    def test_gpg_identity_diagnostics_drain_output_before_truncating(self):
        source = (ROOT / "tools/build_windows_native.ps1").read_text(encoding="utf-8")
        capture = source.index("$GpgVersionOutput = @(& $GpgPath --no-options --batch --version 2>&1)")
        status = source.index("$GpgVersionExit = $LASTEXITCODE")
        truncate = source.index("$GpgVersionOutput | Select-Object -First 2")
        evidence = source.index("'gpg-toolchain.json'")
        provision = source.index("'tools\\provision_upstream_key.py')")
        self.assertLess(capture, status)
        self.assertLess(status, truncate)
        self.assertLess(truncate, evidence)
        self.assertLess(evidence, provision)
        self.assertIn("[math]::Min(256", source)

    def test_candidate_preparation_never_starts_native_program(self):
        evidence = self.root / "evidence"
        evidence.mkdir()
        commands = [["./mach", "build"], ["./mach", "package"]]
        workflow.write_json(evidence / "commands.json", {
            "commands": [{"argv": argv, "exit_code": 0} for argv in commands]})
        for name in ("source-preparation.json", "source-verification.json", "build.log", "package.log"):
            (evidence / name).write_text("inert test evidence")
        dist = self.root / "source/obj-privacybrowser/dist"
        dist.mkdir(parents=True)
        with zipfile.ZipFile(dist / "native.zip", "x") as package:
            package.writestr("privacybrowser/privacybrowser.exe", b"inert fixture, not executable")
        nsis_notice = self.root / "tools/nsis/nsis-3.07/COPYING"
        nsis_notice.parent.mkdir(parents=True)
        nsis_notice.write_bytes(b"inert complete component-license fixture\r\nsecond line\r\n")
        environment = {"PB_EXPECTED_COMMIT": "a" * 40, "GITHUB_REPOSITORY": "test/repo", "GITHUB_RUN_ID": "123"}
        with mock.patch.dict(workflow.os.environ, environment), \
             mock.patch.object(workflow.native, "validate_candidate", return_value={"static_candidate_checks_passed": True}) as validate, \
             mock.patch.object(workflow, "run_smoke") as smoke:
            workflow.prepare_candidate(self.root)
            validate.assert_called_once()
            smoke.assert_not_called()
        self.assertTrue((evidence / "build-evidence.json").is_file())
        self.assertFalse((evidence / "xpcshell.log").exists())
        self.assertNotIn("xpcshell_log", json.loads((evidence / "build-evidence.json").read_text()))
        self.assertTrue((evidence / "native-static-checks.json").is_file())
        self.assertFalse((evidence / "windows-runtime-smoke.json").exists())
        runtime = self.root / "candidate/PrivacyBrowser"
        self.assertEqual((runtime / "NSIS-COPYING.txt").read_bytes(), nsis_notice.read_bytes())
        self.assertEqual((runtime / "NOTICE").read_bytes(), (ROOT / "NOTICE").read_bytes())
        proof = json.loads((evidence / "build-evidence.json").read_text())
        self.assertEqual(proof["files"]["NSIS-COPYING.txt"], workflow.native.digest(nsis_notice))

    def test_nsis_notice_existing_destination_is_never_overwritten(self):
        source = self.root / "tools/nsis/nsis-3.07/COPYING"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"component notice fixture")
        runtime = self.root / "runtime"
        runtime.mkdir()
        destination = runtime / "NSIS-COPYING.txt"
        destination.write_bytes(b"existing notice")
        with self.assertRaises(ValueError):
            workflow.stage_nsis_notice(self.root, runtime)
        self.assertEqual(destination.read_bytes(), b"existing notice")

    def test_nsis_notice_missing_or_empty_source_is_rejected(self):
        runtime = self.root / "runtime"
        runtime.mkdir()
        with self.assertRaises((ValueError, OSError)):
            workflow.stage_nsis_notice(self.root, runtime)
        source = self.root / "tools/nsis/nsis-3.07/COPYING"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"")
        with self.assertRaises(ValueError):
            workflow.stage_nsis_notice(self.root, runtime)
        self.assertFalse((runtime / "NSIS-COPYING.txt").exists())

    def test_smoke_requires_existing_static_preparation(self):
        with mock.patch.object(workflow, "run_smoke") as smoke:
            with self.assertRaises(OSError):
                workflow.smoke_candidate(self.root)
            smoke.assert_not_called()

    def test_build_orchestration_defers_smoke_to_installer_test(self):
        source = (ROOT / "tools/build_windows_native.ps1").read_text()
        preparation = source.index("'tools\\windows_native_evidence.py'), '--work-root', $Work)")
        hardening = source.index("'tools\\windows_pe_hardening.py')")
        package = source.index("'tools\\package_windows_native.py')")
        installation = source.index("'tools\\test_native_installer.ps1')")
        stage = source.index("'--stage')")
        self.assertLess(preparation, hardening)
        self.assertLess(hardening, package)
        self.assertLess(package, installation)
        self.assertLess(installation, stage)
        self.assertNotIn("'--smoke'", source)
        self.assertIn('"windows-runtime-smoke.json"', inspect.getsource(workflow.stage))
        self.assertIn('"windows-installer-test.json"', inspect.getsource(workflow.stage))

    def test_automatic_startup_probe_is_required_before_staging(self):
        smoke = inspect.getsource(workflow.smoke_candidate)
        invocation = "windows_autoconfig_smoke.run(runtime, work / \"windows-autoconfig-profile\", evidence)"
        validation = "windows_autoconfig_smoke.validate_retained(evidence, runtime)"
        self.assertIn(invocation, smoke)
        self.assertLess(smoke.index("native.validate_candidate"), smoke.index(invocation))
        self.assertLess(smoke.index(invocation), smoke.index(validation))
        self.assertLess(smoke.index(validation), smoke.index('write_json(evidence / "windows-runtime-smoke.json"'))
        stage = inspect.getsource(workflow.stage)
        self.assertLess(stage.index(validation), stage.index("output.mkdir()"))

    def installer_report(self):
        return {"schema_version": 1, "passed": True, "installer_sha256": "a" * 64,
                "native_binary_sha256": "b" * 64, "release_eligible": False,
                "checks": {key: True for key in workflow.REQUIRED_INSTALLER_CHECKS}}

    def test_installer_gate_accepts_all_exact_true_scenarios(self):
        workflow.validate_installer_test(self.installer_report(), "a" * 64, "b" * 64)

    def test_installer_gate_rejects_missing_or_nonboolean_scenario(self):
        for key in workflow.REQUIRED_INSTALLER_CHECKS:
            for value in (None, False, 0, 1, "true", "passed"):
                with self.subTest(key=key, value=value):
                    report = self.installer_report()
                    report["checks"][key] = value
                    with self.assertRaisesRegex(ValueError, key):
                        workflow.validate_installer_test(report, "a" * 64, "b" * 64)
            report = self.installer_report()
            del report["checks"][key]
            with self.subTest(missing=key), self.assertRaisesRegex(ValueError, key):
                workflow.validate_installer_test(report, "a" * 64, "b" * 64)

    def test_installer_gate_rejects_missing_detailed_checks(self):
        for checks in (None, [], True, "all passed"):
            report = self.installer_report()
            report["checks"] = checks
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                workflow.validate_installer_test(report, "a" * 64, "b" * 64)

    def test_installer_gate_preserves_hash_and_status_requirements(self):
        for key, value in (("schema_version", True), ("schema_version", 2), ("passed", 1),
                           ("passed", False), ("installer_sha256", "c" * 64),
                           ("native_binary_sha256", "c" * 64), ("release_eligible", True)):
            report = self.installer_report()
            report[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                workflow.validate_installer_test(report, "a" * 64, "b" * 64)

    def test_artifact_creation_requires_detailed_installer_validation(self):
        stage = inspect.getsource(workflow.stage)
        self.assertLess(stage.index("validate_installer_test("), stage.index("output.mkdir()"))
        test_script = (ROOT / "tools/test_native_installer.ps1").read_text()
        for key in workflow.REQUIRED_INSTALLER_CHECKS:
            self.assertIn("$checks." + key + " = $true", test_script)


if __name__ == "__main__":
    unittest.main()
