"""Admission: who may start now, and in plain words why everyone else waits.

Pure functions over plain data, so every rule is a unit test and none needs
a machine. The rules are the shared box's lessons (2026-10 .. 2026-11), each
named where it is enforced:

- ONE HEAVY JOB AT A TIME. A heavy job is one whose expected peak is above
  ``heavy_gb``. Two at once is how the kernel's OOM killer and systemd-oomd
  took whole terminal scopes, every agent in them.
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

    def heavy(self, p: Policy) -> bool:
        return self.peak_gb > p.heavy_gb

    def headroom_gb(self) -> float:
        """What this job may still grow into before its cap stops it."""
        return max(0.0, max(self.peak_gb, self.budget_gb or 0.0) - self.used_gb)


@dataclass(frozen=True)
class Machine:
    available_gb: float
    disk_free_gb: float


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
    now: float = 0.0,
) -> Decision:
    d = Decision()
    room = projected(machine, running)
    d.projected_gb = round(room, 1)
    heavy_holder: Optional[Running] = next((r for r in running if r.heavy(policy)), None)
    started_heavy: Optional[Request] = None
    count = len(running)
    head_starving: Optional[Request] = None

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
        if heavy and reserved_for and r.owner != reserved_for:
            d.waits[r.id] = f"the heavy slot is promised to {reserved_for}"
            continue
        if heavy and (heavy_holder is not None or started_heavy is not None):
            who = heavy_holder or started_heavy
            d.waits[r.id] = f"one heavy job at a time: {who.owner}:{who.kind} holds it"
            if now - r.created >= policy.starve_secs:
                head_starving = r
            continue
        # The cap must fit in what is projected free (the worst case: the job
        # grows to its cap), and the EXPECTED peak must leave the floor. The
        # floor on top of the cap asked a 14 GB job for 22 GB free on a 31 GB
        # box, and an empty box admitted nothing (2026-11-19).
        need = max(r.budget(), r.peak_gb + policy.floor_gb)
        if room < need:
            d.waits[r.id] = (
                f"memory: needs {r.budget():.1f} GB for its cap and {r.peak_gb:.1f} GB plus the "
                f"{policy.floor_gb:.0f} GB floor for its expected peak, "
                f"{room:.1f} GB projected free once running jobs reach their peaks"
            )
            if heavy and now - r.created >= policy.starve_secs:
                head_starving = r
            continue
        d.start.append(r.id)
        room -= r.budget()
        count += 1
        if heavy:
            started_heavy = r
    return d


#: The peak a job with no declaration and no history is admitted at.
UNKNOWN_PEAK_GB = 6.0


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
        return float(declared_gb), "declared"
    if declared_gb is not None:
        return float(declared_gb), "declared"
    if seen is not None:
        return round(seen, 1), f"learned ({len(history_gb)} runs)"
    return default_gb, "default (no declaration, no history)"
