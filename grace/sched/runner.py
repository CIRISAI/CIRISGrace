"""Run one job: wait for admission, run it capped in its own scope, measure,
release.

- **Waiting** is a request file naming this waiter process. Every few seconds
  the waiter takes the mutex, reclaims dead holders, asks
  :func:`policy.decide`, and either moves itself to a lease or writes down why
  it still waits, so ``status`` can show the reason.
- **Running** happens in a transient systemd user scope named
  ``grace-sched-<id>``, as the legacy guard did:
  - ``MemoryMax`` caps the job and ``MemorySwapMax=0`` stops it swapping;
  - ``memory.oom.group=1`` makes an OOM kill the whole job, never one rustc,
    and never the terminal that launched it.
  Nothing outside the job's own scope is changed, the caller's unit included.
- **No cap, no run.** Admission reserves each job's cap, so a job without a
  hard cap could outgrow what was reserved and exhaust the box. When systemd
  user scopes are unavailable, ``run`` refuses (exit 78). Only with
  ``GRACE_SCHED_ALLOW_UNCAPPED=1`` does it run the job uncapped, and then it
  kills the job's own process group the moment its RSS passes the cap (a
  polled cap, weaker than the kernel's).
  The job runs as a child of this process, not ``exec``'d, so this process
  can sample it and release the lease when it exits. Nobody has to remember
  to report "done".
- **Measuring** polls the scope's own ``memory.peak``, the kernel's running
  maximum, so a spike that came and went between two polls is still counted
  (2026-11-19: a 4.9 GB conversion spike under a 4 GB grant slipped past a
  one-second RSS poll). The scope vanishes when the job exits, so the peak is
  read while it lives. Live use is read from ``memory.current`` by every
  other waiter, for its projection.
- **A cap kill is confirmed, not guessed.** Exit 137 alone could be an
  operator's SIGKILL or the host's OOM killer. A kill counts as "at its cap"
  only when the scope's own ``memory.events`` showed an OOM kill or systemd's
  ``Result`` for the unit is ``oom-kill``; only then is the cap recorded as
  the job's peak.
- **Killing** on ``--timeout`` targets this job's own scope only. A watchdog
  that ran ``pkill -f "cargo build --release"`` killed every session's
  release build on the box (2026-11-18); nothing here matches by name.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Dict, List, Optional

from . import policy as pol
from . import probe, state

EXIT_WAITED_TOO_LONG = 75
EXIT_USAGE = 77
EXIT_NO_CAP = 78


def _say(msg: str) -> None:
    print(f"grace-sched: {msg}", file=sys.stderr, flush=True)


def running_view(d) -> List[pol.Running]:
    out = []
    for v in state.entries(d, "leases"):
        used = None
        if v.get("cgroup"):
            used = probe.cgroup_gb(v["cgroup"])
        if used is None and v.get("pgid"):
            used = probe.rss_gb_of_group(int(v["pgid"]))
        out.append(pol.Running(v["id"], v["owner"], v["kind"], float(v["peak_gb"]), float(used or 0.0),
                               budget_gb=float(v.get("cap_gb") or v["peak_gb"])))
    # Builds under the legacy cargo_guard.sh never asked this scheduler, but
    # they use the same memory. Each counts as a running job whose peak is
    # its own cap, until every session has moved over.
    if os.environ.get("GRACE_SCHED_COUNT_LEGACY", "1") == "1":
        for g in probe.legacy_guard_scopes():
            out.append(pol.Running(str(g["unit"]), "legacy cargo_guard.sh", str(g["unit"]),
                                   float(g["cap_gb"]), float(g["used_gb"])))
    return out


def queue_view(d) -> List[pol.Request]:
    return [
        pol.Request(v["id"], v["owner"], v["kind"], float(v["peak_gb"]), v.get("priority", "normal"),
                    float(v.get("created", 0)), budget_gb=float(v.get("cap_gb") or v["peak_gb"]))
        for v in state.entries(d, "queue")
    ]


def machine(d) -> pol.Machine:
    return pol.Machine(probe.available_gb(), probe.disk_free_gb(os.environ.get("GRACE_SCHED_DISK", "/")))


def policy_from_env() -> pol.Policy:
    e = os.environ.get
    return pol.Policy(
        floor_gb=float(e("GRACE_SCHED_FLOOR_GB", 8)),
        heavy_gb=float(e("GRACE_SCHED_HEAVY_GB", 4)),
        disk_floor_gb=float(e("GRACE_SCHED_DISK_FLOOR_GB", 30)),
        max_running=int(e("GRACE_SCHED_MAX_RUNNING", 4)),
        starve_secs=float(e("GRACE_SCHED_STARVE_SECS", 1800)),
    )


def tick(d) -> pol.Decision:
    """One scheduling step, under the mutex: reclaim, decide, write reasons."""
    state.reap(d)
    res = state.reservation(d)
    dec = pol.decide(queue_view(d), running_view(d), machine(d), policy_from_env(),
                     reserved_for=(res or {}).get("owner"), now=time.time())
    for v in state.entries(d, "queue"):
        why = dec.waits.get(v["id"])
        if why and v.get("waiting_for") != why:
            v["waiting_for"] = why
            state.put(d, "queue", v)
    return dec


def _job_env(extra: Dict[str, str]) -> Dict[str, str]:
    env = dict(os.environ)
    # The box's build defaults: an incremental cache grew one tree to 133 GB
    # and filled the disk (2026-11-18); two jobs is the measured sweet spot.
    env.setdefault("CARGO_INCREMENTAL", "0")
    env.setdefault("CARGO_BUILD_JOBS", "2")
    env.setdefault("NEXTEST_TEST_THREADS", env["CARGO_BUILD_JOBS"])
    env.update(extra)
    return env


def default_cap(peak: float) -> float:
    return round(min(max(peak * 1.5, peak + 2.0), 14.0), 1)


def run(argv: List[str], owner: str, kind: str, declared_gb: Optional[float],
        priority: str = "normal", cap_gb: Optional[float] = None, wait_secs: float = 6 * 3600,
        timeout_secs: Optional[float] = None) -> int:
    if not argv:
        _say("nothing to run")
        return EXIT_USAGE
    if priority not in pol.PRIORITIES:
        _say(f"priority must be one of {', '.join(pol.PRIORITIES)}")
        return EXIT_USAGE
    scoped = probe.systemd_scopes_usable()
    if not scoped and os.environ.get("GRACE_SCHED_ALLOW_UNCAPPED") != "1":
        _say("systemd user scopes are unavailable, so no hard memory cap can be applied; refusing to run "
             f"({EXIT_NO_CAP}). Set GRACE_SCHED_ALLOW_UNCAPPED=1 to run under a polled RSS cap instead.")
        return EXIT_NO_CAP
    job_id = state.new_id()
    with state.locked() as d:
        peak, source = pol.estimate_peak(declared_gb, state.history(d, kind))
        cap = round(cap_gb, 1) if cap_gb else default_cap(peak)
        me = os.getpid()
        req = {"id": job_id, "owner": owner, "kind": kind, "peak_gb": peak, "peak_source": source,
               "cap_gb": cap, "scoped": scoped,
               "priority": priority, "created": time.time(), "pid": me,
               "pid_start": probe.start_time(me), "cmd": " ".join(argv)[:300], "cwd": os.getcwd()}
        state.put(d, "queue", req)
        state.event(d, "requested", id=job_id, owner=owner, kind=kind, peak_gb=peak, priority=priority)
    _say(f"{job_id} queued: {owner}:{kind}, peak {peak:g} GB ({source}), cap {cap:g} GB, {priority}")

    t0 = time.time()
    last_why = None
    try:
        while True:
            with state.locked() as d:
                dec = tick(d)
                if job_id in dec.start:
                    state.drop(d, "queue", job_id)
                    lease = {**req, "started": time.time(), "projected_gb_at_start": dec.projected_gb}
                    state.put(d, "leases", lease)
                    state.event(d, "started", id=job_id, owner=owner, kind=kind,
                                waited_secs=round(time.time() - t0), projected_gb=dec.projected_gb)
                    break
                why = dec.waits.get(job_id, "waiting")
            if why != last_why:
                _say(f"{job_id} waits: {why}")
                last_why = why
            if time.time() - t0 > wait_secs:
                with state.locked() as d:
                    state.drop(d, "queue", job_id)
                    state.event(d, "gave_up", id=job_id, owner=owner, kind=kind, why=why)
                _say(f"{job_id} waited {wait_secs / 3600:g} h; giving up ({EXIT_WAITED_TOO_LONG})")
                return EXIT_WAITED_TOO_LONG
            time.sleep(3)
    except KeyboardInterrupt:
        with state.locked() as d:
            state.drop(d, "queue", job_id)
        return 130

    return _execute(job_id, req, argv, cap, timeout_secs, t0, scoped)


def _record_job(job_id: str, **fields) -> None:
    with state.locked() as d:
        lease = next((v for v in state.entries(d, "leases") if v["id"] == job_id), None)
        if lease is not None:
            lease.update(fields)
            state.put(d, "leases", lease)


def _execute(job_id: str, req: Dict, argv: List[str], cap: float, timeout_secs: Optional[float],
             t_request: float, scoped: bool) -> int:
    unit = f"grace-sched-{job_id}"
    env = _job_env({"GRACE_SCHED_JOB": job_id})
    if scoped:
        cmd = ["systemd-run", "--user", "--scope", "-q", f"--unit={unit}",
               "-p", f"MemoryMax={cap:.1f}G", "-p", "MemorySwapMax=0", "--",
               "bash", "-c",
               'echo 1 > "/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)/memory.oom.group" 2>/dev/null; exec "$@"',
               "grace-sched", *argv]
    else:
        _say(f"systemd user scopes unavailable (GRACE_SCHED_ALLOW_UNCAPPED=1): no kernel cap; the job's "
             f"process group is killed when its RSS passes {cap:.1f} GB")
        cmd = argv
    _say(f"{job_id} running (cap {cap:.1f} GB): {' '.join(argv)[:200]}")
    started = time.time()
    child = subprocess.Popen(cmd, env=env, close_fds=True, start_new_session=True)
    # The process group first, at once: if this supervisor dies from here on,
    # the lease can still tell the job is alive (state.reap).
    _record_job(job_id, pgid=child.pid, unit=unit if scoped else None)
    cgroup = None
    if scoped:
        for _ in range(50):
            rel = probe.cgroup_of(child.pid)
            if rel and unit in rel:
                cgroup = rel
                break
            time.sleep(0.1)
        if cgroup:
            _record_job(job_id, cgroup=cgroup)

    def forward(signum, _frame):
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, forward)

    peak_seen = 0.0
    oom_seen = 0
    timed_out = False
    polled_cap_kill = False
    while child.poll() is None:
        # `memory.peak` is the kernel's running maximum for the scope, so each
        # read already includes a spike that came and went between reads. The
        # scope vanishes when the job exits, so it is read while it lives.
        if cgroup:
            used = probe.cgroup_gb(cgroup, "memory.peak") or probe.cgroup_gb(cgroup)
            oom_seen = max(oom_seen, probe.oom_kills(cgroup))
        else:
            used = probe.rss_gb_of_group(child.pid)
            if used > cap and not polled_cap_kill:
                polled_cap_kill = True
                _say(f"{job_id} passed its {cap:.1f} GB cap ({used:.2f} GB RSS): killing its own process group")
                forward(signal.SIGKILL, None)
        peak_seen = max(peak_seen, used or 0.0)
        if timeout_secs and time.time() - started > timeout_secs:
            timed_out = True
            _say(f"{job_id} over its {timeout_secs:g} s timeout: killing its own scope only")
            if scoped:
                subprocess.run(["systemctl", "--user", "kill", "--signal=SIGKILL", f"{unit}.scope"],
                               capture_output=True, close_fds=True)
            else:
                forward(signal.SIGKILL, None)
        time.sleep(0.2)
    rc = child.returncode
    kernel_peak = probe.cgroup_gb(cgroup, "memory.peak") if cgroup else None
    if cgroup:
        oom_seen = max(oom_seen, probe.oom_kills(cgroup))
    peak_gb = round(max(peak_seen, kernel_peak or 0.0), 2)
    killed = rc in (137, -9) and not timed_out
    # Exit 137 alone could be an operator's SIGKILL or the host's OOM killer.
    # A cap kill needs the kernel's word for THIS scope (memory.events, or
    # systemd's Result), or our own polled-cap kill.
    result = probe.unit_result(f"{unit}.scope") if scoped else ""
    oom = killed and (oom_seen > 0 or result == "oom-kill" or polled_cap_kill)
    if scoped:
        probe.reset_failed(f"{unit}.scope")
    if oom:
        # It reached at least the cap, whatever the last poll saw, and the
        # next estimate must learn that.
        peak_gb = max(peak_gb, round(cap, 2))
    secs = round(time.time() - started)
    with state.locked() as d:
        state.drop(d, "leases", job_id)
        state.record(d, kind=req["kind"], owner=req["owner"], peak_gb=peak_gb, secs=secs, rc=rc,
                     oom_killed=oom, timed_out=timed_out, cap_gb=round(cap, 1),
                     peak_estimate_gb=req["peak_gb"], at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        state.event(d, "finished", id=job_id, owner=req["owner"], kind=req["kind"], rc=rc,
                    secs=secs, peak_gb=peak_gb, waited_secs=round(started - t_request),
                    oom_killed=oom, timed_out=timed_out)
    note = " (killed at its memory cap)" if oom else (" (killed by SIGKILL from outside: not a cap kill)"
                                                     if killed else "")
    _say(f"{job_id} done: rc {rc}{note}, {secs} s, peak {peak_gb:g} GB (estimate {req['peak_gb']:g})")
    return 124 if timed_out else (rc if rc >= 0 else 128 - rc)
