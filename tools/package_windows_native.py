#!/usr/bin/env python3
"""Package an independently source-built, statically verified Windows candidate.

The developer-overlay packager is intentionally separate. This program never
patches executable resources, signs, publishes, installs or starts the browser.
Its NSIS template installs per-user without default-browser/file associations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from package_windows import runtime_files, write_payload_include
from verify_windows_native import validate_candidate, validate_installer

ROOT = Path(__file__).resolve().parents[1]
SETUP_NAME = "PrivacyBrowser-0.1.0-dev-win64-setup-UNSIGNED.exe"
RESERVED = {"uninstall.exe", "PrivacyBrowser.install-marker", "PrivacyBrowser.cleanup-intent",
            "PrivacyBrowser.desktop-link", "PrivacyBrowser.program-link"}


def validate_paths(runtime: Path, evidence: Path, output: Path) -> None:
    if runtime.is_symlink() or output.is_symlink():
        raise ValueError("Payload and installer output cannot be symbolic links")
    base = runtime.resolve()
    target = output.resolve()
    if target.name != SETUP_NAME:
        raise ValueError("Use the fixed development installer basename: " + SETUP_NAME)
    if base == target or base in target.parents:
        raise ValueError("Installer output must be outside the runtime payload")
    if any(value in str(target) for value in ('$', '"', '\n', '\r', '\t')):
        raise ValueError("Unsafe NSIS output path")
    outputs = [target, Path(str(target) + ".json"), Path(str(target) + ".build.log")]
    if any(path.exists() or path.is_symlink() for path in outputs):
        raise ValueError("Installer or report already exists; no artifact is overwritten")
    if evidence.resolve() in outputs:
        raise ValueError("Output cannot overwrite build evidence")
    # NSIS materializes the payload on Windows even when packaging on a
    # case-sensitive host. Reserved names therefore compare case-insensitively.
    if base.is_dir() and any(path.name.casefold() in {name.casefold() for name in RESERVED}
                             for path in base.iterdir()):
        raise ValueError("Runtime contains files reserved for the independent installer")
    for name in ("LICENSE", "NOTICE"):
        path = base / name
        if not path.is_file() or path.is_symlink() or not path.read_bytes().strip():
            raise ValueError("Stage project LICENSE and NOTICE before hashing the payload")
    files = runtime_files(base)  # Reject unsafe NSIS paths and Windows case collisions.
    if any(len(path.relative_to(base).as_posix()) > 240 for path in files):
        raise ValueError("Native payload path exceeds the supported Windows path limit")


def write_native_payload_include(runtime: Path, destination: Path) -> dict:
    """Retain the exact install list, adding retry-safe native-only removal.

    The browser executable remains until every other packaged file is gone, so
    a failed uninstall can retry its exclusive-open guard. Unknown files and
    directories never count as packaged payload and are never recursively removed.
    """
    payload = write_payload_include(runtime, destination)
    install, separator, _ = destination.read_text(encoding="utf-8").partition("!macro RemovePayload\n")
    if not separator:
        raise ValueError("Expected the reviewed exact-payload include format")
    files = [path.relative_to(runtime.resolve()) for path in runtime_files(runtime)]
    executable = Path("privacybrowser.exe")
    if executable not in files:
        raise ValueError("Native payload requires privacybrowser.exe")
    other_files = [path for path in files if path != executable]
    directories = {parent for path in files for parent in path.parents if parent != Path(".")}
    lines = ["!macro ValidateNativePayload"]
    for folder in sorted(directories, key=lambda path: (len(path.parts), path.as_posix())):
        lines += ['  Push "$INSTDIR\\' + folder.as_posix().replace("/", "\\") + '"',
                  "  Call un.LockPayloadDirectory"]
    for path in files:
        lines += ['  Push "$INSTDIR\\' + path.as_posix().replace("/", "\\") + '"',
                  "  Call un.ValidatePayloadFile"]
    lines += ["!macroend", "", "!macro VerifyNativePayloadAbsent"]
    for path in files:
        lines += ['  IfFileExists "$INSTDIR\\' + path.as_posix().replace("/", "\\") + '" 0 +2',
                  "    Call un.DataRefusal"]
    lines += ["!macroend", "", "!macro RemoveNativePayload", "  StrCpy $R9 0"]
    for path in other_files:
        lines += ['  Push "$INSTDIR\\' + path.as_posix().replace("/", "\\") + '"',
                  "  Call un.DeletePayloadFile"]
    for path in other_files:
        lines += ['  IfFileExists "$INSTDIR\\' + path.as_posix().replace("/", "\\") + '" 0 +2',
                  "    StrCpy $R9 1"]
    lines += ["  ${If} $R9 == 0", '    Push "$INSTDIR\\privacybrowser.exe"', "    Call un.DeletePayloadFile",
              '    IfFileExists "$INSTDIR\\privacybrowser.exe" 0 +2', "      StrCpy $R9 1", "  ${EndIf}",
              "  ${If} $R9 == 0", "    Call un.ClosePayloadDirectoryLocks"]
    for folder in sorted(directories, key=lambda path: (-len(path.parts), path.as_posix())):
        lines += ['    Push "$INSTDIR\\' + folder.as_posix().replace("/", "\\") + '"',
                  "    Call un.RemovePayloadDirectory"]
    lines += ["  ${EndIf}", "!macroend", ""]
    maximum = max(len(path.as_posix()) for path in files)
    destination.write_text(f"!define NATIVE_MAX_RELATIVE_PATH {maximum}\n" + install + "\n".join(lines), encoding="utf-8")
    return payload


def build(runtime: Path, evidence: Path, output: Path, compiler: Path | None = None) -> dict:
    validate_paths(runtime, evidence, output)
    validation = validate_candidate(runtime, evidence)
    command_path = str(compiler) if compiler is not None else shutil.which("makensis")
    if not command_path or not Path(command_path).is_file():
        raise ValueError("A verified NSIS makensis compiler is required")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = "/" if os.name == "nt" else "-"
    with tempfile.TemporaryDirectory(prefix="privacybrowser-native-nsis-") as temporary:
        include = Path(temporary) / "payload.nsh"
        payload = write_native_payload_include(runtime, include)
        command = [command_path, prefix + "V3", prefix + "NOCD",
                   prefix + "DRUNTIME_DIR=" + str(runtime.resolve()),
                   prefix + "DPAYLOAD_INCLUDE=" + str(include),
                   prefix + "DOUTPUT_FILE=" + str(output),
                   str(ROOT / "packaging/windows/privacy-browser-native.nsi")]
        result = subprocess.run(command, capture_output=True, text=True, timeout=900)
        with Path(str(output) + ".build.log").open("x", encoding="utf-8") as log:
            log.write(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError("Native NSIS compilation failed; inspect the retained build log")
    # Fail closed if NSIS generated stale product strings or a non-project icon.
    installer = validate_installer(output)
    proof = {
        "schema_version": 1,
        "artifact": output.name,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "artifact_bytes": output.stat().st_size,
        "build_kind": "full-source-build-candidate-installer",
        "native_binary_sha256": validation["native_binary_sha256"],
        "source_build_evidence": validation["source_build_evidence"],
        "nsis_payload": payload,
        "installer_static_validation": installer,
        "installer_compiled": True,
        "authenticode_signed": False,
        "windows_execution_tested": False,
        "windows_install_uninstall_tested": False,
        "default_browser_registration": "not implemented; no associations are changed",
        "automatic_updates": "disabled",
        "official_release": False,
        "release_eligible": False,
    }
    with Path(str(output) + ".json").open("x", encoding="utf-8") as report:
        json.dump(proof, report, indent=2)
        report.write("\n")
    return proof


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.runtime, args.evidence, args.output, args.compiler), indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit("Native installer refused: " + str(error))
