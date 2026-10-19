#!/usr/bin/env python3
"""Safely extract an already signature-verified upstream source archive."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile


def extract(archive: Path, evidence: Path, destination: Path) -> None:
    proof = json.loads(evidence.read_text())
    if proof.get("signature_verified") is not True or proof.get("kind") != "source" or proof.get("archive") != archive.name:
        raise ValueError("Expected signed source archive verification evidence")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha512").hexdigest()
    if digest != proof.get("sha512"):
        raise ValueError("Source archive changed since signature verification")
    if destination.exists():
        raise ValueError("Extraction destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tar:
        roots = set()
        for member in tar.getmembers():
            # No source code file needs device nodes or links outside its tree.
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or member.isdev() or member.isfifo():
                raise ValueError("Unsafe source archive member")
            if path.parts:
                roots.add(path.parts[0])
        if len(roots) != 1:
            raise ValueError("Expected one top-level Firefox source directory")
        # Archive directory names need not include the 'esr' version suffix.
        # Flatten the verified single top-level directory into the exact caller
        # destination instead of guessing its upstream spelling.
        with tempfile.TemporaryDirectory(prefix="privacybrowser-source-", dir=destination.parent) as temporary:
            tar.extractall(temporary, filter="data")
            unpacked = Path(temporary) / next(iter(roots))
            if not unpacked.is_dir() or not (unpacked / "mach").is_file():
                raise ValueError("Verified archive does not contain a Firefox source checkout")
            os.replace(unpacked, destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    extract(args.archive, args.verification, args.destination)
