"""Strict retained evidence validation for the shared read-only IShellLinkW gate.

No COM execution is simulated here. WSH is bounded diagnostic data, never an
acceptance authority or a substitute for any failed Unicode read.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
from pathlib import Path
import re

import verify_windows_native as native

MAX_STRING = 1024
MAX_REPORT_BYTES = 1024 * 1024
ACTUAL_REPORT = "windows-installer-shortcuts.json"
INSTALL_DIR = r"D:\pb\installer-test\PrivacyBrowser-日本語"
HRESULTS = ("load_hresult", "target_hresult", "arguments_hresult", "icon_hresult", "working_directory_hresult")
FIELDS = ("target", "arguments", "icon", "working_directory")


def zero(value) -> bool:
    return type(value) is int and value == 0


def digest_value(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_wide(wide: dict, expected: dict) -> None:
    native.require(isinstance(wide, dict) and zero(wide.get("load_mode")) and zero(wide.get("path_flags")),
                   "Missing explicit wide shortcut reader")
    native.require(all(zero(wide.get(name)) for name in HRESULTS), "Wide shortcut HRESULT failed")
    native.require(wide.get("strings_within_bound") is True and wide.get("error_type") is None
                   and wide.get("error_hresult") is None, "Wide shortcut reader returned an error")
    for name in FIELDS:
        observed, value = wide.get(name), expected.get(name)
        native.require(isinstance(value, str) and 0 < len(value) <= MAX_STRING
                       and isinstance(observed, str) and 0 < len(observed) <= MAX_STRING,
                       "Missing or unbounded shortcut " + name)
        native.require(observed.lower() == value.lower() if name == "target" else observed == value,
                       "Shortcut " + name + " mismatch")
    native.require(type(wide.get("icon_index")) is int and wide["icon_index"] == expected["icon_index"],
                   "Shortcut icon_index mismatch")


def validate_wsh(wsh) -> None:
    native.require(isinstance(wsh, dict)
                   and {*FIELDS, "icon_location", "icon_index", "read_succeeded", "strings_within_bound", "error_type", "error_hresult"} <= set(wsh)
                   and type(wsh.get("read_succeeded")) is bool
                   and type(wsh.get("strings_within_bound")) is bool, "Missing bounded WSH diagnostic")
    for name in (*FIELDS, "icon_location", "error_type"):
        value = wsh.get(name)
        native.require(value is None or isinstance(value, str) and len(value) <= MAX_STRING,
                       "Unbounded WSH diagnostic")
    for name in ("icon_index", "error_hresult"):
        native.require(wsh.get(name) is None or type(wsh[name]) is int and -(2**31) <= wsh[name] < 2**31,
                       "Invalid WSH diagnostic integer")


def validate_link(link: dict, kind: str, path: str, wsh_observed: bool, expected: dict) -> None:
    native.require(isinstance(link, dict), "Invalid shortcut observation")
    identity = link.get("identity_before")
    native.require(link.get("kind") == kind and link.get("path") == path and link.get("passed") is True
                   and link.get("wsh_observed") is wsh_observed
                   and link.get("error_type") is None and link.get("error_hresult") is None
                   and type(link.get("bytes")) is int and 0 < link["bytes"] <= 65536
                   and type(link.get("bytes_after")) is int and link["bytes_after"] == link["bytes"]
                   and digest_value(link.get("sha256_before"))
                   and link["sha256_before"] == link.get("sha256_after")
                   and isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{8}:[0-9a-f]{8}:[0-9a-f]{8}", identity)
                   and identity == link.get("identity_after"), "Shortcut bytes or fixed identity changed")
    validate_wide(link.get("wide"), expected)
    if wsh_observed:
        validate_wsh(link.get("wsh"))
    else:
        native.require(link.get("wsh") is None, "WSH must not open custom-extension snapshots")


def validate_authority(report: dict) -> None:
    native.require(report.get("authoritative_reader") == "IShellLinkW"
                   and report.get("wsh_role") == "non-authoritative diagnostic", "Invalid shortcut authority")


def folder_path(value) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= MAX_STRING
            and re.match(r"^[A-Za-z]:\\", value) is not None
            and not any(part in ("", ".", "..") or ":" in part for part in value[3:].split("\\"))
            and not any(character in value for character in '/"<>|?*')
            and not any(ord(character) < 32 for character in value)
            and not any(part.endswith((".", " ")) for part in value[3:].split("\\")))


def validate_actual(report: dict, installer_sha256: str, binary_sha256: str) -> None:
    native.require(isinstance(report, dict) and type(report.get("schema_version")) is int
                   and report["schema_version"] == 1 and report.get("passed") is True
                   and report.get("installer_sha256") == installer_sha256
                   and report.get("native_binary_sha256") == binary_sha256
                   and report.get("install_directory") == INSTALL_DIR,
                   "Missing matching actual shortcut evidence")
    validate_authority(report)
    for name in ("browser_executed", "shortcut_executed", "resolve_called", "save_called", "release_eligible"):
        native.require(report.get(name) is False, "Unexpected shortcut execution/persistence claim")
    native.require(report.get("error_type") is None and report.get("error_hresult") is None
                   and report.get("copies_identical") is True and report.get("files_unchanged") is True,
                   "Actual shortcut observation failed")
    folders = report.get("known_folders")
    native.require(isinstance(folders, dict) and set(folders) == {"desktop", "programs", "local_appdata"}
                   and all(folder_path(value) for value in folders.values()), "Invalid known-folder observations")
    expected = {"target": ntpath.join(INSTALL_DIR, "privacybrowser.exe"),
                "arguments": '-no-remote -profile "' + ntpath.join(folders["local_appdata"], "PrivacyBrowser", "Profile") + '"',
                "icon": ntpath.join(INSTALL_DIR, "distribution", "privacybrowser", "assets", "privacybrowser.ico"),
                "icon_index": 0, "working_directory": INSTALL_DIR}
    native.require(report.get("expected") == expected and type(report["expected"].get("icon_index")) is int, "Actual shortcut expected fields do not match fixed installation")
    entries = (("desktop", ntpath.join(folders["desktop"], "PrivacyBrowser.lnk"), True),
               ("program", ntpath.join(folders["programs"], "PrivacyBrowser", "PrivacyBrowser.lnk"), True),
               ("desktop-snapshot", ntpath.join(INSTALL_DIR, "PrivacyBrowser.desktop-link"), False),
               ("program-snapshot", ntpath.join(INSTALL_DIR, "PrivacyBrowser.program-link"), False))
    links = report.get("links")
    native.require(isinstance(links, list) and len(links) == len(entries), "Missing actual shortcut observations")
    for link, (kind, path, wsh) in zip(links, entries):
        validate_link(link, kind, path, wsh, expected)
    for source, copied in ((2, 0), (3, 1)):
        native.require(links[source]["sha256_before"] == links[copied]["sha256_before"]
                       and links[source]["bytes"] == links[copied]["bytes"], "Actual shortcut snapshot mismatch")


def validate_binding(binding: dict, report: dict, evidence: Path | None = None) -> None:
    native.require(isinstance(binding, dict) and binding.get("path") == ACTUAL_REPORT
                   and type(binding.get("bytes")) is int and 0 < binding["bytes"] <= MAX_REPORT_BYTES
                   and digest_value(binding.get("sha256")), "Missing actual shortcut evidence binding")
    # Bind the exact retained bytes even for offline consumers that only receive
    # the final installer report. JSON is data, not a pass/fail summary assertion.
    raw = binding.get("json")
    native.require(isinstance(raw, str) and 0 < len(raw) <= MAX_REPORT_BYTES, "Missing or unbounded retained actual shortcut JSON")
    data = raw.encode("utf-8")
    native.require(len(data) == binding["bytes"] and hashlib.sha256(data).hexdigest() == binding["sha256"],
                   "Actual shortcut evidence hash mismatch")
    parsed = json.loads(raw)
    validate_actual(parsed, report.get("installer_sha256"), report.get("native_binary_sha256"))
    # Ordinary Python equality conflates False/0 and True/1 (and 0.0/0).
    # Compare typed JSON representations too, not just dictionary equality.
    native.require(json.dumps(parsed, sort_keys=True, ensure_ascii=False, allow_nan=False)
                   == json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False),
                   "Actual shortcut embedded evidence mismatch")
    if evidence is not None:
        path = native.regular_file(evidence, ACTUAL_REPORT)
        native.require(path.stat().st_size == len(data) and path.read_bytes() == data,
                       "Retained actual shortcut evidence mismatch")
