"""grace sched — the shared box's build scheduler.

    grace sched run --owner edge --kind "edge check" --peak 6 [--priority critical] -- cargo check ...
    grace sched status [--json]       who runs, who waits and why, the box's headroom
    grace sched doctor [--json]       stuck waiters, unscheduled builds, legacy lock holders, disk
    grace sched reserve <owner> [--minutes 30]   promise the heavy slot to one owner
    grace sched unreserve
    grace sched history [--kind K]    measured peaks and durations

Exit codes for `run`: the job's own, 75 when it waited too long, 77 for
usage, 124 for its own timeout. A job killed at its memory cap exits 137.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from . import policy as pol
from . import probe, runner, state


def _age(t: float) -> str:
    s = int(time.time() - t)
    return f"{s // 3600}h{(s % 3600) // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def snapshot() -> Dict[str, Any]:
    with state.locked() as d:
        dec = runner.tick(d)
        res = state.reservation(d)
        leases = state.entries(d, "leases")
        queue = state.entries(d, "queue")
        m = runner.machine(d)
    run_view = {r.id: r for r in runner.running_view(d)}
    legacy = [r for r in run_view.values() if r.owner == "legacy cargo_guard.sh"]
    return {
        "available_gb": round(m.available_gb, 1),
        "disk_free_gb": round(m.disk_free_gb, 1),
        "projected_gb": dec.projected_gb,
        "policy": runner.policy_from_env().__dict__,
        "reservation": res,
        "running": [
            {**{k: v[k] for k in ("id", "owner", "kind", "peak_gb", "priority", "cmd") if k in v},
             "for": _age(v["started"]), "used_gb": round(run_view[v["id"]].used_gb, 2)
             if v["id"] in run_view else None}
            for v in leases
        ],
        "waiting": [
            {**{k: v[k] for k in ("id", "owner", "kind", "peak_gb", "priority") if k in v},
             "for": _age(v["created"]), "why": v.get("waiting_for") or dec.waits.get(v["id"], "")}
            for v in pol_order(queue)
        ],
        "starting_next": dec.start,
        "legacy_guard_builds": [{"unit": r.id, "cap_gb": r.peak_gb, "used_gb": round(r.used_gb, 2)}
                                for r in legacy],
    }


def pol_order(queue: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rank = {p: i for i, p in enumerate(pol.PRIORITIES)}
    return sorted(queue, key=lambda v: (rank.get(v.get("priority"), 9), v.get("created", 0)))


def cmd_status(a) -> int:
    s = snapshot()
    if a.json:
        print(json.dumps(s, indent=1, sort_keys=True))
        return 0
    print(f"box: {s['available_gb']} GB available, {s['projected_gb']} GB once running jobs peak, "
          f"{s['disk_free_gb']} GB disk free")
    if s["reservation"]:
        r = s["reservation"]
        print(f"reserved: the heavy slot is promised to {r['owner']} for "
              f"{max(0, int((r['until'] - time.time()) / 60))} more min (by {r.get('by')})")
    print("running:" if s["running"] else "running: nothing")
    for r in s["running"]:
        print(f"  {r['id']}  {r['owner']}:{r['kind']}  {r['for']}  using {r['used_gb']} of ~{r['peak_gb']} GB")
    for g in s["legacy_guard_builds"]:
        print(f"  (legacy guard) {g['unit']}  using {g['used_gb']} of its {g['cap_gb']} GB cap")
    print("waiting:" if s["waiting"] else "waiting: nobody")
    for w in s["waiting"]:
        print(f"  {w['id']}  {w['owner']}:{w['kind']}  [{w['priority']}]  {w['for']}  — {w['why']}")
    return 0


def cmd_doctor(a) -> int:
    s = snapshot()
    findings: List[str] = []
    for w in s["waiting"]:
        if not w["why"]:
            findings.append(f"{w['owner']}:{w['kind']} waits with no reason: a scheduler bug, report it")
    builds = probe.builds()
    by_guard: Dict[str, List[Dict[str, Any]]] = {"grace-sched": [], "cargo-guard": [], "none": []}
    for b in builds:
        by_guard[probe.guard_of(str(b["cgroup"]))].append(b)
    stray = [b for b in by_guard["none"] if b["exe"] != "sccache" or b["rss_gb"] >= 1.0]
    for b in stray:
        findings.append(f"unscheduled build: pid {b['pid']} {b['exe']} {b['rss_gb']} GB ({b['cmd'][:90]})")
    # The legacy guard's slot is an flock every process in its scope
    # inherits; that is the guard working. A holder OUTSIDE any guard scope
    # outlives the build and holds the slot until it dies (2026-11-19: a
    # memory sampler under systemd --user held slot 0 for an hour).
    legacy = probe.fd_holders(os.environ.get("GRACE_SCHED_LEGACY_LOCK", "/tmp/cargo_guard.lock"))
    leaked = [h for h in legacy if probe.guard_of(str(h["cgroup"])) == "none"]
    for h in leaked:
        findings.append(f"leaked legacy guard slot: pid {h['pid']} holds {h['file']} (fd {h['fd']}) outside any "
                        f"guard scope: {h['cmd'][:100]}. Ask its owner to end it (or close fd 9 before it starts)")
    legacy_scopes = sorted({str(b["cgroup"]).rsplit("/", 1)[-1] for b in by_guard["cargo-guard"]})
    if s["disk_free_gb"] < s["policy"]["disk_floor_gb"] * 2:
        big = []
        for p in sorted(Path.home().joinpath(".cache").glob("ciris-target-*")) + \
                sorted(Path.home().glob("CIRIS*/target")):
            big.append(str(p))
        findings.append(f"disk is low ({s['disk_free_gb']} GB); build output to look at first: {', '.join(big[:8])}")
    out = {"findings": findings, "unscheduled": stray, "leaked_legacy_slots": leaked,
           "legacy_guard_scopes": legacy_scopes,
           "scheduled_build_processes": len(by_guard["grace-sched"])}
    if a.json:
        print(json.dumps(out, indent=1, sort_keys=True))
    else:
        print("\n".join(findings) if findings else "doctor: nothing wrong")
        if legacy_scopes:
            print(f"note: {len(legacy_scopes)} build(s) under the legacy cargo_guard.sh, invisible to this "
                  f"scheduler's projection: {', '.join(legacy_scopes)}")
    return 1 if findings else 0


def cmd_history(a) -> int:
    d = state.root()
    rows = []
    try:
        for line in (d / "history.jsonl").read_text().splitlines():
            r = json.loads(line)
            if not a.kind or r.get("kind") == a.kind:
                rows.append(r)
    except OSError:
        pass
    for r in rows[-a.last:]:
        flag = " OOM" if r.get("oom_killed") else (" TIMEOUT" if r.get("timed_out") else "")
        print(f"{r.get('at', '')}  {r.get('owner')}:{r.get('kind')}  peak {r.get('peak_gb')} GB "
              f"(est {r.get('peak_estimate_gb')})  {r.get('secs')} s  rc {r.get('rc')}{flag}")
    return 0


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="grace sched", description="the shared box's build scheduler")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="wait for admission, then run a job capped in its own scope")
    r.add_argument("--owner", default=os.environ.get("GRACE_SCHED_OWNER") or Path.cwd().name)
    r.add_argument("--kind", default=None, help="what this job is, for history (default: cwd + command)")
    r.add_argument("--peak", type=float, default=None, help="expected peak GB (history may raise it)")
    r.add_argument("--priority", default="normal", choices=pol.PRIORITIES)
    r.add_argument("--cap", type=float, default=None, help="MemoryMax GB (default from the peak)")
    r.add_argument("--wait", type=float, default=6 * 3600, help="seconds to wait for admission")
    r.add_argument("--timeout", type=float, default=None, help="seconds the job may run")
    r.add_argument("argv", nargs=argparse.REMAINDER)
    for name in ("status", "doctor"):
        s = sub.add_parser(name)
        s.add_argument("--json", action="store_true")
    rs = sub.add_parser("reserve", help="promise the heavy slot to one owner")
    rs.add_argument("owner")
    rs.add_argument("--minutes", type=float, default=30)
    sub.add_parser("unreserve")
    h = sub.add_parser("history")
    h.add_argument("--kind", default=None)
    h.add_argument("--last", type=int, default=20)
    a = p.parse_args(argv)
    if a.cmd == "run":
        args = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
        kind = a.kind or f"{Path.cwd().name}: {' '.join(args[:3])}"
        return runner.run(args, a.owner, kind, a.peak, a.priority, a.cap, a.wait, a.timeout)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "doctor":
        return cmd_doctor(a)
    if a.cmd == "reserve":
        with state.locked() as d:
            v = state.reserve(d, a.owner, a.minutes, by=os.environ.get("GRACE_SCHED_OWNER", Path.cwd().name))
        print(f"the heavy slot is promised to {v['owner']} for {a.minutes:g} min")
        return 0
    if a.cmd == "unreserve":
        with state.locked() as d:
            state.unreserve(d)
        return 0
    if a.cmd == "history":
        return cmd_history(a)
    return 77


if __name__ == "__main__":
    sys.exit(main())
