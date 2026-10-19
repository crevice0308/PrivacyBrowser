#!/usr/bin/env python3
"""Collect a genuine hosted Windows source candidate; never publish a release.

Default preparation only extracts and statically verifies the fresh package.
The explicit --smoke mode runs the freshly built program for bounded version
and rendering tests, after the installer test establishes owned profile roots.
An interactive UI/privacy acceptance test remains separate and unverified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from urllib.parse import urlsplit
import urllib.request
import zipfile

import verify_windows_native as native

ROOT = Path(__file__).resolve().parents[1]
SETUP_NAME = "PrivacyBrowser-0.1.0-dev-win64-setup-UNSIGNED.exe"
NSIS_URL = "https://github.com/mozilla-releng/adhoc-signing-blobs/raw/nsis/nsis-3.07-signed.zip"
NSIS_SHA256 = "5856cd49b52870caa41814d71ce30c4304d5001b80c04f4eb7028ffedd91df7e"
NSIS_BYTES = 2618390
REQUIRED_INSTALLER_CHECKS = (
    "default_silent_retains_user_data",
    "reinstall_preserves_owned_profile",
    "ownership_marker_purge_refused",
    "directory_handle_blocks_rename",
    "reparse_purge_refused",
    "locked_user_data_purge_retry_safe",
    "explicit_purge_owned_profile_cache_and_registry",
    "foreign_files_profiles_downloads_and_registry_retained",
    "active_browser_uninstall_refused",
    "association_state_unchanged",
)
NSIS_DOWNLOAD_PATHS = {
    ("github.com", "/mozilla-releng/adhoc-signing-blobs/raw/nsis/nsis-3.07-signed.zip"),
    ("raw.githubusercontent.com", "/mozilla-releng/adhoc-signing-blobs/nsis/nsis-3.07-signed.zip"),
    ("raw.githubusercontent.com", "/mozilla-releng/adhoc-signing-blobs/refs/heads/nsis/nsis-3.07-signed.zip"),
}


def trusted_nsis_url(url: str) -> None:
    parsed = urlsplit(url)
    native.require(parsed.scheme == "https" and (parsed.hostname, parsed.path) in NSIS_DOWNLOAD_PATHS
                   and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                   and parsed.port in (None, 443), "Unexpected NSIS download destination")


class NsisRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        trusted_nsis_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download_nsis(destination: Path) -> None:
    """Read only the reviewed Mozilla-owned archive, before any code executes."""
    trusted_nsis_url(NSIS_URL)
    request = urllib.request.Request(NSIS_URL, headers={"User-Agent": "PrivacyBrowser-native-build/0.1"})
    opener = urllib.request.build_opener(NsisRedirects())
    with opener.open(request, timeout=90) as response, destination.open("xb") as output:
        trusted_nsis_url(response.url)
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            native.require(total <= NSIS_BYTES, "Oversized NSIS download")
            output.write(chunk)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def safe_extract_zip(archive: Path, destination: Path, max_bytes: int = 8 * 1024**3) -> None:
    """Inspect the entire archive before extracting any ordinary files."""
    native.require(not destination.exists() and not destination.is_symlink(), "ZIP destination must be new")
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        native.require(0 < len(members) <= 100000, "Invalid ZIP member count")
        native.require(sum(item.file_size for item in members) <= max_bytes, "Oversized ZIP payload")
        seen = set()
        files = set()
        for item in members:
            name = item.filename.rstrip("/") if item.is_dir() else item.filename
            parts = native.safe_relative(name)
            folded = name.casefold()
            native.require(folded not in seen, "Duplicate or case-colliding ZIP members")
            seen.add(folded)
            native.require(not item.flag_bits & 1, "Encrypted ZIP members are forbidden")
            mode = item.external_attr >> 16
            native.require(stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR), "ZIP links/special files are forbidden")
            native.require(all("/".join(parts[:n]).casefold() not in files for n in range(1, len(parts))), "ZIP parent is a file")
            if not item.is_dir():
                native.require(not any(old.startswith(folded + "/") for old in seen), "ZIP file is an existing parent")
                files.add(folded)
        destination.mkdir(parents=True)
        for item in members:
            target = destination.joinpath(*native.safe_relative(item.filename.rstrip("/")))
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(item) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)


def prepare_nsis(work: Path) -> None:
    # Check the exact versioned source archive's definition as well as our pins.
    definition = (work / "source/taskcluster/kinds/fetch/toolchains.yml").read_text(encoding="utf-8")
    match = re.search(r"(?m)^nsis-3\.07-win:\n(?P<body>(?:[ \t].*\n|\n)+)", definition)
    native.require(match is not None, "Pinned ESR NSIS toolchain definition missing")
    block = match.group("body")
    for field, value in (("url", NSIS_URL), ("sha256", NSIS_SHA256), ("size", str(NSIS_BYTES))):
        native.require(re.search(r"(?m)^\s+" + field + r": " + re.escape(value) + r"\s*$", block), "Pinned ESR NSIS " + field + " changed")
    archive = work / "downloads/nsis-3.07-signed.zip"
    native.require(not archive.exists(), "NSIS archive destination already exists")
    download_nsis(archive)
    native.require(archive.stat().st_size == NSIS_BYTES and native.digest(archive) == NSIS_SHA256, "NSIS archive integrity mismatch")
    destination = work / "tools/nsis"
    safe_extract_zip(archive, destination, 64 * 1024**2)
    # The actual ZIP includes a tiny top-level launcher as well as the compiler.
    # Use only the inspected Bin executable and explicitly set NSISDIR in pwsh.
    compiler = destination / "nsis-3.07/Bin/makensis.exe"
    native.require(compiler.is_file(), "Expected pinned NSIS Bin compiler missing")
    write_json(work / "evidence/nsis-toolchain.json", {
        "source_url": NSIS_URL, "archive_sha256": NSIS_SHA256, "archive_bytes": NSIS_BYTES,
        "definition": "verified ESR taskcluster/kinds/fetch/toolchains.yml:nsis-3.07-win",
        "compiler": compiler.relative_to(work).as_posix(),
        "nsis_directory": compiler.parent.parent.relative_to(work).as_posix(),
        "compiler_sha256": native.digest(compiler),
        "licenses_retained_with_toolchain": True,
    })


def record_file(evidence: Path, path: Path) -> dict:
    native.require(path.is_file() and path.stat().st_size > 0, "Missing retained build evidence: " + path.name)
    return {"path": path.relative_to(evidence).as_posix(), "sha256": native.digest(path)}


def run_smoke(argv: list[str], evidence: Path, label: str, timeout: int) -> dict:
    log = evidence / (label + ".log")
    environment = os.environ.copy()
    environment.update(MOZ_CRASHREPORTER_NO_REPORT="1", MOZ_NO_REMOTE="1")
    with log.open("xb") as output:
        output.write((json.dumps({"argv": argv}) + "\n").encode())
        output.flush()
        # The Windows job-level timeout remains a second bound. The browser is
        # launched with a dedicated disposable profile, never the user's data.
        process = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT, env=environment)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False,
                           stdout=output, stderr=subprocess.STDOUT, timeout=20)
            raise ValueError(label + " timed out; candidate will not be staged")
    native.require(code == 0, label + " failed; see " + log.name)
    return {"argv": argv, "exit_code": code, "log": record_file(evidence, log)}


def check_png(path: Path, size: tuple[int, int]) -> None:
    from PIL import Image

    native.require(path.is_file(), "Browser did not produce a screenshot")
    with Image.open(path) as screenshot:
        native.require(screenshot.format == "PNG" and screenshot.size == size, "Invalid headless screenshot")
        screenshot.verify()


def stage_nsis_notice(work: Path, runtime: Path) -> None:
    """Retain the pinned toolchain's complete component notices byte-for-byte."""
    source = native.regular_file(work, "tools/nsis/nsis-3.07/COPYING")
    native.require(source.stat().st_size > 0, "Pinned NSIS component license file is empty")
    destination = runtime / "NSIS-COPYING.txt"
    native.require(not destination.exists() and not destination.is_symlink(), "NSIS component notice destination must be new")
    with source.open("rb") as input_stream, destination.open("xb") as output:
        shutil.copyfileobj(input_stream, output)


def prepare_candidate(work: Path) -> None:
    """Prepare evidence and statically inspect files; never start the browser."""
    config = json.loads((ROOT / "config/esr.json").read_text(encoding="utf-8"))
    upstream = config["upstream_version"]
    evidence = work / "evidence"
    commands = native.load_json(evidence / "commands.json")
    expected = [["./mach", "build"], ["./mach", "package"],
                ["./mach", "xpcshell-test", "toolkit/components/places/tests/unit/test_isURIVisited.js"]]
    native.require(commands.get("commands") == [{"argv": argv, "exit_code": 0} for argv in expected], "Missing successful native build/package/test result")
    packages = sorted((work / "source/obj-privacybrowser/dist").glob("*.zip"))
    # Reject tests/symbol archives and require one real new packaged application.
    candidates = []
    for package in packages:
        with zipfile.ZipFile(package) as archive:
            if any(name.casefold().endswith("/privacybrowser.exe") or name.casefold() == "privacybrowser.exe"
                   for name in archive.namelist()):
                candidates.append(package)
    native.require(len(candidates) == 1, "Expected exactly one mach-produced native package ZIP")
    package = candidates[0]
    unpacked = work / "unpacked"
    safe_extract_zip(package, unpacked)
    applications = list(unpacked.rglob("privacybrowser.exe"))
    native.require(len(applications) == 1, "Expected one native application executable in mach package")
    original = applications[0].parent
    native.require(original == unpacked or (original.parent == unpacked and len(list(unpacked.iterdir())) == 1), "Unexpected native package layout")
    runtime = work / "candidate/PrivacyBrowser"
    runtime.parent.mkdir()
    shutil.move(str(original), runtime)
    # Retain upstream package legal resources unchanged and add the project's
    # complete LICENSE/NOTICE before calculating the immutable payload inventory.
    for filename in ("LICENSE", "NOTICE"):
        source, destination = ROOT / filename, runtime / filename
        if destination.exists():
            native.require(destination.read_bytes() == source.read_bytes(), "Refusing to overwrite upstream " + filename)
        else:
            shutil.copyfile(source, destination)
    # Our setup embeds NSIS/LZMA components. Include their complete notices
    # before inventory hashing without changing any project/upstream notice.
    stage_nsis_notice(work, runtime)
    proof = {"schema_version": 1, "build_kind": "full-source-build", "source_compiled": True,
             "official_release": False, "target": "windows", "upstream_version": upstream,
             "native_binary": "privacybrowser.exe", "files": native.inventory(runtime),
             "commands": commands["commands"], "created_utc": datetime.now(timezone.utc).isoformat(),
             "commit": os.environ["PB_EXPECTED_COMMIT"],
             "workflow_run": "https://github.com/" + os.environ["GITHUB_REPOSITORY"] + "/actions/runs/" + os.environ["GITHUB_RUN_ID"],
             "mach_package": {"name": package.name, "sha256": native.digest(package)},
             "preparation": record_file(evidence, evidence / "source-preparation.json"),
             "source_verification": record_file(evidence, evidence / "source-verification.json"),
             "build_log": record_file(evidence, evidence / "build.log"),
             "package_log": record_file(evidence, evidence / "package.log"),
             "xpcshell_log": record_file(evidence, evidence / "xpcshell.log")}
    build_evidence = evidence / "build-evidence.json"
    write_json(build_evidence, proof)
    report = native.validate_candidate(runtime, build_evidence)
    write_json(evidence / "native-static-checks.json", report)


def smoke_candidate(work: Path) -> None:
    """Called by the installer test only after its first fresh successful install.

    Even --profile cannot redirect every Windows known-folder write. The caller
    first verifies ownership markers in the real per-user PrivacyBrowser roots,
    then invokes this mode before testing complete removal by the uninstaller.
    """
    evidence = work / "evidence"
    runtime = work / "candidate/PrivacyBrowser"
    build_evidence = evidence / "build-evidence.json"
    proof = native.load_json(build_evidence)
    native.validate_candidate(runtime, build_evidence)
    static = native.load_json(evidence / "native-static-checks.json")
    native.require(static.get("static_candidate_checks_passed") is True
                   and static.get("native_binary_sha256") == proof["files"]["privacybrowser.exe"],
                   "Matching successful static preparation is required before runtime smoke")
    upstream = proof["upstream_version"]
    binary = str(runtime / "privacybrowser.exe")
    results = [run_smoke([binary, "--version"], evidence, "windows-version", 30)]
    # Ignore the recorded argv line when validating the actual program's text.
    version_text = (evidence / "windows-version.log").read_text(encoding="utf-8", errors="replace").split("\n", 1)[1]
    native.require("PrivacyBrowser" in version_text and upstream.removesuffix("esr") in version_text
                   and "Firefox" not in version_text, "Native --version output has wrong identity/version")
    screenshots = []
    local_page = evidence / "rendering-smoke.html"
    local_page.write_text('<!doctype html><meta charset="utf-8"><title>PrivacyBrowser native smoke</title>'
                          '<style>html,body{margin:0;background:rgb(17,121,83);color:white}</style>'
                          '<h1>PrivacyBrowser native rendering smoke</h1>', encoding="utf-8")
    for label, url in (("windows-local-render", local_page.as_uri()),
                       ("windows-privacybrowser-home", "chrome://privacybrowser/content/home.html")):
        profile = work / (label + "-profile")
        profile.mkdir()
        screenshot = evidence / (label + ".png")
        results.append(run_smoke([binary, "--headless", "--no-remote", "--profile", str(profile),
                                  "--screenshot", str(screenshot), "--window-size", "800,600", url],
                                 evidence, label, 120))
        check_png(screenshot, (800, 600))
        if label == "windows-local-render":
            from PIL import Image
            with Image.open(screenshot) as rendered:
                native.require(rendered.convert("RGB").getpixel((700, 500)) == (17, 121, 83), "Local page did not render expected content")
        screenshots.append(record_file(evidence, screenshot))
    # Browser startup must not mutate packaged application files.
    native.require(native.inventory(runtime) == proof["files"], "Runtime payload changed during smoke tests")
    write_json(evidence / "windows-runtime-smoke.json", {
        "schema_version": 1, "windows_smoke_checks_passed": True,
        "native_binary_sha256": proof["files"]["privacybrowser.exe"], "commands": results,
        "screenshots": screenshots, "tested": ["version identity", "local HTML headless rendering", "custom chrome home screenshot capture only"],
        "custom_home_content_verified": False,
        "interactive_windows_ui_tested": False, "install_uninstall_tested": False,
        "privacy_security_acceptance_tested": False, "release_eligible": False,
        "limitation": "The custom home screenshot needs visual review; an image alone does not prove its content loaded. Smoke tests do not validate normal browsing, Explorer display, installer behavior, persistence, privacy or security requirements."})


def validate_installer_test(report: dict, installer_sha256: str, native_binary_sha256: str) -> None:
    """Require every bounded install/uninstall scenario, not a summary flag."""
    native.require(type(report.get("schema_version")) is int and report["schema_version"] == 1
                   and report.get("passed") is True
                   and report.get("installer_sha256") == installer_sha256
                   and report.get("native_binary_sha256") == native_binary_sha256
                   and report.get("release_eligible") is False,
                   "Missing matching passed per-user install/uninstall tests")
    checks = report.get("checks")
    native.require(isinstance(checks, dict), "Missing detailed install/uninstall checks")
    for key in REQUIRED_INSTALLER_CHECKS:
        native.require(checks.get(key) is True, "Required install/uninstall check did not pass: " + key)


def stage(work: Path) -> None:
    evidence = work / "evidence"
    runtime = work / "candidate/PrivacyBrowser"
    proof = evidence / "build-evidence.json"
    native.validate_candidate(runtime, proof)
    smoke = native.load_json(evidence / "windows-runtime-smoke.json")
    native.require(smoke.get("windows_smoke_checks_passed") is True and
                   smoke.get("native_binary_sha256") == native.digest(runtime / "privacybrowser.exe"), "Missing matching passed Windows smoke tests")
    setup = work / "candidate" / SETUP_NAME
    native.validate_installer(setup)
    install_test = native.load_json(evidence / "windows-installer-test.json")
    validate_installer_test(install_test, native.digest(setup), native.digest(runtime / "privacybrowser.exe"))
    for suffix in (".json", ".build.log"):
        native.require(Path(str(setup) + suffix).is_file(), "Missing native installer evidence")
    output = work / "artifact"
    native.require(not output.exists(), "Artifact output already exists")
    output.mkdir()
    shutil.copytree(evidence, output / "evidence")
    # ZIP payload contains the native executable and all runtime dependencies;
    # no upstream installer or developer-overlay binary can enter this directory.
    packaged = output / "PrivacyBrowser-0.1.0-dev-win64-UNSIGNED.zip"
    with zipfile.ZipFile(packaged, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for relative in native.inventory(runtime):
            archive.write(runtime / relative, "PrivacyBrowser/" + relative)
    for path in (setup, Path(str(setup) + ".json"), Path(str(setup) + ".build.log")):
        shutil.copyfile(path, output / path.name)
    (output / "UNPUBLISHED-CANDIDATE.txt").write_text(
        "PrivacyBrowser unsigned, unpublished full-source Windows candidate.\n"
        "Native PE identity/icons, build/package logs, a Places xpcshell smoke test, "
        "--version, local headless rendering and bounded per-user install/uninstall "
        "checks passed in the workflow. The custom-home screenshot still needs visual review.\n"
        "Interactive UI, Explorer display, broader "
        "privacy/security acceptance and independent signing remain unverified.\n"
        "This is not an official release. The production release gate remains closed.\n", encoding="utf-8")
    hashes = native.inventory(output)
    (output / "SHA256SUMS").write_text("".join(checksum + "  " + name + "\n" for name, checksum in hashes.items()), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--stage", action="store_true")
    modes.add_argument("--prepare-nsis", action="store_true")
    modes.add_argument("--smoke", action="store_true",
                       help="Installer-test callback: run only after fresh installation establishes owned profile roots")
    args = parser.parse_args()
    native.require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
                   and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted", "Only ephemeral GitHub-hosted Windows execution is supported")
    work = args.work_root.resolve()
    native.require(str(work).lower() == "d:\\pb" and not work.is_symlink(), "Expected isolated D:\\pb workspace")
    (stage if args.stage else prepare_nsis if args.prepare_nsis else smoke_candidate if args.smoke else prepare_candidate)(work)


if __name__ == "__main__":
    main()
