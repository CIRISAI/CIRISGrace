#!/usr/bin/env bash
# Drop-in for the legacy cargo_guard.sh: the same call shape, run through
# `grace sched` (CIRISGrace) instead of a slot flock.
#
#   cargo_guard.sh cargo test --features sqlite ...
#
# Owner: $GRACE_SCHED_OWNER, else the checkout's directory name. Peak:
# $GRACE_SCHED_PEAK_GB if set, else what this kind of job reached before
# (a first job keeps the legacy 11 GB cap). Kind: $GRACE_SCHED_KIND, else the
# directory plus the real command's first three words. Priority: $GRACE_SCHED_PRIORITY (critical|normal|low).
# Old knobs: CARGO_GUARD_MAX_GB becomes the cap, CARGO_GUARD_WAIT_SECS the wait.
set -uo pipefail
[ $# -ge 1 ] || { echo "usage: $0 <cargo|maturin|...> args..." >&2; exit 77; }
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# The job's kind keys its learned peak, so it names the real command: leading
# wrappers (nice -n 19, ionice -c3, env A=b, timeout 600, ...) are skipped.
# Every session called `cargo_guard.sh nice -n 19 ionice -c3 cargo ...`, which
# keyed all their history as "nice -n 19" (edge, 2026-11-19).
real=("$@")
while [ ${#real[@]} -gt 1 ]; do
  case "${real[0]}" in
    nice|ionice|env|timeout|stdbuf|chrt|taskset|setsid|-*|[0-9]*|*=*) real=("${real[@]:1}") ;;
    *) break ;;
  esac
done
kind="${GRACE_SCHED_KIND:-$(basename "$PWD"): ${real[0]##*/} ${real[1]:-} ${real[2]:-}}"
args=(run --owner "${GRACE_SCHED_OWNER:-$(basename "$PWD")}" --priority "${GRACE_SCHED_PRIORITY:-normal}"
      --kind "$kind")
[ -n "${GRACE_SCHED_PEAK_GB:-}" ] && args+=(--peak "$GRACE_SCHED_PEAK_GB")
[ -n "${CARGO_GUARD_MAX_GB:-}" ] && args+=(--cap "$CARGO_GUARD_MAX_GB")
[ -n "${CARGO_GUARD_WAIT_SECS:-}" ] && args+=(--wait "$CARGO_GUARD_WAIT_SECS")
PYTHONPATH="$here/..${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m grace.sched "${args[@]}" -- "$@"
