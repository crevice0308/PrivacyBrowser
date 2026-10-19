#!/usr/bin/env python3
"""Apply the Privacy Browser overlay to a verified Firefox ESR source checkout.

This prepares a real Gecko source build. It does not claim to compile or test it.
Expected upstream files are checked first; structural drift stops preparation so
an ESR update conflict cannot silently omit the overlay from a shipped package.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shutil

from build_dev import AUTOCONFIG, ROOT, overlay_files
from session_patch import READ_ANCHOR, WRITE_ANCHOR, patch_session_file

MARKER = "Privacy Browser source overlay integration v0.1.0"
PRODUCT_NAME = "PrivacyBrowser"
EXECUTABLE_NAME = "privacybrowser"
COMPANY_NAME = "PrivacyBrowser contributors"
ICON_SIZES = {(size, size) for size in (16, 24, 32, 48, 64, 128, 256)}


def replace_one(text: str, pattern: str, value: str, label: str) -> str:
    """Change a reviewed branding field, never legal text or arbitrary words."""
    result, count = re.subn(pattern, lambda match: value, text, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"ESR branding layout changed: expected one {label}, found {count}")
    return result


def startup_error_caption(text: str) -> str:
    return replace_one(
        text, re.escape('messageBoxW(nullptr, wide_msg, L"Firefox",'),
        'messageBoxW(nullptr, wide_msg, L"PrivacyBrowser",',
        "Windows startup-error message-box caption",
    )


def regular_file(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.is_file() or any(part.is_symlink() for part in (path, *path.parents) if part != root.parent):
        raise ValueError(f"Not a supported source tree: missing or symlinked {relative}")
    return path


def custom_icon(overlay: Path):
    """Check every ICO frame against our PNG before using it in native resources."""
    try:
        from PIL import Image
        with Image.open(regular_file(overlay, "assets/privacybrowser.png")) as png:
            if png.format != "PNG" or png.width != png.height or png.width < 256:
                raise ValueError("Brand PNG must be square and at least 256 x 256")
            image = png.convert("RGBA")
        icon_bytes = regular_file(overlay, "assets/privacybrowser.ico").read_bytes()
        with Image.open(io.BytesIO(icon_bytes)) as icon:
            if icon.format != "ICO" or icon.ico.sizes() != ICON_SIZES:
                raise ValueError("Brand ICO must contain exactly the 16/24/32/48/64/128/256 px frames")
            for size in sorted(ICON_SIZES):
                expected = image.resize(size, Image.Resampling.LANCZOS)
                if icon.ico.getimage(size).convert("RGBA").tobytes() != expected.tobytes():
                    raise ValueError(f"Brand ICO frame {size} does not match the custom PNG")
    except (ImportError, OSError, SyntaxError) as error:
        raise ValueError("Valid matching custom PNG/ICO files and Pillow are required") from error
    return image, icon_bytes


def branding_plan(source: Path, overlay: Path) -> dict[Path, bytes]:
    """Validate the complete supported layout before writing any source files.

    Only product identity fields are rewritten. Mozilla license notices,
    copyrights, trademark attributions, Gecko versions, app ID and Windows
    LimitedAccessFeature permission identities are deliberately untouched.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise ValueError("Pillow is required for source branding") from error

    original = source / "browser/branding/unofficial"
    target = source / "browser/branding/privacybrowser"
    if target.exists() or target.is_symlink():
        raise ValueError("PrivacyBrowser branding directory already exists")
    required = (
        "moz.build", "configure.sh", "branding.nsi", "pref/firefox-branding.js",
        "locales/en-US/brand.ftl", "locales/en-US/brand.properties", "content/jar.mn",
        "content/about-logo.svg", "content/about-wordmark.svg", "content/firefox-wordmark.svg",
        "content/document_pdf.svg", "firefox.VisualElementsManifest.xml",
        "private_browsing.VisualElementsManifest.xml",
        "firefox.ico", "document.ico", "newwindow.ico", "newtab.ico", "pbmode.ico", "document_pdf.ico",
        "default16.png", "default32.png", "default48.png", "default64.png", "default128.png",
        "VisualElements_150.png", "VisualElements_70.png", "PrivateBrowsing_150.png", "PrivateBrowsing_70.png",
    )
    for relative in required:
        regular_file(original, relative)
    paths = sorted(original.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("Symlinks are not supported in the upstream branding tree")
    image, icon_bytes = custom_icon(overlay)
    files = {path.relative_to(original).as_posix(): path.read_bytes() for path in paths if path.is_file()}
    # Do not let missing packaged artwork be silently omitted from the build.
    jar_text = files["content/jar.mn"].decode("utf-8")
    for name, alternate in re.findall(r'^\s+content/branding/([^\s]+)(?:[ \t]+\(([^)]+)\))?[ \t]*$', jar_text, re.MULTILINE):
        packaged = (original / "content" / (alternate or name)).resolve()
        if not packaged.is_relative_to(original.resolve()) or not packaged.is_file():
            raise ValueError(f"Missing or escaping branding package asset: {name}")

    def edit(relative: str, pattern: str, replacement: str) -> None:
        files[relative] = replace_one(files[relative].decode("utf-8"), pattern, replacement, relative).encode("utf-8")

    edit("configure.sh", r'^MOZ_APP_DISPLAYNAME=.*$', 'MOZ_APP_DISPLAYNAME="PrivacyBrowser"')
    for name in ("shorter", "short", "shortcut", "full", "product"):
        edit("locales/en-US/brand.ftl", rf'^-brand-{name}-name\s*=.*$', f'-brand-{name}-name = {PRODUCT_NAME}')
    edit("locales/en-US/brand.ftl", r'^-vendor-short-name\s*=.*$', f'-vendor-short-name = {COMPANY_NAME}')
    for name in ("brandShorterName", "brandShortName", "brandFullName"):
        edit("locales/en-US/brand.properties", rf'^{name}=.*$', f'{name}={PRODUCT_NAME}')
    installer_values = {
        "BrandFullNameInternal": PRODUCT_NAME, "BrandFullName": PRODUCT_NAME,
        "CompanyName": COMPANY_NAME,
        # No independent distribution/support service or signing certificate has
        # been provisioned. Never direct our users to Mozilla's Firefox downloads.
        "URLInfoAbout": "", "HelpLink": "", "URLStubDownloadX86": "",
        "URLStubDownloadAMD64": "", "URLStubDownloadAArch64": "", "URLManualDownload": "",
        "URLSystemRequirements": "", "CertNameDownload": "", "CertIssuerDownload": "",
    }
    for name, value in installer_values.items():
        edit("branding.nsi", rf'^!define\s+{name}\s+"[^"\n]*"\s*$', f'!define {name} "{value}"')
    for name in ("app.update.url.manual", "app.update.url.details"):
        edit("pref/firefox-branding.js", rf'^pref\("{re.escape(name)}", "[^"\n]*"\);$', f'pref("{name}", "");')

    # Preserve the reviewed packaging filenames used by Gecko while replacing
    # every native icon group and all Linux/Windows brand artwork.
    png_stream = io.BytesIO()
    image.save(png_stream, "PNG")
    logo_svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">'
                '<title>PrivacyBrowser</title><image width="256" height="256" href="data:image/png;base64,'
                + base64.b64encode(png_stream.getvalue()).decode("ascii") + '"/></svg>\n').encode("utf-8")
    wordmark = (b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="64" viewBox="0 0 320 64">'
                b'<title>PrivacyBrowser</title><text x="0" y="46" font-family="sans-serif" '
                b'font-size="40" font-weight="600" fill="context-fill">PrivacyBrowser</text></svg>\n')
    known_svgs = {"content/about-logo.svg", "content/about-wordmark.svg", "content/firefox-wordmark.svg", "content/document_pdf.svg"}
    for relative, data in list(files.items()):
        suffix = Path(relative).suffix.lower()
        if suffix in (".png", ".bmp", ".jpg", ".jpeg"):
            try:
                with Image.open(io.BytesIO(data)) as old:
                    size = old.size
                    old.verify()
            except (OSError, SyntaxError) as error:
                raise ValueError(f"Unreadable upstream brand artwork: {relative}") from error
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            logo = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
            canvas.alpha_composite(logo, ((size[0] - logo.width) // 2, (size[1] - logo.height) // 2))
            if suffix != ".png":
                background = Image.new("RGB", size, "#286b58")
                background.paste(canvas, mask=canvas.getchannel("A"))
                canvas = background
            output = io.BytesIO()
            canvas.save(output, {".png": "PNG", ".bmp": "BMP", ".jpg": "JPEG", ".jpeg": "JPEG"}[suffix])
            files[relative] = output.getvalue()
        elif suffix == ".ico":
            files[relative] = icon_bytes
        elif suffix == ".svg":
            if relative not in known_svgs:
                raise ValueError(f"Unreviewed upstream brand SVG: {relative}")
            files[relative] = wordmark if "wordmark" in relative else logo_svg
        elif suffix in (".gif", ".webp"):
            raise ValueError(f"Unreviewed upstream brand artwork: {relative}")
        # .icns and macOS disk-image assets are outside the supported target set.

    common = regular_file(source, "browser/branding/branding-common.mozbuild")
    common_text = replace_one(common.read_text(encoding="utf-8"), r'"firefox\.VisualElementsManifest\.xml"',
                              'CONFIG["MOZ_APP_NAME"] + ".VisualElementsManifest.xml"', "Windows visual manifest packaging")
    files["privacybrowser.VisualElementsManifest.xml"] = files.pop("firefox.VisualElementsManifest.xml")
    changes = {target / relative: data for relative, data in files.items()}
    changes[common] = common_text.encode("utf-8")
    package_manifest = regular_file(source, "browser/installer/package-manifest.in")
    changes[package_manifest] = replace_one(
        package_manifest.read_text(encoding="utf-8"),
        r'^@BINPATH@/firefox\.VisualElementsManifest\.xml$',
        '@BINPATH@/@MOZ_APP_NAME@.VisualElementsManifest.xml',
        "Windows visual manifest package entry",
    ).encode("utf-8")

    # create_rc.py uses module.ver for native Windows Details fields. This file
    # is Latin-1 upstream: preserve copyright bytes instead of corrupting them.
    for relative, binary in (("browser/app/module.ver", "privacybrowser.exe"),
                              ("browser/app/pbproxy/module.ver", "private_browsing.exe")):
        module = regular_file(source, relative)
        module_text = module.read_bytes().decode("latin1")
        for field, expected in (("PRODUCTVERSION", "@MOZ_APP_WINVERSION@"),
                                ("PRODUCTVERSION_STRING", "@MOZ_APP_VERSION@")):
            if re.findall(rf'^WIN32_MODULE_{field}=(.*)$', module_text, re.MULTILINE) != [expected]:
                raise ValueError(f"{relative} has an unreviewed engine version field")
        for name, value in {"COMPANYNAME": COMPANY_NAME, "DESCRIPTION": PRODUCT_NAME,
                            "PRODUCTNAME": PRODUCT_NAME, "NAME": PRODUCT_NAME}.items():
            module_text = replace_one(module_text, rf'^WIN32_MODULE_{name}=.*$', f'WIN32_MODULE_{name}={value}', f"{relative} {name}")
        if "WIN32_MODULE_ORIGINAL_FILENAME" in module_text:
            raise ValueError(f"{relative} has an unreviewed OriginalFilename override")
        module_text = module_text.rstrip("\r\n") + f"\nWIN32_MODULE_ORIGINAL_FILENAME={binary}\n"
        changes[module] = module_text.encode("latin1")

    # The resource generator searches for <compiled binary>.manifest. Keep the
    # existing privilege, DPI, supported-OS and compatibility declarations.
    manifest = regular_file(source, "browser/app/firefox.exe.manifest")
    native_manifest = source / "browser/app/privacybrowser.exe.manifest"
    if native_manifest.exists() or native_manifest.is_symlink():
        raise ValueError("Native PrivacyBrowser executable manifest already exists")
    manifest_text = replace_one(manifest.read_text(encoding="utf-8"), r'\bname="Firefox"',
                                'name="PrivacyBrowser"', "native assembly identity")
    manifest_text = replace_one(manifest_text, r'<description>Firefox</description>',
                                '<description>PrivacyBrowser</description>', "native assembly description")
    changes[native_manifest] = manifest_text.encode("utf-8")
    browser_app = regular_file(source, "browser/app/nsBrowserApp.cpp")
    changes[browser_app] = startup_error_caption(browser_app.read_text(encoding="utf-8")).encode("utf-8")
    # These are app-registration identity strings, not Gecko compatibility IDs.
    # Never route PrivacyBrowser's default-browser UI to a Firefox registration.
    shell_identity = regular_file(source, "browser/components/shell/WindowsDefaultBrowser.cpp")
    changes[shell_identity] = replace_one(
        shell_identity.read_text(encoding="utf-8"),
        r'^#define APP_REG_NAME_BASE L"Firefox-"$',
        '#define APP_REG_NAME_BASE L"PrivacyBrowser-"', "Windows app registration name",
    ).encode("utf-8")
    shell_service = regular_file(source, "browser/components/shell/nsWindowsShellService.cpp")
    shell_text = shell_service.read_text(encoding="utf-8")
    for suffix in ("URL", "HTML", "PDF"):
        shell_text = replace_one(shell_text, re.escape(f'FormatProgID(L"Firefox{suffix}", aumid.get())'),
                                 f'FormatProgID(L"PrivacyBrowser{suffix}", aumid.get())', f"Windows {suffix} ProgID")
    changes[shell_service] = shell_text.encode("utf-8")
    # Modern ESR project_flag values accept implied options only, not confvars
    # or environment exports. Keep the Gecko add-on application ID unchanged.
    browser_configure = regular_file(source, "browser/moz.configure")
    browser_text = browser_configure.read_text(encoding="utf-8")
    if '"MOZ_APP_PROFILE"' in browser_text:
        raise ValueError("ESR has an unreviewed application profile override")
    changes[browser_configure] = replace_one(
        browser_text, r'^imply_option\("MOZ_APP_VENDOR", "Mozilla"\)$',
        'imply_option("MOZ_APP_VENDOR", "PrivacyBrowser")\nimply_option("MOZ_APP_PROFILE", "PrivacyBrowser")',
        "application vendor/profile project flags",
    ).encode("utf-8")
    # ESR153 has no --disable-installer option. NSIS discovery auto-enables its
    # Firefox-specific installer and helper.exe. Gate only that reviewed check
    # off for our product; independent project packaging uses its own NSIS.
    root_configure = regular_file(source, "moz.configure")
    nsis_check = '''nsis = check_prog(
    "MAKENSISU",
    ("makensis",),
    bootstrap="nsis/bin",
    allow_missing=True,
    when=target_is_windows,
)'''
    changes[root_configure] = replace_one(
        root_configure.read_text(encoding="utf-8"), re.escape(nsis_check),
        nsis_check.replace("when=target_is_windows,", 'when=target_is_windows & depends(moz_app_basename)(lambda name: name != "PrivacyBrowser"),'),
        "upstream NSIS installer discovery",
    ).encode("utf-8")
    return changes


def append_once(path: Path, text: str) -> None:
    contents = path.read_text(encoding="utf-8")
    if MARKER in contents:
        raise ValueError(f"Source already prepared: {path}")
    comment = "; " if path.name == "package-manifest.in" else "# "
    path.write_text(contents.rstrip() + "\n\n" + comment + MARKER + "\n" + text + "\n", encoding="utf-8")


def brand_tree(source: Path, overlay: Path) -> None:
    for path, data in branding_plan(source, overlay).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def prepare(source: Path, overlay: Path, target: str) -> dict:
    source, overlay = source.resolve(), overlay.resolve()
    if target not in ("linux", "windows"):
        raise ValueError("Only Linux and Windows source branding is supported")
    if source == overlay or source in overlay.parents or overlay in source.parents:
        raise ValueError("Source and overlay directories must not overlap")
    files = overlay_files(overlay)
    if any(path.is_symlink() for path in overlay.rglob("*")):
        raise ValueError("Symlinks are not supported in the overlay")
    mozconfig = regular_file(ROOT, f"config/mozconfig.{target}")
    required = ["mach", "browser/moz.build", "browser/confvars.sh", "browser/installer/package-manifest.in", "browser/branding/unofficial/moz.build", "browser/components/sessionstore/SessionFile.sys.mjs"]
    for name in required:
        regular_file(source, name)
    for relative in ("mozconfig", "PRIVACY_BROWSER_SOURCE_PREPARATION.json"):
        if (source / relative).is_symlink():
            raise ValueError(f"Refusing to write through symlink: {relative}")
    config = json.loads((ROOT / "config/esr.json").read_text(encoding="utf-8"))
    upstream_version = config["upstream_version"]
    source_version = regular_file(source, "browser/config/version.txt").read_text(encoding="utf-8").strip()
    display_version = regular_file(source, "browser/config/version_display.txt").read_text(encoding="utf-8").strip()
    if source_version != upstream_version.removesuffix("esr") or display_version != upstream_version:
        raise ValueError("Source engine version does not match the pinned ESR; obtain the verified source archive")
    if any(MARKER in (source / name).read_text(encoding="utf-8") for name in required[1:4]):
        raise ValueError("Source tree is already prepared; use a clean verified checkout")
    output = source / "browser/privacybrowser"
    if output.exists() or output.is_symlink():
        raise ValueError("Source overlay directory already exists")
    session_file = source / "browser/components/sessionstore/SessionFile.sys.mjs"
    session_text = session_file.read_text(encoding="utf-8")
    if session_text.count(READ_ANCHOR) != 1 or session_text.count(WRITE_ANCHOR) != 1:
        raise ValueError("ESR SessionFile changed: manual source patch review required")
    # Validate custom branding before mutating upstream files.
    brand_tree(source, overlay)
    output.mkdir()
    shutil.copytree(overlay, output / "overlay", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    manifest = output / "overlay/chrome.manifest"
    if not manifest.exists():
        shutil.copyfile(output / "overlay/privacybrowser.manifest", manifest)
    shutil.copyfile(overlay / "bootstrap.cfg", output / "privacybrowser.cfg")
    (output / "privacybrowser-autoconfig.js").write_text(AUTOCONFIG)
    mozbuild = ["# " + MARKER, 'DIST_SUBDIR = ""', 'FINAL_TARGET_FILES += ["privacybrowser.cfg"]',
                'FINAL_TARGET_FILES.defaults.pref += ["privacybrowser-autoconfig.js"]']
    packaged = ["@RESPATH@/privacybrowser.cfg", "@RESPATH@/defaults/pref/privacybrowser-autoconfig.js"]
    copied_files = sorted(path for path in (output / "overlay").rglob("*") if path.is_file())
    for path in copied_files:
        relative = path.relative_to(output / "overlay")
        destination = "FINAL_TARGET_FILES" + ''.join(f'[{json.dumps(part)}]' for part in ("distribution", "privacybrowser", *relative.parts[:-1]))
        mozbuild.append(f'{destination} += [{json.dumps("overlay/" + relative.as_posix())}]')
        packaged.append("@RESPATH@/distribution/privacybrowser/" + relative.as_posix())
    (output / "moz.build").write_text("\n".join(mozbuild) + "\n")
    append_once(source / "browser/moz.build", 'DIRS += ["privacybrowser"]')
    append_once(source / "browser/installer/package-manifest.in", "\n".join(packaged))
    append_once(source / "browser/confvars.sh", '''# Product identity is set in branding/configure.sh, browser/moz.configure
# and mozconfig. Keep Gecko's application ID and engine versions unchanged.
''')
    patch_session_file(session_file)
    shutil.copyfile(mozconfig, source / "mozconfig")
    evidence = {"prepared": True, "compiled": False, "target": target, "overlay_files": len(files),
                "native_binary": "privacybrowser.exe" if target == "windows" else "privacybrowser",
                "app_name": PRODUCT_NAME, "engine_version": source_version, "upstream_version": upstream_version,
                "icon_sha256": hashlib.sha256((overlay / "assets/privacybrowser.ico").read_bytes()).hexdigest(),
                "branding": "independent PrivacyBrowser product names, native resources and icons; Mozilla legal attribution retained",
                "windows_installer": "upstream NSIS/helper disabled; independent project packaging required",
                "default_browser_registration": "not provisioned; independent lookup names only",
                "source_security_features": "default Gecko sandbox, Fission, signature checks and certificate validation retained",
                "update_service": "disabled until independently signed infrastructure is ready",
                "native_session_guard": "read and write blocked by default before SessionWriter disk I/O; source compiled validation required",
                "overlay_sha256": hashlib.sha256(b"".join(path.relative_to(overlay).as_posix().encode() + path.read_bytes() for path in files)).hexdigest()}
    (source / "PRIVACY_BROWSER_SOURCE_PREPARATION.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, default=ROOT / "browser/privacy")
    parser.add_argument("--target", choices=("linux", "windows"), required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.overlay, args.target), indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        raise SystemExit(f"Source preparation failed: {error}")
