"""Authored models only; these are never genuine Windows COM evidence."""
import hashlib
import json
import ntpath

import windows_shortcut_evidence as shortcuts


def actual_shortcut_report(installer="a" * 64, binary="b" * 64):
    folders = {"desktop": r"C:\Users\fixture\Desktop",
               "programs": r"C:\Users\fixture\AppData\Roaming\Microsoft\Windows\Start Menu\Programs",
               "local_appdata": r"C:\Users\fixture\AppData\Local"}
    expected = {"target": ntpath.join(shortcuts.INSTALL_DIR, "privacybrowser.exe"),
                "arguments": '-no-remote -profile "' + ntpath.join(folders["local_appdata"], "PrivacyBrowser", "Profile") + '"',
                "icon": ntpath.join(shortcuts.INSTALL_DIR, "distribution", "privacybrowser", "assets", "privacybrowser.ico"),
                "icon_index": 0, "working_directory": shortcuts.INSTALL_DIR}
    report = {"schema_version": 1, "authoritative_reader": "IShellLinkW", "wsh_role": "non-authoritative diagnostic",
              "passed": True, "installer_sha256": installer, "native_binary_sha256": binary,
              "install_directory": shortcuts.INSTALL_DIR, "known_folders": folders, "expected": expected,
              "links": [], "copies_identical": True, "files_unchanged": True, "error_type": None, "error_hresult": None}
    for name in ("browser_executed", "shortcut_executed", "resolve_called", "save_called", "release_eligible"):
        report[name] = False
    paths = (("desktop", ntpath.join(folders["desktop"], "PrivacyBrowser.lnk"), True),
             ("program", ntpath.join(folders["programs"], "PrivacyBrowser", "PrivacyBrowser.lnk"), True),
             ("desktop-snapshot", ntpath.join(shortcuts.INSTALL_DIR, "PrivacyBrowser.desktop-link"), False),
             ("program-snapshot", ntpath.join(shortcuts.INSTALL_DIR, "PrivacyBrowser.program-link"), False))
    for kind, path, wsh in paths:
        wide = {**expected, "strings_within_bound": True, "error_type": None, "error_hresult": None,
                "load_mode": 0, "path_flags": 0, **{key: 0 for key in shortcuts.HRESULTS}}
        diagnostic = {**expected, "icon_location": expected["icon"] + ",0", "read_succeeded": True,
                      "strings_within_bound": True, "error_type": None, "error_hresult": None}
        report["links"].append({"kind": kind, "path": path, "bytes": 256, "bytes_after": 256,
                                "sha256_before": "c" * 64, "sha256_after": "c" * 64,
                                "identity_before": "11111111:00000000:00000001", "identity_after": "11111111:00000000:00000001",
                                "wide": wide, "wsh": diagnostic if wsh else None, "wsh_observed": wsh, "passed": True,
                                "error_type": None, "error_hresult": None})
    return report


def attach_shortcut_report(installer_report, evidence=None):
    report = actual_shortcut_report(installer_report["installer_sha256"], installer_report["native_binary_sha256"])
    raw = json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\r\n"
    data = raw.encode("utf-8")
    installer_report["shortcut_readback"] = report
    installer_report["shortcut_readback_evidence"] = {"path": shortcuts.ACTUAL_REPORT, "json": raw,
                                                     "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if evidence is not None:
        (evidence / shortcuts.ACTUAL_REPORT).write_bytes(data)
    return installer_report
