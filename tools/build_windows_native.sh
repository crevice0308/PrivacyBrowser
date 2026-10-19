#!/usr/bin/env bash
# Invoked only by the reviewed PowerShell runner inside official MozillaBuild.
set -euo pipefail
: "${PB_WORK_UNIX:?Missing fresh hosted-runner working directory}"
cd "$PB_WORK_UNIX/source"
evidence="$PB_WORK_UNIX/evidence"
work_windows="$(cygpath -m "$PB_WORK_UNIX")"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

export MOZBUILD_STATE_PATH="$work_windows/mozbuild"
export MOZCONFIG="$work_windows/source/mozconfig"
# Upstream MOZ_AUTOMATION requires a VCS checkout; this is a verified source
# archive. Clear only in this child shell, preserving the later runtime flag.
unset MOZ_AUTOMATION MOZ_FETCHES_DIR
export MACH_TELEMETRY_NO_SUBMIT=1
export MOZ_CRASHREPORTER_NO_REPORT=1
export SCCACHE_DIR="$work_windows/sccache"
export CARGO_HOME="$work_windows/cargo"
export CARGO_TARGET_DIR="$work_windows/cargo-target"
export TMPDIR="$PB_WORK_UNIX/tmp"
export PIP_CACHE_DIR="$work_windows/pip-cache"
export PIP_DISABLE_PIP_VERSION_CHECK=1

run_logged() {
    local phase="$1"
    shift
    local -a statuses
    printf 'PrivacyBrowser phase started: %s\n' "$phase"
    printf '::notice title=PrivacyBrowser phase::Started %s\n' "$phase"
    set +e
    "$@" 2>&1 | tee "$evidence/$phase.log"
    statuses=("${PIPESTATUS[@]}")
    set -e
    printf '{"schema_version":1,"phase":"%s","command_exit_code":%s,"tee_exit_code":%s}\n' \
        "$phase" "${statuses[0]}" "${statuses[1]}" > "$evidence/$phase-exit.json"
    printf 'PrivacyBrowser phase ended: %s; command=%s; tee=%s\n' \
        "$phase" "${statuses[0]}" "${statuses[1]}"
    printf '::notice title=PrivacyBrowser phase::Ended %s; command=%s; tee=%s\n' \
        "$phase" "${statuses[0]}" "${statuses[1]}"
    if (( statuses[0] != 0 )); then return "${statuses[0]}"; fi
    return "${statuses[1]}"
}

# Preserve the exact submitted mozconfig so bootstrap cannot silently change
# branding, object-directory placement, or turn this into an artifact build.
cp mozconfig "$evidence/mozconfig.before-bootstrap"

# Non-automation configure does not search the automation Rust bootstrap slot.
# Fetch the reviewed task from this verified source's toolchain definition,
# never silently substitute a hosted PATH/rustup compiler. The upstream fetch
# verifies Taskcluster artifact metadata; retain its URL/hash manifest too.
: "${PB_RUST_TASK:?Missing reviewed source-defined Rust task}"
: "${PB_RUST_VERSION:?Missing reviewed Rust version}"
download_rust_toolchain() (
    mkdir "$PB_WORK_UNIX/rust-toolchain" &&
    cd "$PB_WORK_UNIX/rust-toolchain" &&
    "$PB_WORK_UNIX/source/mach" --no-interactive artifact toolchain \
        --from-build "$PB_RUST_TASK" \
        --artifact-manifest "$work_windows/evidence/rust-toolchain-artifacts.json"
)
run_logged rust-toolchain download_rust_toolchain
run_logged rust-integrity python3 "$script_dir/verify_rust_toolchain.py"
rust_bin="$PB_WORK_UNIX/rust-toolchain/rustc/bin"
run_logged rust-binary-hashes sha256sum "$rust_bin/rustc.exe" "$rust_bin/cargo.exe"
run_logged rustc-version "$rust_bin/rustc.exe" --version --verbose
run_logged cargo-version "$rust_bin/cargo.exe" --version --verbose
run_logged rust-version-check grep -Fx -e "release: $PB_RUST_VERSION" \
    -e "release: $PB_RUST_VERSION"$'\r' "$evidence/rustc-version.log"
run_logged rust-host-check grep -Fx -e 'host: x86_64-pc-windows-msvc' \
    -e $'host: x86_64-pc-windows-msvc\r' "$evidence/rustc-version.log"
export RUSTC="$(cygpath -m "$rust_bin/rustc.exe")"
export CARGO="$(cygpath -m "$rust_bin/cargo.exe")"

# The pinned auto-bootstrap sandbox swallows configure errors in StringIO.
# This logging-only wrapper runs the unchanged mach with those exact arguments.
run_logged bootstrap python3 "$script_dir/run_mach_bootstrap.py"
run_logged bootstrap-config-check cmp mozconfig "$evidence/mozconfig.before-bootstrap"

# Archive mode permits missing Windows auxiliary tools. Keep winchecksec
# mandatory so upstream PE mitigation checks are not silently omitted.
run_logged winchecksec-hash sha256sum "$PB_WORK_UNIX/mozbuild/winchecksec/winchecksec.exe"
export WINCHECKSEC="$work_windows/mozbuild/winchecksec/winchecksec.exe"
# PDBSTR is upstream's optional PDB source-indexing tool in source-archive
# mode. Configure records its detection; we do not claim source-indexed PDBs.
printf '%s\n' '{"schema_version":1,"source_kind":"verified-source-archive","mozilla_ci_mode":false,"warnings_as_errors":true,"winchecksec_required":true,"pdbstr_policy":"upstream optional source-indexing tool; detection recorded in configure output","source_indexed_pdbs_claimed":false}' > "$evidence/source-archive-build-policy.json"

# These are genuine full C++/Rust/Gecko compilation and packaging commands.
run_logged build ./mach build
run_logged package ./mach package
run_logged xpcshell ./mach xpcshell-test toolkit/components/places/tests/unit/test_isURIVisited.js

# This marker is created only after all three commands exit successfully;
# evidence generation requires it and hashes the separate retained logs.
printf '%s\n' '{"schema_version":1,"commands":[{"argv":["./mach","build"],"exit_code":0},{"argv":["./mach","package"],"exit_code":0},{"argv":["./mach","xpcshell-test","toolkit/components/places/tests/unit/test_isURIVisited.js"],"exit_code":0}]}' > "$evidence/commands.json"
