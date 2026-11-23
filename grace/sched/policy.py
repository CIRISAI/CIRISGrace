"""Admission: who may start now, and in plain words why everyone else waits.

Pure functions over plain data, so every rule is a unit test and none needs
a machine. The rules are the shared box's lessons (2026-10 .. 2026-11), each
named where it is enforced:

- AT MOST ``max_heavy`` HEAVY JOBS AT ONCE (default 2). A heavy job is one
  whose expected peak is above ``heavy_gb``. Two UNCAPPED at once is how the
  kernel's OOM killer and systemd-oomd took whole terminal scopes, every agent
  in them; every job now runs hard-capped in its own scope and admission
  reserves each cap, so a second heavy job starts only when both caps fit.
- ADMIT ON PROJECTED MEMORY, NOT CURRENT. A build that has just started has
  not reached its peak. Projected available is ``available now - the
  headroom every running job has still to grow into`` (its expected peak
  minus what it uses now). Granting on ``free -g`` alone overcommitted three
  jobs and the low-memory reaper killed one at 92 %.
- KEEP A FLOOR. Nothing starts unless ``floor_gb`` stays available after its
  expected peak, and its cap fits in what is free: the agents themselves,
  browsers and databases live in that floor, and the cap is the worst case.
- BUDGET THE CAP, NOT THE ESTIMATE. A job may grow to its hard memory cap
  before the kernel stops it, so admission reserves the cap (``budget_gb``).
  Reserving only the estimate let several light jobs, each under its own
  cap, together eat the floor. Heavy or light is still judged on the
  estimate.
- NO ROOM IS KNOWN BESIDE AN UNCAPPED JOB. A running job with no memory
  limit (a legacy scope whose ``memory.max`` is ``max``) may grow into all
  the memory there is, so nothing starts until it ends.
- DISK IS A RESOURCE. Below ``disk_floor_gb`` free, nothing starts: a full
  disk fails every session's writes at once.
- PRIORITY, THEN FIFO; RESERVATIONS ARE KEPT. Critical-path work goes
  first; within a class, the earliest request. A slot promised to a named
  owner is not taken by anyone else's heavy job.
- LIGHT WORK BACKFILLS, BUT NEVER STARVES THE HEAD. A light job may start
  beside others when it fits. Once the first heavy waiter has waited
  ``starve_secs``, nothing behind it starts until it does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

#: Priority classes, best first.
PRIORITIES = ("critical", "normal", "low")


@dataclass(frozen=True)
class Policy:
    floor_gb: float = 8.0
    heavy_gb: float = 4.0
    disk_floor_gb: float = 30.0
    max_running: int = 4
    starve_secs: float = 1800.0
    #: Heavy jobs at once. Each runs hard-capped in its own scope and admission
    #: reserves every cap, so the memory budget, not a count of one, is what
    #: keeps them apart (Eric, 2026-11-20: persist's v62 push needs more than
    #: one heavy build at a time). 1 restores the old one-at-a-time rule.
    max_heavy: int = 2
    #: RECLAIM (Borg's reservation reclamation, on one box): a running job that
    #: has reached its peak and then stayed well under it gives back the
    #: headroom it no longer needs. It must have reached `reach_fraction` of
    #: its estimated peak (the build's link step, say), and then held at or
    #: under `settle_fraction` of that maximum for `settle_secs`. Its
    #: reservation drops to max(used x1.3, used + 1.5 GB). Its hard CAP does
    #: not change, so the job can still grow; the PSI gate below and the floor
    #: are what cover that (edge's 45.1.10 lib test held 9.2 GB at 2.5 GB used
    #: for 17 min after linking while four jobs waited, 2026-11-22).
    reach_fraction: float = 0.7
    settle_fraction: float = 0.5
    settle_secs: float = 120.0
    #: PSI GATE: nothing starts while the box's memory pressure (the share of
    #: the last 10 s some task stalled on memory, /proc/pressure/memory "some
    #: avg10", in %) is above this. A static floor guesses at pressure; PSI
    #: measures it (Meta's oomd/senpai). It only ever holds a start back.
    psi_max: float = 10.0


@dataclass(frozen=True)
class Request:
    id: str
    owner: str
    kind: str
    peak_gb: float
    priority: str = "normal"
    created: float = 0.0
    #: what admission reserves: the job's hard cap (default: the estimate)
    budget_gb: Optional[float] = None
    #: a SMALL job: its measured need stayed at or under SMALL_GB over its last
    #: runs (see `is_small`), so it is admitted against half the floor
    small: bool = False

    def heavy(self, p: Policy) -> bool:
        return self.peak_gb > p.heavy_gb

    def budget(self) -> float:
        return max(self.peak_gb, self.budget_gb or 0.0)


@dataclass(frozen=True)
class Running:
    id: str
    owner: str
    kind: str
    peak_gb: float
    used_gb: float = 0.0
    #: the job's hard cap, which it may grow into (default: the estimate)
    budget_gb: Optional[float] = None
    #: no memory limit at all: it may grow into everything available
    uncapped: bool = False
    #: the reservation this job keeps after RECLAIM (see Policy), or None
    reclaimed_gb: Optional[float] = None

    def effective_peak(self) -> float:
        return self.peak_gb if self.reclaimed_gb is None else min(self.peak_gb, self.reclaimed_gb)

    def heavy(self, p: Policy) -> bool:
        return self.effective_peak() > p.heavy_gb

    def headroom_gb(self) -> float:
        """What this job may still grow into before its reservation is used.
        Its cap, or after RECLAIM the smaller reservation it settled to."""
        if self.reclaimed_gb is not None:
            return max(0.0, self.reclaimed_gb - self.used_gb)
        return max(0.0, max(self.peak_gb, self.budget_gb or 0.0) - self.used_gb)


def reclaim(peak_gb: float, used_gb: float, max_used_gb: float, settled_since: Optional[float],
            now: float, policy: "Policy") -> Optional[float]:
    """The reservation a running job settles to after its peak, or None while
    it has not (see Policy.reach_fraction). Never above its estimated peak."""
    if settled_since is None or peak_gb <= 0:
        return None
    if max_used_gb < policy.reach_fraction * peak_gb:
        return None
    if used_gb > policy.settle_fraction * max_used_gb:
        return None
    if now - settled_since < policy.settle_secs:
        return None
    return round(min(peak_gb, max(used_gb * 1.3, used_gb + 1.5)), 2)


@dataclass(frozen=True)
class Machine:
    available_gb: float
    disk_free_gb: float
    #: /proc/pressure/memory "some avg10" (%), 0 where the kernel has no PSI
    psi_some_avg10: float = 0.0


@dataclass
class Decision:
    start: List[str] = field(default_factory=list)
    #: request id -> why it waits, in words a person can act on
    waits: Dict[str, str] = field(default_factory=dict)
    projected_gb: float = 0.0


def order(queue: List[Request]) -> List[Request]:
    rank = {p: i for i, p in enumerate(PRIORITIES)}
    return sorted(queue, key=lambda r: (rank.get(r.priority, len(PRIORITIES)), r.created, r.id))


def projected(machine: Machine, running: List[Running]) -> float:
    return machine.available_gb - sum(r.headroom_gb() for r in running)


def decide(
    queue: List[Request],
    running: List[Running],
    machine: Machine,
    policy: Policy = Policy(),
    reserved_for: Optional[str] = None,
    reserved_exclusive: bool = False,
    now: float = 0.0,
) -> Decision:
    d = Decision()
    room = projected(machine, running)
    d.projected_gb = round(room, 1)
    heavy_holders: List[str] = [f"{r.owner}:{r.kind}" for r in running if r.heavy(policy)]
    count = len(running)
    head_starving: Optional[Request] = None
    #: A critical heavy job that could not start this pass. Nothing heavy of a
    #: lower class starts until it does, so memory a normal job would take is
    #: there for it the moment running jobs free it (Eric, 2026-11-20: persist
    #: v62 is the critical path; an edge test lane started beside a waiting
    #: critical lane would hold its memory for ten minutes).
    critical_waiting: Optional[Request] = None

    if machine.psi_some_avg10 > policy.psi_max:
        for r in queue:
            d.waits[r.id] = (
                f"memory pressure: tasks stalled on memory {machine.psi_some_avg10:.0f}% of the last "
                f"10 s (over {policy.psi_max:.0f}%); waiting for it to ease"
            )
        return d

    if machine.disk_free_gb < policy.disk_floor_gb:
        for r in queue:
            d.waits[r.id] = (
                f"disk: {machine.disk_free_gb:.0f} GB free, under the {policy.disk_floor_gb:.0f} GB "
                "floor; free space (old target dirs) before anything starts"
            )
        return d

    unbounded = next((r for r in running if r.uncapped), None)
    if unbounded is not None:
        for r in queue:
            d.waits[r.id] = (
                f"{unbounded.owner}:{unbounded.kind} runs with no memory cap, so no free memory can be "
                "promised beside it; waiting until it ends"
            )
        return d

    for r in order(queue):
        if head_starving is not None:
            d.waits[r.id] = (
                f"behind {head_starving.owner}:{head_starving.kind}, which has waited "
                f"{(now - head_starving.created) / 60:.0f} min; nothing passes it now"
            )
            continue
        if count >= policy.max_running:
            d.waits[r.id] = f"{count} jobs running, the most this box runs at once"
            continue
        heavy = r.heavy(policy)
        # An EXCLUSIVE reservation (a release certify): nothing of another owner
        # starts, light work included; running jobs drain and the owner's own
        # jobs are admitted by the ordinary memory rules.
        if reserved_exclusive and reserved_for and r.owner != reserved_for:
            d.waits[r.id] = f"the box is reserved for {reserved_for} (exclusive): waiting for it to release"
            continue
        if heavy and critical_waiting is not None and r.priority != "critical":
            d.waits[r.id] = (
                f"behind the critical {critical_waiting.owner}:{critical_waiting.kind}: no other "
                "heavy job starts until it does"
            )
            continue
        if heavy and reserved_for and r.owner != reserved_for:
            d.waits[r.id] = f"the heavy slot is promised to {reserved_for}"
            continue
        if heavy and len(heavy_holders) >= policy.max_heavy:
            if policy.max_heavy == 1:
                d.waits[r.id] = f"one heavy job at a time: {heavy_holders[0]} holds it"
            else:
                d.waits[r.id] = (f"{policy.max_heavy} heavy jobs at a time: "
                                 f"{', '.join(heavy_holders)} hold the slots")
            if now - r.created >= policy.starve_secs:
                head_starving = r
            if r.priority == "critical":
                critical_waiting = critical_waiting or r
            continue
        # The cap must fit in what is projected free (the worst case: the job
        # grows to its cap), and the EXPECTED peak must leave the floor. The
        # floor on top of the cap asked a 14 GB job for 22 GB free on a 31 GB
        # box, and an empty box admitted nothing (2026-11-19).
        need = max(r.budget(), r.peak_gb + policy.floor_gb)
        # An IDLE box: no job runs and none has started this pass. The lone
        # job is hard-capped, so the floor need only cover the sessions beside
        # it, not a second job's growth: its cap plus HALF the floor, never
        # more than the ordinary rule. On an empty box, the full floor on top
        # of a margin-inflated peak held persist's lib nextest forever (14.4 +
        # 8 = 22.4 GB against 22.0 free, nothing running, 2026-11-20).
        # A LIGHT job beside it (at or under heavy_gb) does not end "idle": its
        # memory is already reserved in `room`, and it cannot grow past its
        # cap. Only another heavy job does. A 2 GB localize task held persist's
        # critical clippy (13.2 + 8 = 21.2 GB against 21.0 free) while it used
        # 0.02 GB (2026-11-21).
        # A SMALL job (measured need at or under SMALL_GB on each of its last
        # LEARN_BELOW_RUNS runs): its cap must still fit, but its expected peak
        # leaves only HALF the floor, even beside a heavy job. Four short jobs
        # (persist's sqlite lane needing 0.1-0.24 GB, commit hooks) waited
        # 7-17 min behind one edge test that had reserved 9.2 GB and was using
        # 1.9 (2026-11-22). The full floor stays for everything else.
        if r.small:
            need = min(need, max(r.budget(), r.peak_gb + policy.floor_gb / 2))
        if not any(j.heavy(policy) for j in running) and not heavy_holders:
            # The cap plus a quarter of the floor (the worst case, the job at its
            # cap), and the EXPECTED peak plus half the floor (the sessions beside
            # it). Caps sit close to the measured peak (runner.default_cap), so
            # the two agree within a couple of GB; cap + floor/2 alone held a
            # measured 10 GB build off a 15.5 GB idle box (2026-11-21).
            need = min(need, max(r.budget() + policy.floor_gb / 4, r.peak_gb + policy.floor_gb / 2))
        if room < need:
            d.waits[r.id] = (
                f"memory: needs {r.budget():.1f} GB for its cap and {r.peak_gb:.1f} GB plus the "
                f"{policy.floor_gb:.0f} GB floor for its expected peak, "
                f"{room:.1f} GB projected free once running jobs reach their peaks"
            )
            if heavy and now - r.created >= policy.starve_secs:
                head_starving = r
            if heavy and r.priority == "critical":
                critical_waiting = critical_waiting or r
            continue
        d.start.append(r.id)
        room -= r.budget()
        count += 1
        if heavy:
            heavy_holders.append(f"{r.owner}:{r.kind}")
    return d


#: The peak a job with no declaration and no history is admitted at.
UNKNOWN_PEAK_GB = 6.0

#: Measured runs before a kind's own history may set its estimate BELOW what
#: its caller declared (and before it may count as a small job).
LEARN_BELOW_RUNS = 3

#: The least an estimate learned below a declaration goes to.
LEARNED_MIN_GB = 0.5

#: A job whose measured need stayed at or under this on each of its last
#: LEARN_BELOW_RUNS runs is a small job (`Request.small`).
SMALL_GB = 1.0


def is_small(need_history_gb: List[float]) -> bool:
    """Whether a kind's measured NEED (anonymous memory, not page cache) stayed
    small on each of its last runs: at least LEARN_BELOW_RUNS of them, every
    one of the last five at or under SMALL_GB."""
    recent = need_history_gb[-5:]
    return len(need_history_gb) >= LEARN_BELOW_RUNS and max(recent) <= SMALL_GB


def estimate_peak(
    declared_gb: Optional[float],
    history_gb: List[float],
    default_gb: float = UNKNOWN_PEAK_GB,
    margin: float = 1.15,
) -> Tuple[float, str]:
    """The peak to admit on, and where it came from.

    The larger of what the caller declared and what this kind of job really
    reached the last few times (the cgroup's own ``memory.peak``, which sees
    the spike a one-second poll misses), with a margin. A first-time job with
    no declaration takes ``default_gb``.
    """
    seen = max(history_gb[-5:]) * margin if history_gb else None
    if declared_gb is not None and seen is not None:
        if seen > declared_gb:
            return round(seen, 1), f"learned ({len(history_gb)} runs, above the declared {declared_gb:g})"
        # Learned BELOW the declaration, once the kind has enough measured
        # runs: a declaration is a caller's guess and the runs are the box's
        # measurement. persist's sqlite lane was declared 3 GB and measured
        # 0.1-0.24 GB on every run, and was admitted at 3 GB + the floor each
        # time (2026-11-22). The CAP keeps the declaration (runner.plan), so a
        # run that does grow is not killed early; only admission learns.
        if len(history_gb) >= LEARN_BELOW_RUNS:
            low = max(round(seen, 1), LEARNED_MIN_GB)
            if low < declared_gb:
                return low, f"learned ({len(history_gb)} runs, below the declared {declared_gb:g})"
        return float(declared_gb), "declared"
    if declared_gb is not None:
        return float(declared_gb), "declared"
    if seen is not None:
        return round(seen, 1), f"learned ({len(history_gb)} runs)"
    return default_gb, "default (no declaration, no history)"
