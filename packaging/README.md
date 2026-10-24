# Development packaging and future source releases

Privacy Browser v0.1.0 currently provides experimental Firefox ESR chrome and
source integration tools. A developer runtime overlay is not an independently
compiled browser. Native branding, executable metadata and signatures in that
runtime remain upstream's. The distributable bootstrap DEB contains this
project's code only and requires a separately obtained matching ESR runtime.

## Local Linux developer adapter

```sh
python tools/package_deb.py --bootstrap-only --output dist/privacy-browser-dev-bootstrap_0.1.0~dev1_all.deb
sudo dpkg -i dist/privacy-browser-dev-bootstrap_0.1.0~dev1_all.deb
privacy-browser-dev --runtime /absolute/path/to/firefox-esr
sudo dpkg -r privacy-browser-dev-bootstrap
```

Python 3.11 or newer is required. `firefox-esr` is recommended, since Ubuntu's
default repositories may not provide that package; a separately extracted
official Mozilla ESR runtime is accepted. The exact ESR version is pinned in
`config/esr.json`. The launcher requires `application.ini` to identify an ESR
source repository and the expected version. This check detects accidental
version mismatch; it is not a substitute for verifying Mozilla's signatures.

The launcher leaves the system runtime untouched, creates a developer runtime
copy in `$XDG_CACHE_HOME/privacybrowser-dev/`, and uses a separate profile in
`$XDG_CONFIG_HOME/privacybrowser-dev/profile`. Defaults are `~/.cache` and
`~/.config`. Removing the package keeps user profiles and the private runtime
copy; users may delete those two directories explicitly. Downloads outside the
profile are retained. This cache contains executable application files, not a
promise that browser storage never reaches disk.

`tools/build_dev.py --runtime /path/to/firefox --output /tmp/privacybrowser-dev`
also prepares a full local developer runtime for testing. `package_deb.py
--runtime` can package that copy for private testing, but such a package is not
the independently branded release artifact and must not be promoted by CI.
Mozilla's built-in updater is disabled in all modified copies to prevent
upstream updates from replacing the overlay. Current project updates are manual.

## Verified upstream source acquisition

```sh
python tools/provision_upstream_key.py --output upstream/mozilla-release.gpg
python tools/update_esr.py fetch --version 153.5.1esr --kind source \
  --destination upstream --keyring upstream/mozilla-release.gpg \
  --fingerprint 14F26682D0916CDD81E37B6D61B7B526D98F0353 \
  --signing-fingerprint 827E658608679618CD349F93678E455D76767AA3
python tools/extract_source.py \
  --archive upstream/firefox-153.5.1esr.source.tar.xz \
  --verification upstream/firefox-153.5.1esr.source.tar.xz.verification.json \
  --destination upstream/source
python tools/prepare_source.py --source upstream/source --target linux
cd upstream/source
./mach bootstrap --application-choice browser
./mach build
./mach package
```

The key provisioner checks an independently reviewed primary fingerprint and
the replacement signing subkey published by Mozilla on 2026-08-10. Verification
uses an isolated GPG home and exact fingerprint pins. A key rotation stops
downloads until the pins are reviewed. HTTPS metadata is advisory; it never
substitutes for the signed `SHA512SUMS` and the archive's SHA-512 hash.

The source preparer copies custom names and icons into a separate branding tree,
adds exact installer manifest entries, and adds a native SessionFile read/write
guard. Unknown session source layouts stop preparation for a manual conflict
review. The native guard preserves Firefox's shutdown notification when writes
are disabled. The existing developer overlay cannot inject this native guard
into a signed prebuilt Gecko binary. Clean-shutdown cleanup in the overlay has
weaker guarantees and must be described as such.

## Windows

Run the same verified acquisition and source preparation with `--target windows`
inside a provisioned MozillaBuild/Visual Studio environment on Windows 11 x64.
Use `./mach build` and `./mach package` to produce the native source build and
Mozilla's installer/uninstaller with independent branding. These steps have not
been executed in the Linux development container.

`packaging/windows/privacy-browser-dev.nsi` is an untested developer-only NSIS
installer scaffold. It is not a signed production installer, updater, or proof
of successful installation. No Windows binary is claimed without executing a
Windows build and installation test.

## CI and release blocking

`quality.yml` runs portable Python/Node tests on Windows and Linux. `esr-watch.yml`
records daily advisory update observations. `source-build.yml` is a manually
dispatched full-source candidate build on provisioned self-hosted Linux/Windows
runners. Candidates are retained as unsigned, unpublished workflow artifacts.
The workflows have been written but were not run on GitHub in this development
session. No workflow has publication credentials or a publication action.

`tools/release_gate.py` explicitly rejects developer artifacts and remains
unconditionally closed because the project has no production signing/update
deployment. Empty project signing allowlists, missing Windows Authenticode,
signed APT hosting, Windows signed update/rollback and cross-platform test
evidence block publication. A future implementation must replace this scaffold
with actual signature verification and a tested update service; changing JSON
flags alone cannot enable releases. No signing secrets are included.
