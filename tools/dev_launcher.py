#!/usr/bin/env python3
"""Run the experimental chrome with a separately obtained Firefox ESR runtime.

The system browser is never modified. A writable private runtime copy is created
under the user's cache, and a separate Privacy Browser profile is used.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from build_dev import build, overlay_files


def find_runtime(supplied: Path | None) -> Path:
    candidates = [supplied] if supplied else []
    configured = os.environ.get("PRIVACY_BROWSER_RUNTIME")
    if configured:
        candidates.append(Path(configured))
    candidates += [Path("/usr/lib/firefox-esr"), Path("/usr/lib64/firefox-esr"), Path("/opt/firefox-esr")]
    for candidate in candidates:
        if candidate and (candidate / "application.ini").is_file():
            return candidate.resolve()
    raise ValueError("Firefox ESR runtime not found. Obtain the pinned ESR from Mozilla, then use --runtime DIRECTORY or PRIVACY_BROWSER_RUNTIME. This developer package contains no Gecko binary.")


def prepare(runtime: Path, project: Path, cache: Path) -> Path:
    config = configparser.ConfigParser()
    config.read(runtime / "application.ini")
    version = config.get("App", "Version", fallback="")
    repository = config.get("App", "SourceRepository", fallback="")
    expected = json.loads((project / "config/esr.json").read_text())["upstream_version"].removesuffix("esr")
    if version != expected or "/releases/mozilla-esr" not in repository:
        raise ValueError(f"This overlay is pinned to Firefox ESR {expected}; the supplied runtime has not been validated for this developer version")
    overlay = project / "browser/privacy"
    digest = hashlib.sha256((runtime / "application.ini").read_bytes())
    digest.update(str(runtime).encode())
    for name in ("build_dev.py", "dev_launcher.py"):
        digest.update(Path(__file__).with_name(name).read_bytes())
    for path in overlay_files(overlay):
        digest.update(path.relative_to(overlay).as_posix().encode())
        digest.update(path.read_bytes())
    key = digest.hexdigest()[:20]
    destination = cache / ("runtime-" + key)
    if not (destination / "PRIVACY_BROWSER_BUILD.json").is_file():
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="assemble-", dir=cache) as temporary:
            temporary_output = Path(temporary) / "runtime"
            build(runtime, overlay, temporary_output)
            try:
                temporary_output.rename(destination)
            except OSError:
                if not (destination / "PRIVACY_BROWSER_BUILD.json").is_file():
                    raise
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--prepare-only", action="store_true")
    args, browser_args = parser.parse_known_args()
    if browser_args[:1] == ["--"]:
        browser_args = browser_args[1:]
    runtime = find_runtime(args.runtime)
    if sys.platform == "win32":
        cache = Path(os.environ["LOCALAPPDATA"]) / "PrivacyBrowser-Dev/Cache"
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "privacybrowser-dev"
    prepared = prepare(runtime, args.project, cache)
    if args.prepare_only:
        print(prepared)
        return 0
    if sys.platform == "win32":
        # Launch the binary directly so a web URL cannot become cmd.exe syntax.
        evidence = json.loads((prepared / "PRIVACY_BROWSER_BUILD.json").read_text())
        profile = Path(os.environ["LOCALAPPDATA"]) / "PrivacyBrowser-Dev/Profile"
        profile.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["MOZ_APP_REMOTINGNAME"] = "privacybrowser-dev"
        has_profile = any(arg in ("-profile", "--profile") or arg.startswith("--profile=") for arg in browser_args)
        defaults = [] if has_profile else ["-profile", str(profile)]
        return subprocess.run([str(prepared / evidence["native_binary"]), "-no-remote", *defaults, *browser_args], env=environment, check=False).returncode
    launcher = prepared / "privacy-browser-dev"
    os.execv(str(launcher), [str(launcher), *browser_args])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        raise SystemExit(f"Privacy Browser developer launcher: {error}")
