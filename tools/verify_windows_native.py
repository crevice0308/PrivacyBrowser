#!/usr/bin/env python3
"""Statically validate an unsigned PrivacyBrowser source-built Windows candidate.

No executable, installer, DLL, or source build is loaded or run. This is separate
from package_windows.py, which only accepts Firefox-based developer overlays.
The retained evidence is integrity-checked, not a reproducible-build attestation:
JSON flags and build logs cannot independently prove where a binary was compiled.
A successful report is NOT release, signing, installation, or runtime approval.
"""
from __future__ import annotations

import argparse
from collections import Counter
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "{ec8030f7-c20a-464f-9b0e-13a3a9e97384}"
BINARY = "privacybrowser.exe"
SETUP_NAME = "PrivacyBrowser-0.1.0-dev-win64-setup-UNSIGNED.exe"
MAX_PE_BYTES = 128 * 1024 * 1024
MAX_RESOURCE_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
SAFE_PART = re.compile(r"[A-Za-z0-9 ._+()@=-]+\Z")
DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?\Z", re.I)
REQUIRED = (
    BINARY, "xul.dll", "mozglue.dll", "nss3.dll", "application.ini",
    "platform.ini", "omni.ja", "browser/omni.ja", "dependentlibs.list",
    "privacybrowser.cfg", "defaults/pref/privacybrowser-autoconfig.js",
    "distribution/privacybrowser/chrome.manifest",
    "distribution/privacybrowser/assets/privacybrowser.ico",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_relative(value: str) -> tuple[str, ...]:
    require(isinstance(value, str) and bool(value), "Missing relative evidence/payload path")
    parts = value.split("/")
    require(all(part not in ("", ".", "..") and SAFE_PART.fullmatch(part)
                and not DEVICE.fullmatch(part) and not part.endswith((" ", "."))
                for part in parts), "Unsafe Windows evidence/payload path")
    return tuple(parts)


def is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def regular_file(root: Path, relative: str) -> Path:
    path = root
    for part in safe_relative(relative):
        path /= part
        require(not is_link_or_reparse(path), "Symlinks/reparse points are forbidden in evidence/payload paths")
    require(path.is_file() and stat.S_ISREG(path.stat().st_mode), "Missing regular file: " + relative)
    require(root.resolve() in path.resolve().parents, "File escaped its root")
    return path


def inventory(runtime: Path) -> dict[str, str]:
    require(not is_link_or_reparse(runtime) and runtime.is_dir(), "Runtime must be a real directory")
    files: dict[str, str] = {}
    seen = set()
    def traversal_error(error):
        raise error

    for directory, dirs, names in os.walk(runtime, followlinks=False, onerror=traversal_error):
        for name in sorted(dirs + names):
            path = Path(directory) / name
            relative = path.relative_to(runtime).as_posix()
            safe_relative(relative)
            require(relative.casefold() not in seen, "Case-colliding Windows payload paths")
            seen.add(relative.casefold())
            require(not is_link_or_reparse(path), "Symlinks/reparse points are forbidden in Windows payloads")
            mode = path.stat().st_mode
            require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), "Non-regular Windows payload entry")
            if stat.S_ISREG(mode):
                files[relative] = digest(path)
    return dict(sorted(files.items()))


def load_json(path: Path) -> dict:
    require(path.stat().st_size <= MAX_JSON_BYTES, "JSON evidence exceeds size limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON evidence key")
            result[key] = value
        return result

    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    require(isinstance(result, dict), "Expected a JSON evidence object")
    return result


def take(data: bytes, offset: int, length: int) -> bytes:
    require(offset >= 0 and length >= 0 and offset <= len(data) - length, "Truncated or out-of-range binary data")
    return data[offset:offset + length]


def unpack(fmt: str, data: bytes, offset: int = 0) -> tuple:
    return struct.unpack(fmt, take(data, offset, struct.calcsize(fmt)))


def aligned(value: int) -> int:
    return (value + 3) & ~3


class PE:
    """Bounded PE resource reader; AMD64 by default, explicit i386 for NSIS only."""

    def __init__(self, data: bytes, expected_machine: int = 0x8664):
        require(len(data) <= MAX_PE_BYTES, "PE exceeds static inspection size limit")
        self.data = data
        require(take(data, 0, 2) == b"MZ", "Missing DOS executable header")
        pe = unpack("<I", data, 0x3C)[0]
        require(pe >= 64 and take(data, pe, 4) == b"PE\0\0", "Invalid PE header")
        machine, count, _, _, _, optional_size, flags = unpack("<HHIIIHH", data, pe + 4)
        require(expected_machine in (0x8664, 0x14C) and machine == expected_machine and 1 <= count <= 96, "Windows executable architecture mismatch")
        require(flags & 2 and not flags & 0x2000, "Expected an executable image, not a DLL")
        optional = take(data, pe + 24, optional_size)
        directory_offset = 112 if expected_machine == 0x8664 else 96
        magic = 0x20B if expected_machine == 0x8664 else 0x10B
        require(optional_size >= directory_offset + 24 and unpack("<H", optional)[0] == magic, "PE optional header architecture mismatch")
        directories = unpack("<I", optional, directory_offset - 4)[0]
        require(3 <= directories <= (optional_size - directory_offset) // 8, "Invalid PE data directories")
        resource_rva, resource_size = unpack("<II", optional, directory_offset + 16)
        require(resource_rva and 16 <= resource_size <= MAX_RESOURCE_BYTES, "Missing or oversized native resources")
        table = pe + 24 + optional_size
        header_end = table + count * 40
        take(data, table, count * 40)
        self.sections = []
        for index in range(count):
            entry = table + index * 40
            virtual_size, address, raw_size, raw = unpack("<IIII", data, entry + 8)
            if raw_size:
                require(raw >= header_end, "PE section overlaps executable headers")
                take(data, raw, raw_size)
            require(address + max(virtual_size, raw_size) <= 2**32, "PE section address overflows")
            for old_address, old_virtual, old_raw, old_size in self.sections:
                require(not (raw_size and old_size and raw < old_raw + old_size and old_raw < raw + raw_size), "Overlapping PE raw sections")
                require(not (max(virtual_size, raw_size) and old_virtual and address < old_address + old_virtual
                             and old_address < address + max(virtual_size, raw_size)), "Overlapping PE virtual sections")
            self.sections.append((address, max(virtual_size, raw_size), raw, raw_size))
        resource_offset = self.rva_offset(resource_rva, resource_size)
        self.resource_rva = resource_rva
        self.resource_data = take(data, resource_offset, resource_size)
        self.resources: dict[tuple, bytes] = {}
        self._visited: set[int] = set()
        self._entries = 0
        self._leaf_bytes = 0
        self._walk(0, ())

    def rva_offset(self, address: int, size: int) -> int:
        matches = [raw + address - start for start, _, raw, raw_size in self.sections
                   if start <= address and size <= raw_size and address - start <= raw_size - size]
        require(len(matches) == 1, "Resource address is outside unambiguous file-backed PE data")
        return matches[0]

    def _name(self, value: int):
        if not value & 0x80000000:
            require(value <= 0xFFFF, "Invalid numeric resource ID")
            return value
        offset = value & 0x7FFFFFFF
        length = unpack("<H", self.resource_data, offset)[0]
        require(0 < length <= 1024, "Invalid resource name length")
        name = take(self.resource_data, offset + 2, length * 2).decode("utf-16le")
        require("\0" not in name, "NUL in resource name")
        return name

    def _walk(self, offset: int, parents: tuple) -> None:
        require(offset % 4 == 0 and offset not in self._visited, "Cyclic, shared, or unaligned resource directory")
        self._visited.add(offset)
        require(len(self._visited) <= 8192 and len(parents) < 3, "Resource directory exceeds limits")
        _, _, _, _, named, numeric = unpack("<IIHHHH", self.resource_data, offset)
        count = named + numeric
        self._entries += count
        require(0 < count and self._entries <= 16384, "Empty or oversized resource directory")
        take(self.resource_data, offset + 16, count * 8)
        names = set()
        for index in range(count):
            raw_name, child = unpack("<II", self.resource_data, offset + 16 + index * 8)
            require(bool(raw_name & 0x80000000) == (index < named), "Malformed named resource ordering")
            name = self._name(raw_name)
            require(name not in names, "Duplicate resource ID/name")
            names.add(name)
            path = parents + (name,)
            if len(path) < 3:
                require(child & 0x80000000, "Resource tree ended before language level")
                self._walk(child & 0x7FFFFFFF, path)
            else:
                require(isinstance(name, int) and not child & 0x80000000 and child % 4 == 0, "Invalid resource language leaf")
                address, size, _, reserved = unpack("<IIII", self.resource_data, child)
                require(reserved == 0 and 0 < size <= MAX_RESOURCE_BYTES, "Invalid resource leaf")
                self._leaf_bytes += size
                require(self._leaf_bytes <= MAX_RESOURCE_BYTES, "Aggregate resource data exceeds inspection limit")
                # Require leaves within the declared resource directory span.
                self.resources[path] = take(self.resource_data, address - self.resource_rva, size)


def icon_frames(icon: bytes) -> list[tuple]:
    reserved, kind, count = unpack("<HHH", icon)
    require(reserved == 0 and kind == 1 and 1 <= count <= 256, "Invalid reference ICO")
    take(icon, 6, count * 16)
    frames = []
    intervals = []
    for index in range(count):
        width, height, colors, reserved, planes, bits, size, offset = unpack("<BBBBHHII", icon, 6 + index * 16)
        require(reserved == 0 and planes in (0, 1) and bits > 0 and size > 0 and offset >= 6 + count * 16, "Invalid ICO frame")
        for start, end in intervals:
            require(offset >= end or offset + size <= start, "Overlapping ICO frames")
        intervals.append((offset, offset + size))
        payload = take(icon, offset, size)
        # PNG-format ICO frames permit planes=0; resource compilers may normalize to 1.
        frames.append((width, height, colors, bits, size, hashlib.sha256(payload).hexdigest()))
    require(len(set(frames)) == len(frames), "Duplicate reference ICO frames")
    return frames


def verify_icons(pe: PE, icon: bytes) -> dict:
    expected = Counter(icon_frames(icon))
    groups = {path: value for path, value in pe.resources.items() if path[0] == 14}
    images = {path: value for path, value in pe.resources.items() if path[0] == 3}
    require(groups and images, "Native RT_GROUP_ICON/RT_ICON resources are missing")
    used = set()
    reports = []
    for path, group in groups.items():
        reserved, kind, count = unpack("<HHH", group)
        require(reserved == 0 and kind == 1 and count == sum(expected.values()) and len(group) == 6 + 14 * count, "Native icon group differs from reference ICO")
        actual = []
        for index in range(count):
            width, height, colors, reserved, planes, bits, size, image_id = unpack("<BBBBHHIH", group, 6 + index * 14)
            require(reserved == 0 and planes in (0, 1), "Malformed native icon frame")
            key = (3, image_id, path[2])
            if key not in images:
                key = (3, image_id, 0)
            require(key in images, "Native icon frame has no same-language or neutral image")
            payload = images[key]
            require(len(payload) == size, "Native icon frame size mismatch")
            used.add(key)
            actual.append((width, height, colors, bits, size, hashlib.sha256(payload).hexdigest()))
        require(Counter(actual) == expected, "Embedded native icon pixels/frames do not match the supplied ICO")
        reports.append({"resource": list(path), "frames": count})
    require(used == set(images), "Unreferenced native icon resources remain")
    return {"verified": True, "method": "RT_GROUP_ICON metadata and exact RT_ICON frame bytes",
            "reference_sha256": hashlib.sha256(icon).hexdigest(), "groups": reports,
            "frames_per_group": sum(expected.values())}


def version_node(data: bytes, start: int = 0, limit: int | None = None, depth: int = 0) -> dict:
    limit = len(data) if limit is None else limit
    require(depth <= 4 and start % 4 == 0, "Invalid version resource nesting/alignment")
    length, value_length, kind = unpack("<HHH", data, start)
    end = start + length
    require(length >= 8 and end <= limit and kind in (0, 1), "Invalid version resource length/type")
    position = start + 6
    key_end = position
    while key_end + 2 <= end and take(data, key_end, 2) != b"\0\0":
        key_end += 2
    require(key_end + 2 <= end and key_end - position <= 2048, "Invalid version resource key")
    key = take(data, position, key_end - position).decode("utf-16le")
    require(bool(key), "Empty version resource key")
    position = aligned(key_end + 2)
    value_bytes = value_length * 2 if kind == 1 else value_length
    require(position <= end and value_bytes <= end - position, "Version value exceeds its block")
    value = take(data, position, value_bytes)
    position = aligned(position + value_bytes)
    children = []
    while position < end:
        require(end - position >= 8, "Truncated version resource child")
        child = version_node(data, position, end, depth + 1)
        children.append(child)
        position = aligned(position + child["length"])
    require(position <= aligned(end), "Version resource child exceeds parent")
    return {"key": key, "kind": kind, "value": value, "children": children, "length": length}


def read_versions(pe: PE) -> list[dict]:
    resources = {path: value for path, value in pe.resources.items() if path[0] == 16}
    require(resources, "Native VERSIONINFO is missing")
    reports = []
    for path, data in resources.items():
        root = version_node(data)
        require(root["key"] == "VS_VERSION_INFO" and root["kind"] == 0 and root["length"] == len(data), "Malformed VERSIONINFO root")
        require(len(root["value"]) == 52, "Missing VS_FIXEDFILEINFO")
        fixed = unpack("<13I", root["value"])
        require(fixed[0] == 0xFEEF04BD and fixed[1] == 0x10000, "Invalid VS_FIXEDFILEINFO signature")
        file_version = (fixed[2] >> 16, fixed[2] & 0xFFFF, fixed[3] >> 16, fixed[3] & 0xFFFF)
        product_version = (fixed[4] >> 16, fixed[4] & 0xFFFF, fixed[5] >> 16, fixed[5] & 0xFFFF)
        infos = [node for node in root["children"] if node["key"] == "StringFileInfo"]
        require(len(infos) == 1 and infos[0]["children"], "Missing or duplicate native string tables")
        table_ids = set()
        for table in infos[0]["children"]:
            require(re.fullmatch(r"[0-9A-Fa-f]{8}", table["key"]) and table["key"].lower() not in table_ids, "Malformed/duplicate native language table")
            table_ids.add(table["key"].lower())
            fields = {}
            for node in table["children"]:
                require(node["kind"] == 1 and not node["children"] and node["key"] not in fields, "Malformed/duplicate version string")
                value = node["value"].decode("utf-16le")
                require(value.endswith("\0") and "\0" not in value[:-1], "Invalid native version text terminator")
                fields[node["key"]] = value[:-1]
            reports.append({"resource": list(path), "language_table": table["key"], "strings": fields,
                            "file_version": list(file_version), "product_version": list(product_version)})
    return reports


def verify_versions(pe: PE, engine: str) -> list[dict]:
    numeric = tuple(int(part) for part in engine.split("."))
    require(len(numeric) in (2, 3) and all(0 <= part <= 0xFFFF for part in numeric), "Invalid configured engine version")
    expected_version = numeric + (0,) * (3 - len(numeric))
    reports = read_versions(pe)
    for report in reports:
        require(tuple(report["file_version"][:3]) == expected_version and tuple(report["product_version"][:3]) == expected_version,
                "Native version must retain the pinned Gecko engine version")
        fields = report["strings"]
        for key in ("ProductName", "FileDescription", "InternalName"):
            require(fields.get(key) == "PrivacyBrowser", "Native " + key + " must be exactly PrivacyBrowser")
        require(fields.get("OriginalFilename") == BINARY, "Native OriginalFilename must be privacybrowser.exe")
        require(fields.get("CompanyName") in ("PrivacyBrowser", "PrivacyBrowser contributors"), "Native CompanyName must identify PrivacyBrowser")
        for key in ("FileVersion", "ProductVersion"):
            require(fields.get(key) in (engine, engine + "esr"), "Native " + key + " must retain the Gecko engine version")
    return reports


def read_pe_image(path: Path) -> bytes:
    """Read bounded PE sections, excluding NSIS's potentially large archive overlay."""
    require(not is_link_or_reparse(path) and path.is_file(), "Expected a regular PE file")
    with path.open("rb") as stream:
        dos = stream.read(64)
        require(take(dos, 0, 2) == b"MZ", "Missing DOS executable header")
        offset = unpack("<I", dos, 0x3C)[0]
        require(64 <= offset <= 1024 * 1024, "PE header offset exceeds inspection limit")
        stream.seek(offset)
        coff = stream.read(24)
        require(take(coff, 0, 4) == b"PE\0\0", "Missing PE signature")
        _, count, _, _, _, optional_size, _ = unpack("<HHIIIHH", coff, 4)
        require(1 <= count <= 96 and optional_size <= 4096, "PE header exceeds inspection limit")
        section_table = offset + 24 + optional_size
        stream.seek(section_table)
        sections = stream.read(count * 40)
        end = section_table + count * 40
        for index in range(count):
            size, raw = unpack("<II", sections, index * 40 + 16)
            if size:
                end = max(end, raw + size)
        require(end <= MAX_PE_BYTES, "File-backed PE image exceeds inspection size limit")
        stream.seek(0)
        result = stream.read(end)
        require(len(result) == end, "Truncated file-backed PE image")
        return result


def validate_installer(installer: Path, icon: Path = ROOT / "browser/privacy/assets/privacybrowser.ico") -> dict:
    """Check native NSIS wrapper identity only, never run or unpack its payload."""
    require(installer.name == SETUP_NAME, "Unexpected native installer basename")
    require(icon.is_file() and not is_link_or_reparse(icon) and icon.stat().st_size <= MAX_RESOURCE_BYTES, "Missing/oversized reference ICO")
    pe = PE(read_pe_image(installer), expected_machine=0x14C)
    versions = read_versions(pe)
    expected = {"ProductName": "PrivacyBrowser Setup", "FileDescription": "PrivacyBrowser Installer",
                "InternalName": "PrivacyBrowser Setup", "OriginalFilename": SETUP_NAME,
                "CompanyName": "PrivacyBrowser contributors", "FileVersion": "0.1.0-dev", "ProductVersion": "0.1.0-dev"}
    for record in versions:
        require(record["file_version"] == [0, 1, 0, 0] and record["product_version"] == [0, 1, 0, 0], "Unexpected installer numeric version")
        for key, value in expected.items():
            require(record["strings"].get(key) == value, "Unexpected installer native " + key)
    return {"schema_version": 1, "installer_static_identity_passed": True, "artifact": installer.name,
            "sha256": digest(installer), "artifact_bytes": installer.stat().st_size,
            "stub_architecture": "i386", "native_version_resources": versions,
            "native_icons": verify_icons(pe, icon.read_bytes()), "payload_extraction_verified": False,
            "authenticode_verification": "not performed", "windows_execution_tested": False,
            "windows_install_uninstall_tested": False, "release_eligible": False,
            "limitation": "Static NSIS wrapper identity only; compressed payload and embedded uninstaller were not inspected or executed."}


def verify_provenance(evidence: Path, files: dict[str, str], upstream: str, icon_sha256: str) -> dict:
    require(not is_link_or_reparse(evidence) and evidence.is_file(), "A regular source-build evidence JSON is required")
    proof = load_json(evidence)
    require(type(proof.get("schema_version")) is int and proof["schema_version"] == 1 and proof.get("build_kind") == "full-source-build"
            and proof.get("source_compiled") is True and proof.get("official_release") is False,
            "An explicitly unpublished full-source-build evidence record is required")
    require(proof.get("target") == "windows" and proof.get("upstream_version") == upstream
            and proof.get("native_binary") == BINARY, "Source-build target/version/native binary mismatch")
    declared = proof.get("files")
    require(isinstance(declared, dict), "Missing source-package payload inventory")
    for name, checksum in declared.items():
        safe_relative(name)
        require(isinstance(checksum, str) and re.fullmatch(r"[a-f0-9]{64}", checksum), "Invalid payload SHA-256")
    require(declared == files, "Source-package file inventory/hash mismatch")
    commands = proof.get("commands")
    require(isinstance(commands, list), "Missing source-build command results")
    for command in ("build", "package"):
        matches = [entry for entry in commands if isinstance(entry, dict) and entry.get("argv") == ["./mach", command]]
        require(len(matches) == 1 and type(matches[0].get("exit_code")) is int and matches[0]["exit_code"] == 0,
                "Successful ./mach " + command + " evidence is required")
    artifacts = {}
    paths = set()
    for role in ("preparation", "source_verification", "build_log", "package_log"):
        record = proof.get(role)
        require(isinstance(record, dict), "Missing source-build evidence: " + role)
        path = regular_file(evidence.resolve().parent, record.get("path"))
        require(path.resolve() not in paths and path.resolve() != evidence.resolve(), "Evidence artifacts must be distinct")
        paths.add(path.resolve())
        require(path.stat().st_size > 0 and record.get("sha256") == digest(path), "Evidence artifact hash mismatch: " + role)
        artifacts[role] = path
    preparation = load_json(artifacts["preparation"])
    require(preparation.get("prepared") is True and preparation.get("compiled") is False
            and preparation.get("target") == "windows" and preparation.get("native_binary") == BINARY
            and preparation.get("app_name") == "PrivacyBrowser" and preparation.get("upstream_version") == upstream
            and preparation.get("icon_sha256") == icon_sha256, "Source preparation evidence does not match native identity")
    source = load_json(artifacts["source_verification"])
    trust = load_json(ROOT / "config/update-trust.json")
    require(source.get("kind") == "source" and source.get("signature_verified") is True
            and source.get("upstream_version") == upstream and source.get("archive") == "firefox-" + upstream + ".source.tar.xz"
            and source.get("checksum_signer") in trust["mozilla_release_key_fingerprints"]
            and isinstance(source.get("sha512"), str) and re.fullmatch(r"[a-f0-9]{128}", source["sha512"]),
            "Pinned upstream source verification evidence is required")
    return {"manifest_sha256": digest(evidence),
            "artifacts": {role: {"path": proof[role]["path"], "sha256": digest(path)} for role, path in artifacts.items()},
            "source_archive_sha512": source["sha512"], "integrity_checked": True,
            "compilation_independently_proven": False,
            "limitation": "Retained command results, logs and source verification metadata are assertions from the build host; this static check does not reproduce the build or reverify the upstream signature."}


def validate_candidate(runtime: Path, evidence: Path, icon: Path = ROOT / "browser/privacy/assets/privacybrowser.ico") -> dict:
    require(runtime.resolve() not in evidence.resolve().parents, "Source-build evidence must be outside the runtime payload")
    files = inventory(runtime)
    for name in REQUIRED:
        require(name in files, "Incomplete source-built runtime: " + name)
    require(not any(Path(name).name.casefold() == "firefox.exe" for name in files), "Firefox binaries cannot enter a native PrivacyBrowser candidate")
    require(not (runtime / "PRIVACY_BROWSER_BUILD.json").exists(), "Developer overlay manifests are not source-build evidence")
    config = load_json(ROOT / "config/esr.json")
    upstream = config["upstream_version"]
    engine = upstream.removesuffix("esr")
    require(icon.is_file() and not icon.is_symlink() and icon.stat().st_size <= MAX_RESOURCE_BYTES, "Missing/oversized reference ICO")
    icon_data = icon.read_bytes()
    icon_hash = hashlib.sha256(icon_data).hexdigest()
    require(files["distribution/privacybrowser/assets/privacybrowser.ico"] == icon_hash, "Packaged overlay ICO differs from supplied branding asset")
    app = configparser.ConfigParser(interpolation=None, strict=True)
    app.read_string((runtime / "application.ini").read_text(encoding="utf-8"))
    expected = {"Name": "PrivacyBrowser", "Vendor": "PrivacyBrowser", "Version": engine, "ID": APP_ID}
    for field, value in expected.items():
        require(app.get("App", field, fallback="") == value, "application.ini App/" + field + " mismatch")
    if app.has_option("App", "RemotingName"):
        require(app.get("App", "RemotingName") == "privacybrowser", "Unexpected application remoting identity")
    platform = configparser.ConfigParser(interpolation=None, strict=True)
    platform.read_string((runtime / "platform.ini").read_text(encoding="utf-8"))
    require(platform.get("Build", "Milestone", fallback="").removesuffix("esr") == engine, "Gecko platform milestone differs from ESR pin")
    binary = runtime / BINARY
    require(binary.stat().st_size <= MAX_PE_BYTES, "Native executable exceeds inspection size limit")
    pe = PE(binary.read_bytes())
    versions = verify_versions(pe, engine)
    icons = verify_icons(pe, icon_data)
    provenance = verify_provenance(evidence, files, upstream, icon_hash)
    return {"schema_version": 1, "static_candidate_checks_passed": True, "app_name": "PrivacyBrowser",
            "native_binary": BINARY, "native_binary_sha256": files[BINARY], "architecture": "AMD64",
            "upstream_version": upstream, "engine_version": engine, "native_version_resources": versions,
            "native_icons": icons, "source_build_evidence": provenance, "files": files,
            "payload_file_count": len(files), "official_release": False, "release_eligible": False,
            "authenticode_verification": "not performed", "windows_execution_tested": False,
            "windows_install_uninstall_tested": False,
            "limitations": ["Static file inspection only; no executable was loaded or run.",
                            "Build provenance authenticity, Windows startup, Explorer display, installer UI, profile behavior and runtime security remain untested.",
                            "A successful static check does not permit publication or replace release_gate.py."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="Unpacked independently source-built application directory")
    parser.add_argument("--evidence", type=Path, required=True, help="Retained source-build evidence JSON outside the payload")
    parser.add_argument("--icon", type=Path, default=ROOT / "browser/privacy/assets/privacybrowser.ico")
    parser.add_argument("--output", type=Path, help="Write static-check JSON outside the payload")
    args = parser.parse_args()
    if args.output:
        output = args.output.resolve()
        require(not args.output.is_symlink() and output != args.runtime.resolve() and args.runtime.resolve() not in output.parents,
                "Report output must be outside the runtime")
        require(output not in (args.evidence.resolve(), args.icon.resolve()), "Report cannot overwrite evidence or icon input")
        # Existing inputs, including retained logs, must never be overwritten.
        require(not args.output.exists(), "Report output already exists; choose a new path")
    report = validate_candidate(args.runtime, args.evidence, args.icon)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, configparser.Error, UnicodeError) as error:
        raise SystemExit("Native Windows candidate rejected: " + str(error))
