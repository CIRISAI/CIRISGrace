#!/usr/bin/env bash
# Drop-in for the legacy cargo_guard.sh: the same call shape, run through
# `grace sched` (CIRISGrace) instead of a slot flock.
#
#   cargo_guard.sh cargo test --features sqlite ...
#
# Owner: $GRACE_SCHED_OWNER, else the checkout's directory name. Peak:
# $GRACE_SCHED_PEAK_GB if set, else what this kind of job reached before
# (default 6 GB). Priority: $GRACE_SCHED_PRIORITY (critical|normal|low).
# Old knobs: CARGO_GUARD_MAX_GB becomes the cap, CARGO_GUARD_WAIT_SECS the wait.
set -uo pipefail
[ $# -ge 1 ] || { echo "usage: $0 <cargo|maturin|...> args..." >&2; exit 77; }
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
args=(run --owner "${GRACE_SCHED_OWNER:-$(basename "$PWD")}" --priority "${GRACE_SCHED_PRIORITY:-normal}"
      --kind "$(basename "$PWD"): $1 ${2:-} ${3:-}")
[ -n "${GRACE_SCHED_PEAK_GB:-}" ] && args+=(--peak "$GRACE_SCHED_PEAK_GB")
[ -n "${CARGO_GUARD_MAX_GB:-}" ] && args+=(--cap "$CARGO_GUARD_MAX_GB")
[ -n "${CARGO_GUARD_WAIT_SECS:-}" ] && args+=(--wait "$CARGO_GUARD_WAIT_SECS")
PYTHONPATH="$here/..${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m grace.sched "${args[@]}" -- "$@"
