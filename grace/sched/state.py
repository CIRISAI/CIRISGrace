"""The shared state: requests, leases, reservations, history, events.

One directory, shared by every session of this user on the box
(``$GRACE_SCHED_DIR``; default ``/run/user/<uid>/grace-sched`` when that
private runtime directory exists, else ``/tmp/grace-sched-<uid>``). The
default is chosen from the filesystem, never from a session's environment,
so every session finds the same directory. Plain JSON files, so ``cat`` is a
debugger.

**The directory must be ours.** Whoever can write it can drop live leases and
make the scheduler admit overlapping jobs. Another account could pre-create
``/tmp/grace-sched-<uid>``, so the directory is refused unless it is a real
directory (not a symlink), owned by this user, and closed to everyone else.

**A slot is a lease, not a file descriptor.** The legacy guard held a slot by
keeping an ``flock`` open for the build's lifetime. Any child that inherited
the descriptor held the slot as long as it lived, and nothing could tell a
leak from a build. Here the mutex is held only for the few milliseconds it
takes to read and write these files. A lease names its holder as (pid, start
time), and a lease whose holder is gone is reclaimed by the next reader. The
mutex file is opened close-on-exec (Python's default, PEP 446), and every child
is started with ``close_fds=True``.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from . import probe


class UnsafeStateDir(Exception):
    """The state directory is not private to this user."""


def default_dir() -> Path:
    uid = os.getuid()
    run = Path(f"/run/user/{uid}")
    try:
        st = os.lstat(run)
        if stat.S_ISDIR(st.st_mode) and st.st_uid == uid and not st.st_mode & 0o022:
            return run / "grace-sched"
    except OSError:
        pass
    return Path(f"/tmp/grace-sched-{uid}")


def check_private(d: Path) -> None:
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode):
        raise UnsafeStateDir(f"{d} is not a directory (a symlink?); refusing to keep scheduler state there")
    if st.st_uid != os.getuid():
        raise UnsafeStateDir(f"{d} belongs to uid {st.st_uid}, not {os.getuid()}; refusing it")
    if st.st_mode & 0o077:
        try:
            os.chmod(d, 0o700)
        except OSError as e:
            raise UnsafeStateDir(f"{d} is open to other users and cannot be closed: {e}") from e
        if os.lstat(d).st_mode & 0o077:
            raise UnsafeStateDir(f"{d} is open to other users; refusing it")


def root() -> Path:
    env = os.environ.get("GRACE_SCHED_DIR")
    d = Path(env) if env else default_dir()
    try:
        d.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        pass
    check_private(d)
    for sub in ("queue", "leases"):
        (d / sub).mkdir(mode=0o700, exist_ok=True)
    return d


@contextlib.contextmanager
def locked() -> Iterator[Path]:
    d = root()
    fd = os.open(d / ".mutex", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield d
    finally:
        os.close(fd)


def new_id() -> str:
    return time.strftime("%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _read(p: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _write(p: Path, v: Dict[str, Any]) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(v, sort_keys=True))
    tmp.replace(p)


def entries(d: Path, sub: str) -> List[Dict[str, Any]]:
    out = []
    for p in sorted((d / sub).glob("*.json")):
        v = _read(p)
        if v is not None:
            out.append(v)
    return out


def put(d: Path, sub: str, v: Dict[str, Any]) -> None:
    _write(d / sub / f"{v['id']}.json", v)


def drop(d: Path, sub: str, job_id: str) -> None:
    with contextlib.suppress(OSError):
        (d / sub / f"{job_id}.json").unlink()


def event(d: Path, name: str, **fields: Any) -> None:
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": name, **fields}
    with open(d / "events.jsonl", "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def job_alive(v: Dict[str, Any]) -> bool:
    """Whether a lease's JOB still runs, whatever became of its supervisor.
    The job runs in its own scope and session, so it can outlive a
    supervisor that was killed."""
    if v.get("cgroup"):
        return probe.cgroup_populated(v["cgroup"])
    if v.get("pgid"):
        return probe.group_alive(int(v["pgid"]))
    return False


def reap(d: Path) -> List[Dict[str, Any]]:
    """Drop every request and lease whose holder process is gone. Answers
    what was reclaimed, each also written to the event log.

    A lease whose supervisor died while its job still runs is KEPT (marked
    orphaned) until the job's scope empties: reclaiming it would hide a live
    build from admission while it still uses memory."""
    gone = []
    for sub in ("queue", "leases"):
        for v in entries(d, sub):
            if not probe.alive(int(v.get("pid", 0)), v.get("pid_start")):
                if sub == "leases" and job_alive(v):
                    if not v.get("orphaned"):
                        v["orphaned"] = True
                        put(d, sub, v)
                        event(d, "orphaned", id=v["id"], owner=v.get("owner"), kind=v.get("kind"),
                              why="its supervisor is gone but the job still runs; kept until it exits")
                    continue
                drop(d, sub, v["id"])
                gone.append({**v, "from": sub})
                event(d, "reclaimed", id=v["id"], owner=v.get("owner"), kind=v.get("kind"), was=sub)
    return gone


def reservation(d: Path) -> Optional[Dict[str, Any]]:
    v = _read(d / "reservation.json")
    if v and v.get("until", 0) < time.time():
        with contextlib.suppress(OSError):
            (d / "reservation.json").unlink()
        return None
    return v


def reserve(d: Path, owner: str, minutes: float, by: str) -> Dict[str, Any]:
    v = {"owner": owner, "until": time.time() + minutes * 60, "by": by}
    _write(d / "reservation.json", v)
    event(d, "reserved", **v)
    return v


def unreserve(d: Path) -> None:
    with contextlib.suppress(OSError):
        (d / "reservation.json").unlink()
    event(d, "unreserved")


def history(d: Path, kind: str, need_only: bool = False) -> List[float]:
    """This kind's measured peaks, oldest first. A malformed record (an append
    cut short) is skipped on its own: stopping at it would hide every later
    measurement from admission for good."""
    try:
        lines = (d / "history.jsonl").read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
            if not (isinstance(rec, dict) and rec.get("kind") == kind):
                continue
            # A run's NEED (its anonymous memory) when it was measured; older
            # records carry only memory.peak, which also counts page cache.
            if rec.get("need_gb") is not None:
                out.append(float(rec["need_gb"]))
            elif not need_only and rec.get("peak_gb"):
                out.append(float(rec["peak_gb"]))
        except (ValueError, TypeError):
            continue
    return out


def record(d: Path, **fields: Any) -> None:
    with open(d / "history.jsonl", "a") as f:
        f.write(json.dumps(fields, sort_keys=True) + "\n")
