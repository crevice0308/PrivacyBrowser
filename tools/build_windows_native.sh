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
export MOZ_AUTOMATION=1
export MACH_TELEMETRY_NO_SUBMIT=1
export MOZ_CRASHREPORTER_NO_REPORT=1
export MOZ_FETCHES_DIR="$work_windows/mozbuild"
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
    set +e
    "$@" 2>&1 | tee "$evidence/$phase.log"
    statuses=("${PIPESTATUS[@]}")
    set -e
    printf '{"schema_version":1,"phase":"%s","command_exit_code":%s,"tee_exit_code":%s}\n' \
        "$phase" "${statuses[0]}" "${statuses[1]}" > "$evidence/$phase-exit.json"
    printf 'PrivacyBrowser phase ended: %s; command=%s; tee=%s\n' \
        "$phase" "${statuses[0]}" "${statuses[1]}"
    if (( statuses[0] != 0 )); then return "${statuses[0]}"; fi
    return "${statuses[1]}"
}

# Preserve the exact submitted mozconfig so bootstrap cannot silently change
# branding, object-directory placement, or turn this into an artifact build.
cp mozconfig "$evidence/mozconfig.before-bootstrap"
# The pinned auto-bootstrap sandbox swallows configure errors in StringIO.
# This logging-only wrapper runs the unchanged mach with those exact arguments.
run_logged bootstrap python3 "$script_dir/run_mach_bootstrap.py"
run_logged bootstrap-config-check cmp mozconfig "$evidence/mozconfig.before-bootstrap"

# These are genuine full C++/Rust/Gecko compilation and packaging commands.
run_logged build ./mach build
run_logged package ./mach package
run_logged xpcshell ./mach xpcshell-test toolkit/components/places/tests/unit/test_isURIVisited.js

# This marker is created only after all three commands exit successfully;
# evidence generation requires it and hashes the separate retained logs.
printf '%s\n' '{"schema_version":1,"commands":[{"argv":["./mach","build"],"exit_code":0},{"argv":["./mach","package"],"exit_code":0},{"argv":["./mach","xpcshell-test","toolkit/components/places/tests/unit/test_isURIVisited.js"],"exit_code":0}]}' > "$evidence/commands.json"
