"""Offline source fixtures only; no Gecko build or Windows binary execution.

Branding field/layout fixtures follow Mozilla ESR source revision
3806652efdaccfd958d04803eddd3fdf131cd0cd. Native runtime verification is separate.
"""
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import prepare_source
import session_patch


LEGAL = "# This Source Code Form is subject to the terms of the Mozilla Public\n# License, v. 2.0. See http://mozilla.org/MPL/2.0/.\n"
MANIFEST = b'''<?xml version="1.0" encoding="UTF-8"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity version="1.0.0.0" processorArchitecture="*" name="Firefox" type="win32"/>
  <description>Firefox</description>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges>
    <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
  </requestedPrivileges></security></trustInfo>
</assembly>
'''


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class NativeBrandingSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.source, self.overlay = root / "source", root / "overlay"
        self.source.mkdir()
        self.overlay.mkdir()
        (self.overlay / "bootstrap.cfg").write_text("// Fixture config\n", encoding="utf-8", newline="\n")
        (self.overlay / "chrome.manifest").write_text("content privacybrowser content/\n", encoding="utf-8", newline="\n")
        shutil.copytree(ROOT / "browser/privacy/assets", self.overlay / "assets")
        self.brand = self.source / "browser/branding/unofficial"
        self.target = self.source / "browser/branding/privacybrowser"
        self.brand.mkdir(parents=True)
        pinned = json.loads((ROOT / "config/esr.json").read_text())["upstream_version"]
        files = {
            "mach": "# source fixture, never executed\n",
            "browser/moz.build": LEGAL + 'DIRS += ["app"]\n',
            "browser/confvars.sh": LEGAL + "MOZ_APP_NAME=firefox\nMOZ_APP_ID={ec8030f7-c20a-464f-9b0e-13a3a9e97384}\n",
            "browser/moz.configure": LEGAL + 'imply_option("MOZ_APP_VENDOR", "Mozilla")\nimply_option("MOZ_APP_ID", "{ec8030f7-c20a-464f-9b0e-13a3a9e97384}")\ninclude("../toolkit/moz.configure")\n',
            "moz.configure": LEGAL + '''nsis = check_prog(
    "MAKENSISU",
    ("makensis",),
    bootstrap="nsis/bin",
    allow_missing=True,
    when=target_is_windows,
)
''',
            "browser/config/version.txt": pinned.removesuffix("esr") + "\n",
            "browser/config/version_display.txt": pinned + "\n",
            "browser/installer/package-manifest.in": "; fixture\n@BINPATH@/firefox.VisualElementsManifest.xml\n",
            "browser/components/sessionstore/SessionFile.sys.mjs": session_patch.READ_ANCHOR + "\n  },\n" + session_patch.WRITE_ANCHOR + "\n  }\n",
            "browser/app/nsBrowserApp.cpp": LEGAL + '''// Firefox attribution remains unchanged.
        messageBoxW(nullptr, wide_msg, L"Firefox",
                    MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
''',
            "browser/components/shell/WindowsDefaultBrowser.cpp": LEGAL + '#define APP_REG_NAME_BASE L"Firefox-"\n',
            "browser/components/shell/nsWindowsShellService.cpp": LEGAL + "\n".join(
                f'CheckProgIDExists(FormatProgID(L"Firefox{suffix}", aumid.get()).get());' for suffix in ("URL", "HTML", "PDF")
            ) + "\n",
            "browser/branding/branding-common.mozbuild": LEGAL + '''@template
def FirefoxBranding():
    if CONFIG["MOZ_WIDGET_TOOLKIT"] == "windows":
        FINAL_TARGET_FILES[".."] += [
            "firefox.VisualElementsManifest.xml",
            "private_browsing.VisualElementsManifest.xml",
        ]
''',
        }
        # The pinned upstream archive contains LF bytes, including on Windows.
        self.source_text_fixtures = files
        for relative, text in files.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        app = self.source / "browser/app"
        app.mkdir(exist_ok=True)
        (app / "firefox.exe.manifest").write_bytes(MANIFEST)
        self.module = (
            b"WIN32_MODULE_COMPANYNAME=Mozilla Corporation\n"
            b"WIN32_MODULE_COPYRIGHT=\xa9Firefox and Mozilla Developers; available under the MPL 2 license.\n"
            b"WIN32_MODULE_PRODUCTVERSION=@MOZ_APP_WINVERSION@\n"
            b"WIN32_MODULE_PRODUCTVERSION_STRING=@MOZ_APP_VERSION@\n"
            b"WIN32_MODULE_TRADEMARKS=Firefox is a Trademark of The Mozilla Foundation.\n"
            b"WIN32_MODULE_DESCRIPTION=@MOZ_APP_DISPLAYNAME@\n"
            b"WIN32_MODULE_PRODUCTNAME=@MOZ_APP_DISPLAYNAME@\n"
            b"WIN32_MODULE_NAME=@MOZ_APP_DISPLAYNAME@\n"
        )
        (app / "module.ver").write_bytes(self.module)
        (app / "pbproxy").mkdir()
        (app / "pbproxy/module.ver").write_bytes(self.module)
        brand_files = {
            "moz.build": LEGAL + 'include("../branding-common.mozbuild")\nFirefoxBranding()\n',
            "configure.sh": LEGAL + "MOZ_APP_DISPLAYNAME=Nightly\nMOZ_MACBUNDLE_ID=nightlyunofficial\n",
            "locales/en-US/brand.ftl": LEGAL + "## Firefox and Mozilla Brand\n" + "\n".join(
                f"-brand-{key}-name = Nightly" for key in ("shorter", "short", "shortcut", "full", "product")
            ) + "\n-vendor-short-name = Mozilla\ntrademarkInfo = { \" \" }\n",
            "locales/en-US/brand.properties": LEGAL + "brandShorterName=Nightly\nbrandShortName=Nightly\nbrandFullName=Nightly\n",
            "pref/firefox-branding.js": LEGAL + 'pref("app.update.url.manual", "https://nightly.mozilla.org");\npref("app.update.url.details", "https://nightly.mozilla.org");\n',
            "content/jar.mn": LEGAL + "browser.jar:\n% content branding %content/branding/ contentaccessible=yes\n" + "\n".join(
                "  content/branding/" + name for name in ("about-logo.svg", "about-wordmark.svg", "firefox-wordmark.svg", "document_pdf.svg")
            ) + "\n",
            "firefox.VisualElementsManifest.xml": '<Application><VisualElements Square150x150Logo="VisualElements\\VisualElements_150.png"/></Application>\n',
            "private_browsing.VisualElementsManifest.xml": '<Application><VisualElements Square150x150Logo="VisualElements\\PrivateBrowsing_150.png"/></Application>\n',
        }
        nsis_values = {
            "BrandFullNameInternal": "Mozilla Developer Preview", "BrandFullName": "Mozilla Developer Preview",
            "CompanyName": "mozilla.org", "URLInfoAbout": "https://www.mozilla.org", "HelpLink": "https://support.mozilla.org",
            "URLStubDownloadX86": "https://download.mozilla.org/?os=win", "URLStubDownloadAMD64": "https://download.mozilla.org/?os=win64",
            "URLStubDownloadAArch64": "https://download.mozilla.org/?os=win64-aarch64", "URLManualDownload": "https://www.mozilla.org/download",
            "URLSystemRequirements": "https://www.mozilla.org/requirements", "CertNameDownload": "Mozilla Corporation",
            "CertIssuerDownload": "DigiCert Trusted G4 Code Signing RSA4096 SHA384 2021 CA1",
        }
        brand_files["branding.nsi"] = LEGAL + "\n".join(f'!define {name} "{value}"' for name, value in nsis_values.items()) + "\n"
        self.brand_text_fixtures = brand_files
        for relative, text in brand_files.items():
            path = self.brand / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        for name in ("about-logo.svg", "about-wordmark.svg", "firefox-wordmark.svg", "document_pdf.svg"):
            (self.brand / "content" / name).write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Nightly</text></svg>', encoding="utf-8", newline="\n")
        for size in (16, 32, 48, 64, 128):
            Image.new("RGBA", (size, size), "red").save(self.brand / f"default{size}.png")
        for prefix in ("VisualElements", "PrivateBrowsing"):
            for size in (70, 150):
                Image.new("RGBA", (size, size), "red").save(self.brand / f"{prefix}_{size}.png")
        for name in ("firefox", "document", "newwindow", "newtab", "pbmode", "document_pdf"):
            Image.new("RGBA", (256, 256), "red").save(self.brand / f"{name}.ico")
        Image.new("RGB", (164, 314), "red").save(self.brand / "wizard.bmp")

    def prepare(self, target="windows"):
        return prepare_source.prepare(self.source, self.overlay, target)

    def assert_rejected_unchanged(self):
        before = snapshot(self.source)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(snapshot(self.source), before)
        self.assertFalse(self.target.exists())

    def test_pinned_upstream_text_fixtures_are_exact_utf8_lf_bytes(self):
        for root, fixtures in ((self.source, self.source_text_fixtures),
                               (self.brand, self.brand_text_fixtures)):
            for relative, expected in fixtures.items():
                with self.subTest(path=(root / relative).relative_to(self.source)):
                    actual = (root / relative).read_bytes()
                    self.assertEqual(actual, expected.encode("utf-8"))
                    self.assertNotIn(b"\r", actual)

    def test_windows_mozconfig_preserves_supported_native_msvc_target(self):
        # The pinned ESR rejects legacy -mingw32/-mingw64 triples. Its native
        # win64 mozconfig uses this MSVC ABI target (and clang-cl toolchain).
        configured = (ROOT / "config/mozconfig.windows").read_bytes()
        targets = [line.strip() for line in configured.decode("utf-8").splitlines()
                   if line.strip().startswith("ac_add_options --target=")]
        self.assertEqual(targets, ["ac_add_options --target=x86_64-pc-windows-msvc"])
        self.assertIn(b"ac_add_options --enable-warnings-as-errors\n", configured)
        self.assertIn(b"ac_add_options --disable-geckodriver\n", configured)
        self.prepare()
        self.assertEqual((self.source / "mozconfig").read_bytes(), configured)

    def test_native_product_fields_and_engine_identity(self):
        result = self.prepare()
        self.assertIn('MOZ_APP_DISPLAYNAME="PrivacyBrowser"', (self.target / "configure.sh").read_text())
        mozconfig = (self.source / "mozconfig").read_text()
        self.assertIn("--with-app-name=privacybrowser", mozconfig)
        self.assertIn("--with-app-basename=PrivacyBrowser", mozconfig)
        self.assertIn("export MOZ_APP_REMOTINGNAME=privacybrowser", mozconfig)
        self.assertNotIn("export MOZ_APP_VENDOR", mozconfig)
        self.assertNotIn("export MOZ_APP_PROFILE", mozconfig)
        browser_configure = (self.source / "browser/moz.configure").read_text()
        self.assertIn('imply_option("MOZ_APP_VENDOR", "PrivacyBrowser")', browser_configure)
        self.assertIn('imply_option("MOZ_APP_PROFILE", "PrivacyBrowser")', browser_configure)
        self.assertIn('imply_option("MOZ_APP_ID", "{ec8030f7-c20a-464f-9b0e-13a3a9e97384}")', browser_configure)
        module = (self.source / "browser/app/module.ver").read_bytes()
        for field in ("DESCRIPTION", "PRODUCTNAME", "NAME"):
            self.assertIn(f"WIN32_MODULE_{field}=PrivacyBrowser\n".encode(), module)
        self.assertIn(b"WIN32_MODULE_COMPANYNAME=PrivacyBrowser contributors\n", module)
        self.assertIn(b"WIN32_MODULE_ORIGINAL_FILENAME=privacybrowser.exe\n", module)
        pbproxy = (self.source / "browser/app/pbproxy/module.ver").read_bytes()
        self.assertIn(b"WIN32_MODULE_PRODUCTNAME=PrivacyBrowser\n", pbproxy)
        self.assertIn(b"WIN32_MODULE_COMPANYNAME=PrivacyBrowser contributors\n", pbproxy)
        self.assertIn(b"WIN32_MODULE_ORIGINAL_FILENAME=private_browsing.exe\n", pbproxy)
        for line in self.module.splitlines(keepends=True):
            if any(token in line for token in (b"COPYRIGHT", b"TRADEMARKS", b"PRODUCTVERSION")):
                self.assertIn(line, module)
        self.assertEqual((self.source / "browser/app/firefox.exe.manifest").read_bytes(), MANIFEST)
        self.assertEqual((self.source / "browser/app/privacybrowser.exe.manifest").read_bytes(),
                         MANIFEST.replace(b'name="Firefox"', b'name="PrivacyBrowser"').replace(b'<description>Firefox</description>', b'<description>PrivacyBrowser</description>'))
        self.assertFalse(result["compiled"])
        self.assertEqual(result["app_name"], "PrivacyBrowser")
        self.assertEqual(result["native_binary"], "privacybrowser.exe")
        self.assertEqual(result["engine_version"], result["upstream_version"].removesuffix("esr"))
        self.assertEqual(result["icon_sha256"], hashlib.sha256((self.overlay / "assets/privacybrowser.ico").read_bytes()).hexdigest())

    def test_exact_ui_names_preserve_mozilla_legal_attributions(self):
        self.prepare()
        for relative in ("configure.sh", "locales/en-US/brand.ftl", "locales/en-US/brand.properties", "branding.nsi"):
            text = (self.target / relative).read_text()
            self.assertTrue(text.startswith(LEGAL))
            self.assertNotIn('="Privacy Browser"', text)
        ftl = (self.target / "locales/en-US/brand.ftl").read_text()
        self.assertIn("## Firefox and Mozilla Brand", ftl)
        self.assertEqual(sum(line.startswith("-brand-") and line.endswith(" = PrivacyBrowser") for line in ftl.splitlines()), 5)
        self.assertIn("-vendor-short-name = PrivacyBrowser contributors", ftl)
        nsi = (self.target / "branding.nsi").read_text()
        self.assertIn('!define BrandFullNameInternal "PrivacyBrowser"', nsi)
        self.assertNotIn("Mozilla Developer Preview", nsi)
        self.assertIn('!define CertNameDownload ""', nsi)
        self.assertNotIn("https://download.mozilla.org", nsi)
        self.assertNotIn("https://nightly.mozilla.org", (self.target / "pref/firefox-branding.js").read_text())

    def test_every_native_icon_matches_supplied_independent_asset(self):
        self.prepare()
        expected = (self.overlay / "assets/privacybrowser.ico").read_bytes()
        icons = list(self.target.rglob("*.ico"))
        self.assertEqual(len(icons), 6)
        for path in icons:
            self.assertEqual(path.read_bytes(), expected, path.name)
        with Image.open(self.target / "default128.png") as icon, Image.open(self.overlay / "assets/privacybrowser.png") as png:
            self.assertEqual(icon.convert("RGBA").tobytes(), png.convert("RGBA").resize((128, 128), Image.Resampling.LANCZOS).tobytes())
        with Image.open(self.target / "wizard.bmp") as wizard:
            self.assertEqual(wizard.format, "BMP")
            self.assertEqual(wizard.size, (164, 314))
            self.assertNotEqual(wizard.getpixel((0, 0)), (255, 0, 0))

    def test_svg_and_start_menu_assets_are_independent(self):
        self.prepare()
        for path in (self.target / "content").glob("*.svg"):
            text = path.read_text()
            self.assertIn("PrivacyBrowser", text)
            self.assertNotIn("Nightly", text)
            self.assertNotIn("Firefox", text)
        common = (self.source / "browser/branding/branding-common.mozbuild").read_text()
        self.assertIn('CONFIG["MOZ_APP_NAME"] + ".VisualElementsManifest.xml"', common)
        self.assertFalse((self.target / "firefox.VisualElementsManifest.xml").exists())
        self.assertTrue((self.target / "privacybrowser.VisualElementsManifest.xml").is_file())
        self.assertIn("@BINPATH@/@MOZ_APP_NAME@.VisualElementsManifest.xml", (self.source / "browser/installer/package-manifest.in").read_text())

    def test_linux_preparation_keeps_distinct_binary_evidence(self):
        result = self.prepare("linux")
        self.assertEqual(result["native_binary"], "privacybrowser")
        self.assertEqual((self.source / "mozconfig").read_bytes(), (ROOT / "config/mozconfig.linux").read_bytes())

    def test_default_browser_lookup_uses_independent_registration_names(self):
        self.prepare()
        windows = (self.source / "browser/components/shell/WindowsDefaultBrowser.cpp").read_text()
        self.assertIn('#define APP_REG_NAME_BASE L"PrivacyBrowser-"', windows)
        shell = (self.source / "browser/components/shell/nsWindowsShellService.cpp").read_text()
        for suffix in ("URL", "HTML", "PDF"):
            self.assertIn(f'FormatProgID(L"PrivacyBrowser{suffix}", aumid.get())', shell)
            self.assertNotIn(f'FormatProgID(L"Firefox{suffix}"', shell)
        self.assertTrue(windows.startswith(LEGAL))

    def test_unknown_shell_identity_layout_is_fail_closed(self):
        (self.source / "browser/components/shell/WindowsDefaultBrowser.cpp").write_text(LEGAL, encoding="utf-8", newline="\n")
        self.assert_rejected_unchanged()

    def test_startup_error_caption_is_exact_product_name(self):
        original = (self.source / "browser/app/nsBrowserApp.cpp").read_text()
        self.prepare()
        result = (self.source / "browser/app/nsBrowserApp.cpp").read_text()
        self.assertEqual(result, original.replace('messageBoxW(nullptr, wide_msg, L"Firefox",',
                                                 'messageBoxW(nullptr, wide_msg, L"PrivacyBrowser",'))
        self.assertIn("// Firefox attribution remains unchanged.", result)

    def test_changed_startup_error_caption_layout_is_fail_closed(self):
        path = self.source / "browser/app/nsBrowserApp.cpp"
        original = path.read_text()
        for value in (original.replace('L"Firefox"', 'L"Other"'), original + original):
            with self.subTest(value=value):
                path.write_text(value, encoding="utf-8", newline="\n")
                self.assert_rejected_unchanged()

    def test_upstream_installer_and_default_agent_disabled_for_this_product(self):
        self.prepare()
        root = (self.source / "moz.configure").read_text()
        self.assertIn('when=target_is_windows & depends(moz_app_basename)(lambda name: name != "PrivacyBrowser"),', root)
        self.assertIn("--disable-default-browser-agent", (self.source / "mozconfig").read_text())
        self.assertNotIn("--disable-installer", (self.source / "mozconfig").read_text())

    def test_changed_nsis_discovery_is_fail_closed(self):
        path = self.source / "moz.configure"
        path.write_text(path.read_text().replace('bootstrap="nsis/bin"', 'bootstrap="changed/nsis"'), encoding="utf-8", newline="\n")
        self.assert_rejected_unchanged()

    def test_missing_native_resource_is_fail_closed(self):
        (self.brand / "firefox.ico").unlink()
        self.assert_rejected_unchanged()

    def test_missing_jar_asset_is_fail_closed(self):
        path = self.brand / "content/jar.mn"
        path.write_text(path.read_text() + "  content/branding/newly-required.png\n", encoding="utf-8", newline="\n")
        self.assert_rejected_unchanged()

    def test_engine_version_resource_changes_need_review(self):
        path = self.source / "browser/app/module.ver"
        path.write_bytes(path.read_bytes().replace(b"@MOZ_APP_WINVERSION@", b"0,1,0,0"))
        self.assert_rejected_unchanged()

    def test_missing_or_duplicate_product_key_is_fail_closed(self):
        path = self.brand / "configure.sh"
        original = path.read_text()
        for value in (LEGAL, original + "MOZ_APP_DISPLAYNAME=Other\n"):
            with self.subTest(value=value):
                path.write_text(value, encoding="utf-8", newline="\n")
                self.assert_rejected_unchanged()

    def test_missing_native_metadata_field_is_fail_closed(self):
        path = self.source / "browser/app/module.ver"
        path.write_bytes(self.module.replace(b"WIN32_MODULE_PRODUCTNAME=@MOZ_APP_DISPLAYNAME@\n", b""))
        self.assert_rejected_unchanged()

    def test_unknown_svg_is_fail_closed(self):
        (self.brand / "content/new-logo.svg").write_text("<svg/>", encoding="utf-8", newline="\n")
        self.assert_rejected_unchanged()

    def test_corrupt_upstream_image_is_fail_closed(self):
        (self.brand / "default16.png").write_bytes(b"broken image")
        self.assert_rejected_unchanged()

    def test_mismatched_custom_ico_is_fail_closed(self):
        Image.new("RGBA", (256, 256), "purple").save(self.overlay / "assets/privacybrowser.ico", sizes=sorted(prepare_source.ICON_SIZES))
        self.assert_rejected_unchanged()

    def test_wrong_esr_version_is_fail_closed(self):
        (self.source / "browser/config/version.txt").write_text("1.0\n", encoding="utf-8", newline="\n")
        self.assert_rejected_unchanged()

    def test_unknown_target_does_not_mutate_source(self):
        before = snapshot(self.source)
        with self.assertRaises(ValueError):
            self.prepare("macos")
        self.assertEqual(snapshot(self.source), before)

    def test_repeat_preparation_rejected_without_changes(self):
        self.prepare()
        before = snapshot(self.source)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(snapshot(self.source), before)

    @unittest.skipIf(sys.platform == "win32", "Symlink fixture requires POSIX permissions")
    def test_symlinked_native_metadata_is_rejected(self):
        path = self.source / "browser/app/module.ver"
        outside = Path(self.temporary.name) / "external-module.ver"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        self.assert_rejected_unchanged()
        self.assertEqual(outside.read_bytes(), self.module)


if __name__ == "__main__":
    unittest.main()
