"""Portable inert fixtures only: no native program, registry, or user-PC access."""
import ctypes
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import windows_test_roots as roots


class WindowsTestRootsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.work = self.base / "work"
        (self.work / "evidence").mkdir(parents=True)
        self.folders = {name: self.base / name for name in roots.FOLDERS}
        self.folders["roaming-appdata"] = self.folders["profile"] / "AppData" / "Roaming"
        for folder in self.folders.values():
            folder.mkdir(parents=True, exist_ok=True)
        self.environment = {
            "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
            "LOCALAPPDATA": str(self.folders["local-appdata"]),
            "APPDATA": str(self.folders["roaming-appdata"]),
        }
        for patcher in (
            mock.patch.dict(os.environ, self.environment),
            mock.patch.object(roots.sys, "platform", "win32"),
            mock.patch.object(roots, "shell_folder", side_effect=self.folders.__getitem__),
            mock.patch.object(roots, "registry_present", return_value=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def report_path(self, phase="initial"):
        return self.work / "evidence" / ("windows-test-roots-" + phase + ".json")

    def report(self, phase="initial"):
        return json.loads(self.report_path(phase).read_text(encoding="utf-8"))

    def refused(self, phase="initial"):
        with self.assertRaisesRegex(ValueError, "not fresh"):
            roots.check(self.work, phase)
        proof = self.report(phase)
        self.assertFalse(proof["passed"])
        self.assertEqual(len(proof["targets"]), 5)
        return proof

    def test_absent_targets_pass_with_fixed_labels_and_regular_report(self):
        proof = roots.check(self.work, "initial")
        self.assertEqual(proof, self.report())
        self.assertTrue(proof["passed"])
        self.assertEqual(set(proof["targets"]), {
            label for label, *_ in roots.FILESYSTEM_TARGETS} | {roots.REGISTRY_LABEL})
        self.assertTrue(stat.S_ISREG(self.report_path().lstat().st_mode))
        for result in proof["targets"].values():
            self.assertEqual(result, {"presence": "absent", "kind": "none", "errors": []})

    def test_every_filesystem_target_refuses_without_altering_existing_data(self):
        for index, (label, folder, basename, _) in enumerate(roots.FILESYSTEM_TARGETS):
            with self.subTest(label=label):
                target = self.folders[folder] / basename
                target.mkdir()
                secret = target / "private-filename-and-content.txt"
                secret.write_text("private content must never be read")
                proof = self.refused(roots.PHASES[index])
                self.assertEqual(proof["targets"][label]["presence"], "present")
                self.assertEqual(proof["targets"][label]["kind"], "directory")
                self.assertEqual(secret.read_text(), "private content must never be read")
                secret.unlink()
                target.rmdir()

    def test_existing_file_even_where_directory_expected_refuses(self):
        target = self.folders["local-appdata"] / "PrivacyBrowser"
        target.write_text("untouched")
        proof = self.refused()
        self.assertEqual(proof["targets"]["local-appdata-root"]["kind"], "file")
        self.assertEqual(target.read_text(), "untouched")

    def test_existing_registry_key_refuses(self):
        with mock.patch.object(roots, "registry_present", return_value=True):
            proof = self.refused()
        self.assertEqual(proof["targets"][roots.REGISTRY_LABEL],
                         {"presence": "present", "kind": "registry-key", "errors": []})

    def test_dangling_and_live_symlinks_refuse_without_following_them(self):
        for index, live in enumerate((False, True)):
            with self.subTest(live=live):
                destination = self.base / ("destination" + str(index))
                if live:
                    destination.mkdir()
                target = self.folders["local-appdata"] / "PrivacyBrowser"
                try:
                    target.symlink_to(destination, target_is_directory=True)
                except OSError:
                    self.skipTest("Host does not permit symlink creation")
                proof = self.refused(roots.PHASES[index])
                self.assertEqual(proof["targets"]["local-appdata-root"]["kind"],
                                 "link-or-reparse")
                self.assertTrue(target.is_symlink())
                target.unlink()

    def test_reparse_attribute_refuses_even_if_mode_is_directory(self):
        target = self.folders["local-appdata"] / "PrivacyBrowser"
        original = Path.lstat

        def metadata(path, *args, **kwargs):
            if path == target:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_file_attributes=0x400)
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "lstat", metadata):
            proof = self.refused()
        self.assertEqual(proof["targets"]["local-appdata-root"]["kind"], "link-or-reparse")

    def test_target_parent_reparse_is_not_mistaken_for_absence(self):
        target_parent = self.folders["local-appdata"]
        original = Path.lstat

        def metadata(path, *args, **kwargs):
            if path == target_parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_file_attributes=0x400)
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "lstat", metadata):
            proof = self.refused()
        self.assertEqual(proof["targets"]["local-appdata-root"]["errors"],
                         ["parent-link-or-reparse"])

    def test_missing_shell_folder_parent_is_not_absence(self):
        self.folders["local-appdata"].rmdir()
        proof = self.refused()
        self.assertEqual(proof["targets"]["local-appdata-root"]["errors"], ["missing-parent"])

    def test_nondirectory_shell_folder_parent_is_not_absence(self):
        self.folders["local-appdata"].rmdir()
        self.folders["local-appdata"].write_text("untouched parent fixture")
        proof = self.refused()
        self.assertEqual(proof["targets"]["local-appdata-root"]["errors"],
                         ["parent-not-directory"])

    def test_file_inspection_failure_is_redacted_and_other_targets_are_checked(self):
        target = self.folders["local-appdata"] / "PrivacyBrowser"
        original = Path.lstat

        def metadata(path, *args, **kwargs):
            if path == target:
                raise PermissionError("private-path-and-error-content")
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "lstat", metadata):
            proof = self.refused()
        self.assertEqual(proof["targets"]["local-appdata-root"]["errors"], ["permission-denied"])
        self.assertEqual(proof["targets"][roots.REGISTRY_LABEL]["presence"], "absent")
        self.assertNotIn("private-path", self.report_path().read_text())

    def test_shell_folder_query_failure_is_retained_without_error_details(self):
        with mock.patch.object(roots, "shell_folder", side_effect=OSError("private API details")):
            proof = self.refused()
        for label, *_ in roots.FILESYSTEM_TARGETS:
            self.assertEqual(proof["targets"][label]["errors"], ["os-error"])
        self.assertNotIn("private API", self.report_path().read_text())

    def test_registry_query_failure_is_not_absence(self):
        with mock.patch.object(roots, "registry_present",
                               side_effect=PermissionError("private registry details")):
            proof = self.refused()
        self.assertEqual(proof["targets"][roots.REGISTRY_LABEL]["errors"],
                         ["permission-denied"])

    def test_unexpected_error_class_and_message_are_bounded(self):
        class PrivateNamedFailure(Exception):
            pass
        with mock.patch.object(roots, "registry_present",
                               side_effect=PrivateNamedFailure("sensitive message")):
            proof = self.refused()
        text = self.report_path().read_text()
        self.assertEqual(proof["targets"][roots.REGISTRY_LABEL]["errors"], ["unexpected-error"])
        self.assertNotIn("PrivateNamedFailure", text)
        self.assertNotIn("sensitive", text)

    def test_environment_override_refuses_but_inspects_actual_shell_folder(self):
        for index, (name, folder, label) in enumerate((
            ("LOCALAPPDATA", "local-appdata", "local-appdata-root"),
            ("APPDATA", "roaming-appdata", "roaming-appdata-root"),
        )):
            with self.subTest(name=name):
                target = self.folders[folder] / "PrivacyBrowser"
                target.mkdir()
                with mock.patch.dict(os.environ, {name: str(self.base / "empty-override")}):
                    proof = self.refused(roots.PHASES[index])
                self.assertEqual(proof["targets"][label]["presence"], "present")
                self.assertEqual(proof["targets"][label]["errors"], ["environment-mismatch"])
                target.rmdir()

    def test_missing_environment_refuses(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.update({key: value for key, value in self.environment.items()
                               if key != "LOCALAPPDATA"})
            proof = self.refused()
        self.assertEqual(proof["targets"]["local-appdata-root"]["errors"],
                         ["environment-mismatch"])

    def test_report_never_contains_user_paths_names_or_contents(self):
        target = self.folders["roaming-appdata"] / "PrivacyBrowser"
        target.mkdir()
        (target / "ultra-private-filename.txt").write_text("ultra-private-file-content")
        self.refused()
        text = self.report_path().read_text()
        for secret in (str(self.base), self.base.name, "ultra-private",
                       "PrivacyBrowser.lnk", roots.REGISTRY_KEY):
            self.assertNotIn(secret, text)
        self.assertLess(len(text), 4096)

    def test_no_target_contents_or_registry_values_are_read(self):
        target = self.folders["local-appdata"] / "PrivacyBrowser"
        target.mkdir()
        original = Path.open

        def open_report_only(path, *args, **kwargs):
            self.assertEqual(path, self.report_path())
            self.assertEqual(args[0], "x")
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "open", open_report_only), \
             mock.patch.object(Path, "iterdir", side_effect=AssertionError("no enumeration")), \
             mock.patch.object(Path, "glob", side_effect=AssertionError("no enumeration")):
            with self.assertRaisesRegex(ValueError, "not fresh"):
                roots.check(self.work, "initial")
        self.assertFalse(self.report()["passed"])

    def test_output_reuse_refuses_before_inspecting_targets_and_preserves_report(self):
        roots.check(self.work, "initial")
        content = self.report_path().read_bytes()
        with mock.patch.object(roots, "inspect_targets") as inspect:
            with self.assertRaisesRegex(ValueError, "report-already-exists"):
                roots.check(self.work, "initial")
            inspect.assert_not_called()
        self.assertEqual(content, self.report_path().read_bytes())

    def test_dangling_output_symlink_refuses_before_inspection(self):
        destination = self.base / "must-not-be-created"
        try:
            self.report_path().symlink_to(destination)
        except OSError:
            self.skipTest("Host does not permit symlink creation")
        with mock.patch.object(roots, "inspect_targets") as inspect:
            with self.assertRaisesRegex(ValueError, "report-already-exists"):
                roots.check(self.work, "initial")
            inspect.assert_not_called()
        self.assertFalse(destination.exists())
        self.assertTrue(self.report_path().is_symlink())

    def test_reparse_evidence_parent_refuses_without_inspection(self):
        original = Path.lstat

        def metadata(path, *args, **kwargs):
            if path == self.work:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_file_attributes=0x400)
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "lstat", metadata), \
             mock.patch.object(roots, "inspect_targets") as inspect:
            with self.assertRaisesRegex(ValueError, "parent-link-or-reparse"):
                roots.check(self.work, "initial")
            inspect.assert_not_called()
        self.assertFalse(self.report_path().exists())

    def test_missing_evidence_directory_is_not_created(self):
        (self.work / "evidence").rmdir()
        with mock.patch.object(roots, "inspect_targets") as inspect:
            with self.assertRaisesRegex(ValueError, "missing-parent"):
                roots.check(self.work, "initial")
            inspect.assert_not_called()
        self.assertFalse((self.work / "evidence").exists())

    def test_report_open_failure_cannot_pass_or_leak_paths(self):
        with mock.patch.object(Path, "open", side_effect=PermissionError("private output path")):
            with self.assertRaisesRegex(ValueError, "could not be written: permission-denied") as error:
                roots.check(self.work, "initial")
        self.assertNotIn("private output path", str(error.exception))
        self.assertFalse(self.report_path().exists())

    def test_report_write_failure_cannot_pass(self):
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.fileno.return_value = 42
        stream.write.side_effect = OSError("private output path")
        with mock.patch.object(Path, "open", return_value=stream), \
             mock.patch.object(os, "fstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG)):
            with self.assertRaisesRegex(ValueError, "could not be written: os-error"):
                roots.check(self.work, "initial")

    def test_partial_failed_report_is_retained_never_deleted_or_retried(self):
        def failed_dump(report, stream, **kwargs):
            stream.write('{"incomplete":')
            raise OSError("private write failure")
        with mock.patch.object(roots.json, "dump", side_effect=failed_dump) as write:
            with self.assertRaisesRegex(ValueError, "could not be written: os-error"):
                roots.check(self.work, "initial")
            write.assert_called_once()
        self.assertEqual(self.report_path().read_text(), '{"incomplete":')
        with self.assertRaisesRegex(ValueError, "report-already-exists"):
            roots.check(self.work, "initial")

    def test_exclusive_open_prevents_late_output_replacement(self):
        original = roots.inspect_targets
        def another_report_arrives():
            proof = original()
            self.report_path().write_text("untouched late report")
            return proof
        with mock.patch.object(roots, "inspect_targets", side_effect=another_report_arrives):
            with self.assertRaisesRegex(ValueError, "could not be written: report-already-exists"):
                roots.check(self.work, "initial")
        self.assertEqual(self.report_path().read_text(), "untouched late report")

    def test_nonregular_report_handle_cannot_pass(self):
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        with mock.patch.object(Path, "open", return_value=stream), \
             mock.patch.object(os, "fstat", return_value=SimpleNamespace(st_mode=stat.S_IFIFO)):
            with self.assertRaisesRegex(ValueError, "report-not-regular"):
                roots.check(self.work, "initial")
        stream.write.assert_not_called()

    def test_phase_allowlist_and_traversal_refuse_without_inspection(self):
        for phase in ("", "../private", "initial/elsewhere", "after-installer", "INITIAL"):
            with self.subTest(phase=phase), mock.patch.object(roots, "inspect_targets") as inspect:
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    roots.check(self.work, phase)
                inspect.assert_not_called()
        self.assertEqual(list((self.work / "evidence").iterdir()), [])

    def test_every_allowed_phase_produces_its_own_exclusive_report(self):
        for phase in roots.PHASES:
            with self.subTest(phase=phase):
                roots.check(self.work, phase)
                self.assertEqual(self.report(phase)["phase"], phase)
        self.assertEqual(len(list((self.work / "evidence").iterdir())), len(roots.PHASES))

    def test_host_requirements_refuse_before_any_target_or_output_access(self):
        for changes, platform in (
            ({"GITHUB_ACTIONS": "false"}, "win32"),
            ({"RUNNER_ENVIRONMENT": "self-hosted"}, "win32"),
            ({}, "linux"),
        ):
            with self.subTest(changes=changes, platform=platform), \
                 mock.patch.dict(os.environ, changes), \
                 mock.patch.object(sys, "platform", platform), \
                 mock.patch.object(roots, "inspect_targets") as inspect, \
                 mock.patch.object(Path, "lstat") as metadata:
                with self.assertRaisesRegex(ValueError, "ephemeral hosted Windows"):
                    roots.check(self.work, "initial")
                inspect.assert_not_called()
                metadata.assert_not_called()

    def test_security_and_runtime_environment_are_never_changed(self):
        overrides = {
            "MOZ_AUTOMATION": "1", "MOZ_DISABLE_CONTENT_SANDBOX": "1",
            "MOZ_DISABLE_GPU_SANDBOX": "1", "MOZ_FORCE_DISABLE_FISSION": "1",
            "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1", "MOZ_CRASHREPORTER_NO_REPORT": "0",
        }
        with mock.patch.dict(os.environ, overrides):
            before = dict(os.environ)
            roots.check(self.work, "initial")
            self.assertEqual(dict(os.environ), before)
            (self.folders["local-appdata"] / "PrivacyBrowser").mkdir()
            self.refused("before-build")
            self.assertEqual(dict(os.environ), before)

    def test_build_data_overrides_are_refused_in_every_phase(self):
        with mock.patch.dict(os.environ, {
            "MOZ_APP_DATA": "private-build-roaming-override",
            "MOZ_LOCAL_APP_DATA": "private-build-local-override",
        }):
            before = dict(os.environ)
            for phase in roots.PHASES:
                with self.subTest(phase=phase):
                    proof = self.refused(phase)
                    for label in ("local-appdata-root", "roaming-appdata-root"):
                        self.assertEqual(proof["targets"][label]["errors"],
                                         ["unexpected-build-root-override"])
                    self.assertNotIn("private-build-", self.report_path(phase).read_text())
                    self.assertEqual(dict(os.environ), before)

    def test_build_data_overrides_do_not_mask_existing_actual_shell_roots(self):
        (self.folders["roaming-appdata"] / "PrivacyBrowser").mkdir()
        with mock.patch.dict(os.environ, {"MOZ_APP_DATA": str(self.base / "unused-override")}):
            proof = self.refused("before-build")
        self.assertEqual(proof["targets"]["roaming-appdata-root"]["presence"], "present")

    def test_crash_helper_literal_roaming_must_equal_actual_known_folder(self):
        original = self.folders["roaming-appdata"]
        redirected = self.base / "redirected-roaming"
        redirected.mkdir()
        self.folders["roaming-appdata"] = redirected
        with mock.patch.dict(os.environ, {"APPDATA": str(redirected)}):
            proof = self.refused()
        self.assertEqual(proof["targets"]["roaming-appdata-root"]["errors"],
                         ["crash-helper-roaming-mismatch"])
        self.assertEqual(list(original.iterdir()), [])
        self.assertEqual(list(redirected.iterdir()), [])
        self.assertNotIn(str(redirected), self.report_path().read_text())

    def test_crash_helper_profile_query_failure_refuses_without_guessing(self):
        def folder(name):
            if name == "profile":
                raise PermissionError("private profile path")
            return self.folders[name]
        with mock.patch.object(roots, "shell_folder", side_effect=folder):
            proof = self.refused()
        self.assertEqual(proof["targets"]["roaming-appdata-root"]["errors"], ["permission-denied"])
        self.assertNotIn("private profile path", self.report_path().read_text())

    def test_base_exceptions_are_not_swallowed(self):
        for error in (KeyboardInterrupt(), SystemExit(9)):
            with self.subTest(error=type(error)), \
                 mock.patch.object(roots, "registry_present", side_effect=error):
                with self.assertRaises(type(error)):
                    roots.check(self.work, "initial")
        self.assertFalse(self.report_path().exists())

    def test_cli_accepts_only_phase_and_uses_fixed_work_root(self):
        with mock.patch.object(sys, "argv", ["windows_test_roots.py", "--phase", "initial"]), \
             mock.patch.object(roots, "check") as check:
            roots.main()
            check.assert_called_once_with(Path(r"D:\pb"), "initial")
        for option in ("--output", "--work-root"):
            with self.subTest(option=option), \
                 mock.patch.object(sys, "argv", ["windows_test_roots.py", "--phase",
                                                "initial", option, "arbitrary"]), \
                 mock.patch.object(sys, "stderr", io.StringIO()), \
                 mock.patch.object(roots, "check") as check:
                with self.assertRaises(SystemExit) as exit:
                    roots.main()
                self.assertEqual(exit.exception.code, 2)
                check.assert_not_called()


class WindowsApiWrapperTests(unittest.TestCase):
    def test_shell_folder_uses_noncreating_current_per_user_api(self):
        shell = mock.Mock()
        with tempfile.TemporaryDirectory() as temporary:
            def query(window, folder, token, flags, buffer):
                self.assertIsNone(window)
                self.assertIsNone(token)
                self.assertEqual(flags, 0)
                self.assertEqual(folder & 0x8000, 0)  # No CSIDL_FLAG_CREATE.
                buffer.value = temporary
                return 0
            shell.SHGetFolderPathW.side_effect = query
            with mock.patch.object(roots.ctypes, "WinDLL", create=True, return_value=shell) as dll:
                for name, csidl in roots.FOLDERS.items():
                    self.assertEqual(roots.shell_folder(name), Path(temporary))
                    self.assertEqual(shell.SHGetFolderPathW.call_args.args[1], csidl)
                dll.assert_called_with("shell32", use_last_error=True)
        self.assertEqual(shell.method_calls[0][0], "SHGetFolderPathW")

    def test_shell_folder_api_failure_or_empty_result_refuses(self):
        shell = mock.Mock()
        for status in (-2147024894, 0):
            shell.SHGetFolderPathW.return_value = status
            with self.subTest(status=status), \
                 mock.patch.object(roots.ctypes, "WinDLL", create=True, return_value=shell):
                with self.assertRaisesRegex(roots.InspectionError, "shell-folder-query-failed"):
                    roots.shell_folder("desktop")

    def test_shell_folder_relative_result_refuses(self):
        shell = mock.Mock()
        def query(window, folder, token, flags, buffer):
            buffer.value = "relative-folder"
            return 0
        shell.SHGetFolderPathW.side_effect = query
        with mock.patch.object(roots.ctypes, "WinDLL", create=True, return_value=shell):
            with self.assertRaisesRegex(roots.InspectionError, "invalid-folder-path"):
                roots.shell_folder("desktop")

    def registry(self, status=0, close_status=0):
        registry = mock.Mock()
        def query(root, key, options, access, output):
            self.assertEqual(root.value, ctypes.c_void_p(ctypes.c_int32(0x80000001).value).value)
            self.assertEqual(key, roots.REGISTRY_KEY)
            self.assertEqual(options, 0x8)  # Open registry link itself, never follow it.
            self.assertEqual(access, 0x20119)  # Read only, explicit 64-bit view.
            if status == 0:
                output._obj.value = 42
            return status
        registry.RegOpenKeyExW.side_effect = query
        registry.RegCloseKey.return_value = close_status
        return registry

    def test_registry_wrapper_only_opens_and_closes_fixed_64bit_key(self):
        registry = self.registry()
        with mock.patch.object(roots.ctypes, "WinDLL", create=True, return_value=registry):
            self.assertTrue(roots.registry_present())
        self.assertEqual([call[0] for call in registry.method_calls],
                         ["RegOpenKeyExW", "RegCloseKey"])
        self.assertEqual(registry.RegCloseKey.call_args.args[0].value, 42)

    def test_registry_absence_is_only_file_not_found(self):
        for status in (2, 3, 5, 1010):
            with self.subTest(status=status):
                registry = self.registry(status)
                with mock.patch.object(roots.ctypes, "WinDLL", create=True, return_value=registry):
                    if status == 2:
                        self.assertFalse(roots.registry_present())
                    else:
                        with self.assertRaisesRegex(roots.InspectionError, "registry-query-failed"):
                            roots.registry_present()
                registry.RegCloseKey.assert_not_called()

    def test_registry_close_failure_refuses(self):
        registry = self.registry(close_status=5)
        with mock.patch.object(roots.ctypes, "WinDLL", create=True, return_value=registry):
            with self.assertRaisesRegex(roots.InspectionError, "registry-close-failed"):
                roots.registry_present()

    def test_unknown_internal_error_code_is_not_emitted(self):
        self.assertEqual(roots.error_type(roots.InspectionError("private details")),
                         "unexpected-error")


if __name__ == "__main__":
    unittest.main()
