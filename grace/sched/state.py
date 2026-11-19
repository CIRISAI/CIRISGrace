"""The shared state: requests, leases, reservations, history, events.

One directory, shared by every session of this user on the box
(``$GRACE_SCHED_DIR``, default ``/tmp/grace-sched-<uid>``). Plain JSON files,
so ``cat`` is a debugger.

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
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from . import probe


def root() -> Path:
    d = Path(os.environ.get("GRACE_SCHED_DIR", f"/tmp/grace-sched-{os.getuid()}"))
    for sub in ("queue", "leases"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
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


def reap(d: Path) -> List[Dict[str, Any]]:
    """Drop every request and lease whose holder process is gone. Answers
    what was reclaimed, each also written to the event log."""
    gone = []
    for sub in ("queue", "leases"):
        for v in entries(d, sub):
            if not probe.alive(int(v.get("pid", 0)), v.get("pid_start")):
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


def history(d: Path, kind: str) -> List[float]:
    out = []
    try:
        for line in (d / "history.jsonl").read_text().splitlines():
            rec = json.loads(line)
            if rec.get("kind") == kind and rec.get("peak_gb"):
                out.append(float(rec["peak_gb"]))
    except (OSError, ValueError):
        pass
    return out


def record(d: Path, **fields: Any) -> None:
    with open(d / "history.jsonl", "a") as f:
        f.write(json.dumps(fields, sort_keys=True) + "\n")
