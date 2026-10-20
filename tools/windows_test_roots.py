#!/usr/bin/env python3
"""Read-only, fail-closed freshness evidence for disposable Windows tests.

Only fixed per-user PrivacyBrowser roots are inspected, by metadata or registry
key existence. No contents, filenames within a root, or registry values are read.
This probe never removes, adopts, masks, or repairs an existing test root.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import ntpath
import os
from pathlib import Path
import stat
import sys

PHASES = (
    "initial", "before-build", "before-package", "after-package", "before-installer", "fast-before", "fast-after",
)
FOLDERS = {
    "local-appdata": 0x001C,
    "roaming-appdata": 0x001A,
    "desktop": 0x0010,
    "programs": 0x0002,
    "profile": 0x0028,
}
FILESYSTEM_TARGETS = (
    ("local-appdata-root", "local-appdata", "PrivacyBrowser", "LOCALAPPDATA"),
    ("roaming-appdata-root", "roaming-appdata", "PrivacyBrowser", "APPDATA"),
    ("desktop-shortcut", "desktop", "PrivacyBrowser.lnk", None),
    ("programs-folder", "programs", "PrivacyBrowser", None),
)
REGISTRY_LABEL = "uninstall-key-hkcu64"
REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser"
REPARSE_ATTRIBUTE = 0x400
ERROR_TYPES = frozenset((
    "permission-denied", "missing-parent", "not-directory", "os-error",
    "unexpected-error", "parent-link-or-reparse", "parent-not-directory",
    "invalid-folder-path", "environment-mismatch", "shell-folder-query-failed",
    "registry-query-failed", "registry-close-failed", "report-not-regular",
    "report-already-exists",
    "unexpected-build-root-override", "crash-helper-roaming-mismatch",
))


class InspectionError(ValueError):
    """An internal bounded code, never an underlying API/path error string."""

    def __init__(self, code: str):
        self.code = code if code in ERROR_TYPES else "unexpected-error"
        super().__init__(self.code)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def error_type(error: Exception) -> str:
    if isinstance(error, InspectionError):
        return error.code
    if isinstance(error, PermissionError):
        return "permission-denied"
    if isinstance(error, FileNotFoundError):
        return "missing-parent"
    if isinstance(error, FileExistsError):
        return "report-already-exists"
    if isinstance(error, NotADirectoryError):
        return "not-directory"
    if isinstance(error, OSError):
        return "os-error"
    return "unexpected-error"


def linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & REPARSE_ATTRIBUTE)


def directory_chain(path: Path) -> None:
    """Inspect every parent without resolving through a link or reparse point."""
    if not path.is_absolute() or ".." in path.parts:
        raise InspectionError("invalid-folder-path")
    for directory in (*reversed(path.parents), path):
        info = directory.lstat()
        if linked(info):
            raise InspectionError("parent-link-or-reparse")
        if not stat.S_ISDIR(info.st_mode):
            raise InspectionError("parent-not-directory")


def shell_folder(name: str) -> Path:
    """Query a current per-user shell folder with NO CSIDL_FLAG_CREATE flag."""
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    query = shell.SHGetFolderPathW
    query.argtypes = (
        wintypes.HWND, ctypes.c_int, wintypes.HANDLE, wintypes.DWORD,
        wintypes.LPWSTR,
    )
    query.restype = ctypes.c_long
    buffer = ctypes.create_unicode_buffer(260)
    # CSIDL IDs contain no creation flag; SHGFP_TYPE_CURRENT is exactly zero.
    status = query(None, FOLDERS[name], None, 0, buffer)
    if status != 0 or not buffer.value:
        raise InspectionError("shell-folder-query-failed")
    folder = Path(buffer.value)
    if not folder.is_absolute() or ".." in folder.parts:
        raise InspectionError("invalid-folder-path")
    return folder


def registry_present() -> bool:
    """Open only the fixed HKCU64 key, including registry links, without values."""
    registry = ctypes.WinDLL("advapi32", use_last_error=True)
    query = registry.RegOpenKeyExW
    query.argtypes = (
        wintypes.HKEY, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.HKEY),
    )
    query.restype = wintypes.LONG
    close = registry.RegCloseKey
    close.argtypes = (wintypes.HKEY,)
    close.restype = wintypes.LONG
    handle = wintypes.HKEY()
    # Sign-extend the predefined HKEY just as the Windows SDK macro does.
    current_user = wintypes.HKEY(ctypes.c_int32(0x80000001).value)
    # REG_OPTION_OPEN_LINK avoids following a dangling registry symbolic link.
    # KEY_READ | KEY_WOW64_64KEY chooses the installer guard's explicit view.
    status = query(current_user, REGISTRY_KEY, 0x8, 0x20019 | 0x0100,
                   ctypes.byref(handle))
    if status == 2:  # ERROR_FILE_NOT_FOUND is the only accepted absence result.
        return False
    if status != 0 or not handle.value:
        raise InspectionError("registry-query-failed")
    if close(handle) != 0:
        raise InspectionError("registry-close-failed")
    return True


def unknown(error: Exception) -> dict:
    return {"presence": "unknown", "kind": "unknown", "errors": [error_type(error)]}


def path_presence(path: Path) -> dict:
    try:
        # A missing/linked parent is an inspection failure, not proof of absence.
        directory_chain(path.parent)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return {"presence": "absent", "kind": "none", "errors": []}
        if linked(info):
            kind = "link-or-reparse"
        elif stat.S_ISDIR(info.st_mode):
            kind = "directory"
        elif stat.S_ISREG(info.st_mode):
            kind = "file"
        else:
            kind = "other"
        return {"presence": "present", "kind": kind, "errors": []}
    except Exception as error:
        return unknown(error)


def matching_environment(name: str, folder: Path) -> bool:
    value = os.environ.get(name)
    return bool(value and Path(value).is_absolute() and ".." not in Path(value).parts
                and ntpath.normcase(ntpath.normpath(value))
                == ntpath.normcase(ntpath.normpath(str(folder))))


def inspect_targets() -> dict:
    targets = {}
    for label, folder_name, basename, environment in FILESYSTEM_TARGETS:
        try:
            folder = shell_folder(folder_name)
            result = path_presence(folder / basename)
            if folder_name == "roaming-appdata":
                # The pinned Rust crash helper derives this literal suffix from
                # FOLDERID_Profile, not from APPDATA or Gecko overrides. Refuse
                # redirected layouts rather than writing outside the owned root.
                profile_roaming = shell_folder("profile") / "AppData" / "Roaming"
                directory_chain(profile_roaming)
                if (ntpath.normcase(ntpath.normpath(str(profile_roaming)))
                        != ntpath.normcase(ntpath.normpath(str(folder)))):
                    result["errors"].append("crash-helper-roaming-mismatch")
            if environment and not matching_environment(environment, folder):
                result["errors"].append("environment-mismatch")
            targets[label] = result
        except Exception as error:
            targets[label] = unknown(error)
    try:
        present = registry_present()
        targets[REGISTRY_LABEL] = {
            "presence": "present" if present else "absent",
            "kind": "registry-key" if present else "none", "errors": [],
        }
    except Exception as error:
        targets[REGISTRY_LABEL] = unknown(error)
    return targets


def write_report(path: Path, report: dict) -> None:
    """Create exactly one fresh regular report; no mkdir, retries, or overwrite."""
    try:
        directory_chain(path.parent)
        # Exclusive creation refuses existing files, directories, and dangling
        # links alike. Parent metadata checks never read any directory contents.
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            info = os.fstat(stream.fileno())
            if linked(info) or not stat.S_ISREG(info.st_mode):
                raise InspectionError("report-not-regular")
            directory_chain(path.parent)
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
    except Exception as error:
        raise ValueError("Windows test-root report could not be written: "
                         + error_type(error)) from None


def check(work: Path, phase: str) -> dict:
    """Check fixed targets; retain bounded refusal evidence before failing.

    The injectable work root exists for portable tests. The CLI deliberately
    exposes no work/output override and always passes the fixed D:\\pb root.
    The caller must already have created a fresh real evidence directory.
    """
    require(sys.platform == "win32"
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Windows test-root checks require an ephemeral hosted Windows runner")
    require(phase in PHASES, "Unsupported Windows test-root phase")
    report_path = work / "evidence" / ("windows-test-roots-" + phase + ".json")
    # Refuse unsafe/unavailable output before touching even target metadata.
    try:
        directory_chain(report_path.parent)
        try:
            report_path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise InspectionError("report-already-exists")
    except Exception as error:
        raise ValueError("Windows test-root report is unavailable: "
                         + error_type(error)) from None
    targets = inspect_targets()
    # No app-data redirection is used to hide actual known-folder writes.
    for variable, label in (
        ("MOZ_LOCAL_APP_DATA", "local-appdata-root"),
        ("MOZ_APP_DATA", "roaming-appdata-root"),
    ):
        if os.environ.get(variable):
            targets[label]["errors"].append("unexpected-build-root-override")
    passed = all(item["presence"] == "absent" and not item["errors"]
                 for item in targets.values())
    report = {"schema_version": 1, "phase": phase, "passed": passed,
              "targets": targets}
    write_report(report_path, report)
    require(passed, "Windows test roots are not fresh; see the fixed phase report")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=PHASES)
    args = parser.parse_args()
    check(Path(r"D:\pb"), args.phase)


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        raise SystemExit(str(error)) from None
