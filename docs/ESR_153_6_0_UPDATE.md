# ESR 153.6.0 security update — 2026-10-27

## Scope and authoritative evidence

This continues existing draft PR #2 on its existing branch, stacked on PR #1;
no duplicate PR, main update, merge, signature, or release is involved.
Firefox ESR/Gecko, the independent UI, privacy defaults, disabled native updater,
and all source/native acceptance gates are retained.

Mozilla's [current-version metadata](https://product-details.mozilla.org/1.0/firefox_versions.json)
reports `FIREFOX_ESR=153.6.0esr`. [MFSA2026-120](https://www.mozilla.org/en-US/security/advisories/mfsa2026-120/),
announced October 27 and rated high, includes same-origin bypass, sandbox escape,
privilege escalation and memory-safety fixes. The release-notes page was still
being prepared when inspected; it is not used as proof of source authenticity.
The adopted ESR series stays 153; this is not a major-engine migration.

## Authenticity and compatibility

The complete official source archive was downloaded through `tools/update_esr.py
fetch`. The detached checksum signature was verified against the unchanged
reviewed Mozilla primary and signing-subkey pins, and the archive SHA-512 matched.
The safe extractor rehashed the archive before extraction. No unsigned mirror,
new trust identity, or relaxed hash check was substituted.

- Source: `https://archive.mozilla.org/pub/firefox/releases/153.6.0esr/source/firefox-153.6.0esr.source.tar.xz`
- SHA-512: `3e9f1f8e7c14d1994f4896702a50b46c1a3c7c7b696a080112f5ab3fdf0fcdd41ade4e81f9abafe2d3824cf1dfb16b5c09e5eaec12a05a733906f1483395565d`
- Primary: `14F26682D0916CDD81E37B6D61B7B526D98F0353`
- Signing subkey: `827E658608679618CD349F93678E455D76767AA3`
- Evidence: `tests/results/esr-153.6.0-source-verification.json`.

All three reviewed Windows bootstrap/mach/Rust-definition SHA-256 values match
this authenticated source byte-for-byte. MozillaBuild 4.2.1, Rust 1.94.1 and their
integrity gates are unchanged. The exact vendored downloader patch also matches
both its before and after hashes. The explicit developer channel and telemetry
policy from PR #1 remain in place; no runtime preference unlocking is introduced.

Full fresh source preparation passed for Windows and Linux: independent branding,
source-layout anchors, session guard, custom UI and privacy overlay were applied.
Their retained preparation records explicitly say `compiled:false`. A small `/tmp`
volume initially ran out of space; its failed extraction cleaned itself, then the
same verified archive extracted successfully onto the larger workspace volume.
No partial tree was accepted or used for preparation.

## Validation boundary

The new source-evidence regression checks that the adopted version, official URL,
signature identities, toolchain pins, downloader patch and both target preparation
records remain synchronized. It does not replace cryptographic verification or
native execution. Current commands/examples and metadata observation date are
updated; prior source/runtime evidence is retained as historical version-specific
evidence, not rewritten to claim a new-version pass.

A fresh branch-only full native Windows run is required for this exact update.
The successful 153.5.1 run `40069406598` does not validate 153.6.0. Native results
and artifact inspection will be recorded in PR #2 after completion. Until then,
compilation, startup/rendering, Places, PE hardening and installer acceptance for
153.6.0 are pending, not inferred from portable tests. `release_allowed=false`
and all signing/publication restrictions remain unchanged.

## First native attempt and reviewed inspector refresh

[Run40604947567](https://github.com/crevice0308/PrivacyBrowser/actions/runs/40604947567)
at `7e10f8d` completed bootstrap, full Gecko compilation and packaging successfully.
Its source evidence matches the signed153.6.0 archive above. The91-file native
identity/resource/overlay static checks passed, but the separate PE-hardening
inspection correctly refused a winchecksec binary differing from the previously
reviewed hash. Runtime/Places/installer checks were not reached; no candidate was
uploaded. Diagnostics archive12788922549 SHA-256 was independently verified as
`cb69703daa0069464571bce5911c85fe80ba0accbb9f9b1d44cfd20d164bffac`.

The new tool hash was **not** accepted solely because the failed run reported it.
The authenticated ESR source independently resolved official Mozilla toolchain
task `E68VbgMJScqsU_6wWla6Bw`. Its separately downloaded archive SHA-256 matched
same-task official HTTPS chain-of-trust metadata; that metadata's task definition
also exactly matched the official task API. The archive contains only the283648-byte
`winchecksec/winchecksec.exe`, whose SHA-256 matches the failed runner's observation.
Its source-fetch dependency explicitly uses the unchanged reviewed upstream
revision `93ffe67dbfc757bf6f440d80b8acf88e652ed60a`. Source definitions, build recipe
and JSON/check schema are byte-identical between153.5.1 and153.6.0; no checker
source/schema change was found. The precise reason for the changed build bytes
is not claimed as independently reproduced.

The exact reviewed executable pin is now
`e63d6ccac4402d17494c60826a52051b50c7a2861b60742e3ed090a41f8a9277`.
`tests/results/winchecksec-2026-10-28-review.json` retains task/archive/source
identities and hashes. Metadata trust is official Mozilla HTTPS; a detached CoT
signature and reproducible binary build were **not** independently verified.
The downloaded EXE was inspected only as data locally, never executed.

The same strict pin is now checked immediately after bootstrap **before** expensive
compilation, and again before post-package inspection. Unknown hashes still fail;
all eight PE checks, source/trust pins, runtime/installer gates and nonpublication
policy remain unchanged. A fresh native run must validate the repaired exact tree;
the failed first attempt is not runtime acceptance evidence.
