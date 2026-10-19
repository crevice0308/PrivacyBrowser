#!/usr/bin/env python3
"""Observe ESR versions and fetch archives only after pinned-signature verification.

Metadata is advisory; it is never trusted as an authenticity proof. To fetch an
archive, supply a locally provisioned release keyring and an independently trusted
full primary-key fingerprint. No key is downloaded or trusted automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, build_opener

VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?esr$")
FINGERPRINT_RE = re.compile(r"^(?:[A-F0-9]{40}|[A-F0-9]{64})$")
ALLOWED_HOSTS = {"archive.mozilla.org", "product-details.mozilla.org"}
METADATA_URL = "https://product-details.mozilla.org/1.0/firefox_versions.json"


def run_gpg(arguments: list[str], home: Path, operation: str) -> subprocess.CompletedProcess:
    """Use one isolated cwd and relative filenames with native or MSYS GPG.

    Native Python supplies cwd through the Windows process API. Avoid passing
    drive-letter/backslash paths through a POSIX GPG argument parser altogether.
    The explicit homedir also prevents reading a personal/default key store.
    """
    executable = shutil.which("gpg") or "gpg"
    command = [executable, "--no-options", "--batch", "--no-tty", "--homedir", ".", *arguments]
    try:
        result = subprocess.run(command, cwd=home, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        detail = {"operation": operation, "executable": executable,
                  "error_type": type(error).__name__, "timed_out": isinstance(error, subprocess.TimeoutExpired)}
        raise ValueError("GPG invocation failed: " + json.dumps(detail, ensure_ascii=True)) from error
    if result.returncode:
        # Escape newlines/control characters so public-key text cannot become
        # a runner command. Bound diagnostics and never dump keys/environment.
        detail = {"operation": operation, "executable": executable, "exit_code": result.returncode,
                  "stderr": (result.stderr or "")[:4096], "stderr_truncated": len(result.stderr or "") > 4096}
        raise ValueError("GPG operation failed: " + json.dumps(detail, ensure_ascii=True))
    return result


def trusted_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Only allowlisted official Mozilla HTTPS URLs are accepted")
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        trusted_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, destination: Path, maximum_bytes: int) -> None:
    trusted_url(url)
    opener = build_opener(SafeRedirect())
    total = 0
    with opener.open(url, timeout=60) as response, destination.open("wb") as output:
        trusted_url(response.url)
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError("Download exceeded the size limit")
            output.write(chunk)


def verify_signature(checksums: Path, signature: Path, keyring: Path, fingerprints: list[str], signing_fingerprints: list[str]) -> str:
    pins = {pin.replace(" ", "").upper() for pin in fingerprints}
    if not pins or any(not FINGERPRINT_RE.fullmatch(pin) for pin in pins):
        raise ValueError("At least one independently verified full signing-key fingerprint is required")
    signing_pins = {pin.replace(" ", "").upper() for pin in signing_fingerprints}
    if not signing_pins or any(not FINGERPRINT_RE.fullmatch(pin) for pin in signing_pins):
        raise ValueError("At least one independently verified current signing-subkey fingerprint is required")
    if not keyring.is_file():
        raise ValueError("A pre-provisioned trusted keyring is required")
    with tempfile.TemporaryDirectory(prefix="privacybrowser-gpg-") as home:
        os.chmod(home, 0o700)
        isolated = Path(home)
        # Use byte-identical public inputs with fixed relative names. An MSYS
        # executable need not interpret any native Windows filename argument.
        for source, name in ((keyring, "trusted.gpg"), (signature, "checksums.sig"), (checksums, "checksums.txt")):
            shutil.copyfile(source, isolated / name)
        result = run_gpg(["--no-default-keyring", "--keyring", "./trusted.gpg", "--status-fd", "1",
                          "--verify", "checksums.sig", "checksums.txt"], isolated, "verify-checksum-signature")
    valid = []
    # KEYEXPIRED can describe unrelated historical subkeys in the same Mozilla
    # public-key bundle. EXPKEYSIG/REVKEYSIG describe the actual signing key.
    forbidden_statuses = {"BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "KEYREVOKED", "SIGEXPIRED", "NO_PUBKEY"}
    rejected_status = False
    for line in result.stdout.splitlines():
        if line.startswith("[GNUPG:] ") and line.split()[1] in forbidden_statuses:
            rejected_status = True
        if line.startswith("[GNUPG:] VALIDSIG "):
            fields = line.split()
            if len(fields) < 11 or fields[9] not in {"8", "9", "10", "11"}:
                rejected_status = True
                continue
            # VALIDSIG's final field is the primary fingerprint when a subkey signs.
            signing = fields[2]
            primary = fields[-1] if FINGERPRINT_RE.fullmatch(fields[-1]) else signing
            valid.append((primary, signing))
    if result.returncode or rejected_status or not any(primary in pins and signing in signing_pins for primary, signing in valid):
        raise ValueError("Checksum signature did not validate against a pinned primary signing key")
    return next(primary for primary, signing in valid if primary in pins and signing in signing_pins)


def checksum_for(checksums: str, archive_path: str) -> str:
    path = PurePosixPath(archive_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe archive path")
    matches = []
    for line in checksums.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].lstrip("*") == archive_path:
            if not re.fullmatch(r"[a-fA-F0-9]{128}", parts[0]):
                raise ValueError("Expected a SHA-512 checksum")
            matches.append(parts[0].lower())
    if len(matches) != 1:
        raise ValueError("Archive must have exactly one signed SHA-512 checksum")
    return matches[0]


def archive_path(version: str, kind: str) -> str:
    if not VERSION_RE.fullmatch(version):
        raise ValueError("Invalid stable ESR version")
    if kind == "source":
        return f"source/firefox-{version}.source.tar.xz"
    if kind == "linux":
        return f"linux-x86_64/en-US/firefox-{version}.tar.xz"
    if kind == "windows":
        return f"win64/en-US/Firefox Setup {version}.exe"
    raise ValueError("Unknown archive kind")


def fetch_verified(version: str, kind: str, destination: Path, keyring: Path, fingerprints: list[str], signing_fingerprints: list[str]) -> dict:
    relative = archive_path(version, kind)
    destination.mkdir(parents=True, exist_ok=True)
    root = f"https://archive.mozilla.org/pub/firefox/releases/{version}/"
    with tempfile.TemporaryDirectory(prefix="privacybrowser-upstream-", dir=destination) as temp:
        temporary = Path(temp)
        sums, signature = temporary / "SHA512SUMS", temporary / "SHA512SUMS.asc"
        download(root + "SHA512SUMS", sums, 32 * 1024 * 1024)
        download(root + "SHA512SUMS.asc", signature, 1024 * 1024)
        signer = verify_signature(sums, signature, keyring, fingerprints, signing_fingerprints)
        expected = checksum_for(sums.read_text(encoding="utf-8"), relative)
        archive = temporary / Path(relative).name
        from urllib.parse import quote
        download(root + quote(relative), archive, 2 * 1024**3)
        with archive.open("rb") as archive_file:
            digest = hashlib.file_digest(archive_file, "sha512").hexdigest()
        if digest != expected:
            raise ValueError("Archive SHA-512 mismatch")
        output = destination / archive.name
        os.replace(archive, output)
    evidence = {"upstream_version": version, "kind": kind, "archive": output.name, "sha512": digest, "checksum_signer": signer,
                "signature_verified": True, "source_url": root + relative}
    (destination / (output.name + ".verification.json")).write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--current", type=Path, default=Path("config/esr.json"))
    check.add_argument("--output", type=Path)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--version", required=True)
    fetch.add_argument("--kind", choices=("source", "linux", "windows"), default="source")
    fetch.add_argument("--destination", type=Path, required=True)
    fetch.add_argument("--keyring", type=Path, required=True)
    fetch.add_argument("--fingerprint", action="append", required=True)
    fetch.add_argument("--signing-fingerprint", action="append", required=True)
    args = parser.parse_args()
    if args.command == "fetch":
        print(json.dumps(fetch_verified(args.version, args.kind, args.destination, args.keyring, args.fingerprint, args.signing_fingerprint), indent=2))
        return 0
    with tempfile.TemporaryDirectory() as temporary:
        data_path = Path(temporary) / "versions.json"
        download(METADATA_URL, data_path, 1024 * 1024)
        version = json.loads(data_path.read_text())["FIREFOX_ESR"]
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ValueError("Metadata did not contain a stable ESR version")
    current = json.loads(args.current.read_text())["upstream_version"]
    evidence = {"configured_version": current, "observed_version": version, "update_available": version != current,
                "metadata_url": METADATA_URL, "authenticity": "advisory HTTPS metadata; signed archive verification required"}
    rendered = json.dumps(evidence, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f"ESR update refused: {error}")
