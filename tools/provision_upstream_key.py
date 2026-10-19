#!/usr/bin/env python3
"""Download a public key, accept only reviewed primary and current subkey pins."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time

from update_esr import download

ROOT = Path(__file__).resolve().parents[1]
KEY_URL = "https://archive.mozilla.org/pub/firefox/nightly/latest-mozilla-central/KEY"


def provision(output: Path, trust_file: Path) -> None:
    trust = json.loads(trust_file.read_text())
    primaries = set(trust["mozilla_release_key_fingerprints"])
    subkeys = set(trust["mozilla_release_signing_subkey_fingerprints"])
    if not primaries or not subkeys:
        raise ValueError("Reviewed primary and signing-subkey pins are required")
    with tempfile.TemporaryDirectory(prefix="privacybrowser-key-") as temporary:
        home = Path(temporary)
        key = home / "KEY"
        download(KEY_URL, key, 2 * 1024 * 1024)
        command = ["gpg", "--no-options", "--batch", "--homedir", str(home)]
        listing = subprocess.run(command + ["--with-colons", "--show-keys", str(key)], check=True, capture_output=True, text=True)
        found_primaries, found_subkeys = set(), set()
        previous = None
        for line in listing.stdout.splitlines():
            values = line.split(":")
            if values[0] in ("pub", "sub"):
                expiration = int(values[6] or 0)
                previous = (values[0], values[1] not in ("r", "e", "d") and (not expiration or expiration > time.time()))
            elif values[0] == "fpr" and previous:
                kind, valid = previous
                if valid:
                    (found_primaries if kind == "pub" else found_subkeys).add(values[9])
                previous = None
        if not (primaries & found_primaries) or not (subkeys & found_subkeys):
            raise ValueError("Official key download does not match current independently reviewed identity pins")
        # Public-key verification needs no trust database or secret-key agent.
        # Dearmor only after checking identity pins, then verify_signature() also
        # restricts the exact primary AND current signing subkey on every use.
        keyring = home / "verified-public-key.gpg"
        subprocess.run(command + ["--dearmor", "--output", str(keyring), str(key)], check=True, capture_output=True)
        exported = keyring.read_bytes()
        if not exported:
            raise ValueError("No pinned public key was exported")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(exported)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trust", type=Path, default=ROOT / "config/update-trust.json")
    args = parser.parse_args()
    provision(args.output, args.trust)
