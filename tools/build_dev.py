#!/usr/bin/env python3
"""Assemble an explicitly unofficial developer runtime without altering its input.

This is a local development repack, not an independently compiled Gecko browser.
Native binary identity/branding remains that of the supplied runtime. Production
redistribution must use the separately branded source-build route instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
AUTOCONFIG = '''// Trusted Privacy Browser developer chrome, not page content.
pref("general.config.filename", "privacybrowser.cfg");
pref("general.config.obscure_value", 0);
pref("general.config.sandbox_enabled", false);
pref("app.update.enabled", false);
pref("app.update.auto", false);
pref("app.update.background.scheduling.enabled", false);
pref("app.update.url", "");
'''


def overlay_files(overlay: Path) -> list[Path]:
    if not overlay.is_dir() or not (overlay / "bootstrap.cfg").is_file():
        raise ValueError("Overlay requires browser/privacy/bootstrap.cfg")
    manifest = "chrome.manifest" if (overlay / "chrome.manifest").is_file() else "privacybrowser.manifest"
    if not (overlay / manifest).is_file():
        raise ValueError("Overlay requires chrome.manifest (or privacybrowser.manifest)")
    return sorted(p for p in overlay.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def install_overlay(runtime: Path, overlay: Path) -> None:
    overlay_files(overlay)
    # Distribution-provided symlinks must never redirect a developer write back
    # into /etc, the original runtime, or another application installation.
    for relative in ("distribution", "distribution/privacybrowser", "distribution/policies.json", "defaults", "defaults/pref", "defaults/pref/privacybrowser-autoconfig.js", "privacybrowser.cfg"):
        if (runtime / relative).is_symlink():
            raise ValueError("Refusing to modify a runtime configuration path through a symlink")
    target = runtime / "distribution" / "privacybrowser"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(overlay, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if not (target / "chrome.manifest").exists():
        shutil.copyfile(target / "privacybrowser.manifest", target / "chrome.manifest")
    shutil.copyfile(overlay / "bootstrap.cfg", runtime / "privacybrowser.cfg")
    preferences = runtime / "defaults" / "pref"
    preferences.mkdir(parents=True, exist_ok=True)
    (preferences / "privacybrowser-autoconfig.js").write_text(AUTOCONFIG, encoding="utf-8")
    distribution = runtime / "distribution"
    # Never permit Mozilla's updater to replace a modified developer installation.
    policy_file = distribution / "policies.json"
    policies = json.loads(policy_file.read_text()) if policy_file.exists() else {"policies": {}}
    policies.setdefault("policies", {})["DisableAppUpdate"] = True
    policies["policies"]["BackgroundAppUpdate"] = False
    policy_file.write_text(json.dumps(policies, indent=2) + "\n", encoding="utf-8")


def build(runtime: Path, overlay: Path, output: Path) -> dict:
    runtime, overlay, output = runtime.resolve(), overlay.resolve(), output.resolve()
    binary = next((p for p in (runtime / "firefox", runtime / "firefox.exe", runtime / "privacybrowser", runtime / "privacybrowser.exe") if p.is_file()), None)
    if binary is None or not (runtime / "application.ini").is_file():
        raise ValueError("Runtime must contain a complete Firefox ESR application directory")
    if runtime not in binary.resolve().parents:
        raise ValueError("Runtime executable must resolve inside its application directory")
    if binary.is_symlink() and Path(os.readlink(binary)).is_absolute():
        raise ValueError("Absolute executable symlinks would bypass the private developer runtime")
    if output == runtime or runtime in output.parents or output in runtime.parents:
        raise ValueError("Runtime and output directories must not overlap")
    if output.exists():
        raise ValueError("Output directory already exists; choose a new path")
    overlay_files(overlay)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(runtime, output, symlinks=True)
    install_overlay(output, overlay)
    if binary.suffix.lower() != ".exe":
        launcher = output / "privacy-browser-dev"
        launcher.write_text('''#!/bin/sh
# Local developer runtime. Keep all processes foreground-bound to this app.
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export MOZ_APP_REMOTINGNAME=privacybrowser-dev
profile_root="${XDG_CONFIG_HOME:-$HOME/.config}/privacybrowser-dev/profile"
has_profile=0
for argument in "$@"; do
  case "$argument" in --profile|-profile|--profile=*) has_profile=1 ;; esac
done
if [ "$has_profile" = 1 ]; then
  exec "$app_dir/''' + binary.name + '''" --no-remote "$@"
fi
mkdir -p "$profile_root" || exit 1
exec "$app_dir/''' + binary.name + '''" --no-remote --profile "$profile_root" "$@"
''')
        launcher.chmod(0o755)
    else:
        # Forward URL arguments as PowerShell array elements, never cmd.exe %*.
        (output / "privacy-browser-dev.ps1").write_text(
            '$env:MOZ_APP_REMOTINGNAME = "privacybrowser-dev"\n' 
            '$profilePath = Join-Path $env:LOCALAPPDATA "PrivacyBrowser-Dev\\Profile"\n' 
            'New-Item -ItemType Directory -Force -Path $profilePath | Out-Null\n' 
            '& (Join-Path $PSScriptRoot "' + binary.name + '") --no-remote --profile $profilePath @args\n' 
            'exit $LASTEXITCODE\n' 
        )
    with binary.open("rb") as stream:
        native_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {"product": "Privacy Browser", "version": "0.1.0-dev", "build_kind": "developer-runtime-overlay",
                "source_compiled": False, "native_branding_replaced": False, "official_release": False,
                "native_binary": binary.name, "native_binary_sha256": native_digest,
                "automatic_updates": "disabled; manual verified upstream refresh required",
                "warning": "Developer repack for local testing. Not the independently branded release build."}
    (output / "PRIVACY_BROWSER_BUILD.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, default=ROOT / "browser" / "privacy")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.runtime, args.overlay, args.output), indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        raise SystemExit(f"Developer build failed: {error}")
