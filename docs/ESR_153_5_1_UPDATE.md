# ESR 153.5.1 security update — 2026-10-23

## Reason and scope

Mozilla's [current-version metadata](https://product-details.mozilla.org/1.0/firefox_versions.json)
reports `FIREFOX_ESR=153.5.1esr`. The [release notes](https://www.firefox.com/en-US/firefox/153.5.1/releasenotes/)
and [MFSA2026-112](https://www.mozilla.org/en-US/security/advisories/mfsa2026-112/)
are dated October 20, 2026. The advisory rates CVE-2026-113470 high: this release
bundles the OneCRL block list for compromised eMudhra intermediate certificates,
including protection when RemoteSettings is disabled or unreachable. This update
does not enable RemoteSettings or change any privacy preference.

This is a security point update within the adopted ESR 153 series, not a major
engine migration. Mozilla's [ESR 153 launch announcement](https://groups.google.com/a/mozilla.org/g/announce/c/BJRxpec8_S0)
identified October 13, 2026 as the replacement date for ESR 140. Current metadata
still lists ESR 153 as the main ESR; no EOL-driven major migration is indicated.

The ESR-specific delta updates the engine pin, matching Windows reviewed-version
label, manual source-build default, and current instructions. Historical 153.5.0 runtime reports
remain unchanged: they are not evidence of running 153.5.1. ESR/Gecko, UI,
privacy defaults, signature enforcement, and `release_allowed=false` remain intact.

## Authenticity

The source was downloaded using `tools/update_esr.py fetch`, not merely inferred
from metadata. The official `SHA512SUMS.asc` signature and the complete downloaded
archive's SHA-512 were verified using the existing primary and current signing
subkey pins. Their identities were also checked against Mozilla's
[August 10 key announcement](https://blog.mozilla.org/security/2026/08/10/updated-gpg-key-for-signing-firefox-and-thunderbird-releases/).
No trust pin changed.

- Primary: `14F26682D0916CDD81E37B6D61B7B526D98F0353`
- Signing subkey: `827E658608679618CD349F93678E455D76767AA3`
- Source: `https://archive.mozilla.org/pub/firefox/releases/153.5.1esr/source/firefox-153.5.1esr.source.tar.xz`
- SHA-512: `9dccf8c5383653ddcf807b500889a91f9b9d56a512dab88a2683755ae6cb0c4b5d0788faa9541e5709a2b783002ee9f434020a43c8a43653038a2c3decd9f2c1`
- Machine-readable evidence: `tests/results/esr-153.5.1-source-verification.json`.

## Initial standalone update verification (before integration)

- Source syntax checks: 26 Python, 12 JavaScript, 4 XML passed.
- Linux portable Python suite: 413 tests, 407 passed, 6 skipped.
- Node suite: 61 passed.
- Full safely extracted source preparation passed for Linux and Windows targets,
  including branding, source-layout anchors, session guard and privacy overlay.
  This is source integration validation, not compilation or runtime validation.
- Metadata check after update: configured and observed `153.5.1esr`; no newer update.
- All three pinned Windows bootstrap/mach-bootstrap/Rust-definition SHA-256 hashes
  match the authenticated new source exactly; no hash pin was relaxed or changed.
- PR #1's exact `patched_source()` function was evaluated on the new downloader
  bytes without modifying either branch: input SHA-256
  `03b990385482f1dcffd6625028b1357a8f420ad19664ded0b038a5b0041f09e9`,
  output SHA-256 `af337035cc729acdce158fba7845584413dda1033ba89a3c66a91c7e1c5f1741`.
  Both match PR #1's fail-closed pins.

The initial standalone update did not include PR #1's Windows bootstrap repair.
Its 153.5.0 native results were not validation of this update. The subsequent
reviewed integration is described below; source compilation, runtime acceptance,
signing, publication and release readiness are not inferred from these earlier
source-only checks.

## Native integration validation

This update is stacked on draft PR #1, whose ESR 153.5.0 native run
[40055166027](https://github.com/crevice0308/PrivacyBrowser/actions/runs/40055166027)
passed at `add174ba829e0a3df47a87e664ebbeea2ce63ba8`. The update branch includes
that bootstrap failure guard and explicit developer-channel telemetry fix.
The nonpublishing validation workflow is restricted to this exact ESR update
branch in this repository; its permissions, pinned actions, full build and
privacy/security/installer gates remain unchanged. Main and PR #1 are untouched.

The previous successful native result does not validate ESR 153.5.1. A new full
native run on the combined update tree is required; its outcome must be recorded
in PR #2 before claiming Windows runtime compatibility. No automatic merge,
signing, update deployment or release publication is authorized by these tests.

Combined-tree offline checks: 437 Python tests (429 passed, 8 platform/artifact
skips), 61 Node tests, and syntax checks for 28 Python, 12 JavaScript and 4 XML
files passed. These are not substitutes for the pending native run.
