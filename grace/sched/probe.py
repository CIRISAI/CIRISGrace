"""What the box looks like right now: memory, disk, processes, cgroups.

Linux ``/proc`` and cgroup v2 reads only; stdlib only.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

GIB = 1024 ** 3


def available_gb() -> float:
    """MemAvailable, the number `free` reports as "available"."""
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024 / GIB
    return 0.0


def disk_free_gb(path: str = "/") -> float:
    return shutil.disk_usage(path).free / GIB


def start_time(pid: int) -> Optional[int]:
    """The process's start time in clock ticks since boot, or None when the
    pid is gone. A lease names (pid, start_time), so a recycled pid is never
    mistaken for the holder that died."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # Field 22, counted after the closing paren of the command name.
    rest = stat[stat.rfind(")") + 2:].split()
    try:
        return int(rest[19])
    except (IndexError, ValueError):
        return None


def alive(pid: int, started: Optional[int]) -> bool:
    now = start_time(pid)
    return now is not None and (started is None or now == started)


def cgroup_of(pid: int) -> Optional[str]:
    try:
        line = Path(f"/proc/{pid}/cgroup").read_text().strip().splitlines()[-1]
    except (OSError, IndexError):
        return None
    return line.split(":", 2)[-1]


def cgroup_path(rel: str) -> Path:
    return Path("/sys/fs/cgroup") / rel.lstrip("/")


def cgroup_gb(rel: str, file: str = "memory.current") -> Optional[float]:
    try:
        return int((cgroup_path(rel) / file).read_text().split()[0]) / GIB
    except (OSError, ValueError, IndexError):
        return None


def cgroup_anon_gb(rel: str) -> Optional[float]:
    """Anonymous memory: the part that OOMs. ``memory.current`` also counts
    page cache the kernel can reclaim."""
    try:
        for line in (cgroup_path(rel) / "memory.stat").read_text().splitlines():
            if line.startswith("anon "):
                return int(line.split()[1]) / GIB
    except OSError:
        return None
    return None


def rss_gb_of_group(pgid: int) -> float:
    """Sum of RSS over a process group (the fallback when there is no
    cgroup to read)."""
    total = 0
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            if os.getpgid(int(p.name)) != pgid:
                continue
            for line in (p / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1]) * 1024
        except (OSError, ValueError, ProcessLookupError):
            continue
    return total / GIB


_BUILD = re.compile(r"(^|/)(rustc|cargo|cargo-nextest|cargo-clippy|clippy-driver|rustdoc|maturin|sccache|ld\.lld|mold)$")


def builds() -> List[Dict[str, object]]:
    """Every compiler-shaped process on the box, with its cgroup and RSS, so
    `doctor` can name the ones running outside any lease."""
    out = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            argv = (p / "cmdline").read_bytes().split(b"\0")
            exe = argv[0].decode(errors="replace")
            if not _BUILD.search(exe):
                continue
            rss = 0
            for line in (p / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
            out.append({
                "pid": int(p.name),
                "exe": os.path.basename(exe),
                "cmd": " ".join(a.decode(errors="replace") for a in argv[:6])[:160],
                "cgroup": cgroup_of(int(p.name)) or "",
                "rss_gb": round(rss / GIB, 2),
            })
        except OSError:
            continue
    return out


def fd_holders(prefix: str) -> List[Dict[str, object]]:
    """Processes holding an open file under ``prefix``. The legacy guard's
    slot was an flock on such a file, and a child that inherited the
    descriptor held the slot for as long as it lived (2026-11-19: a memory
    sampler held slot 0 for an hour)."""
    out = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            for fd in (p / "fd").iterdir():
                target = os.readlink(fd)
                if target.startswith(prefix):
                    cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                    out.append({"pid": int(p.name), "file": target, "fd": int(fd.name), "cmd": cmd[:160],
                                "cgroup": cgroup_of(int(p.name)) or ""})
        except OSError:
            continue
    return out


def systemd_scopes_usable() -> bool:
    if shutil.which("systemd-run") is None or os.environ.get("GRACE_SCHED_NO_SYSTEMD") == "1":
        return False
    import subprocess
    try:
        r = subprocess.run(
            ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=1M", "true"],
            capture_output=True, timeout=10, close_fds=True,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def guard_of(cg: str) -> str:
    """Which guard a process runs under, from its cgroup: `grace-sched`
    (this scheduler), `cargo-guard` (the legacy shell guard) or `none`."""
    if "grace-sched-" in cg:
        return "grace-sched"
    if "cargo-guard-" in cg:
        return "cargo-guard"
    return "none"


def legacy_guard_scopes() -> List[Dict[str, object]]:
    """The legacy cargo_guard.sh's live scopes, each with its MemoryMax and
    current use, so the projection counts builds that never asked this
    scheduler (the migration period). A cap of "max" counts as 14 GB."""
    base = Path(f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice")
    out = []
    for d in sorted(base.glob("cargo-guard-*.scope")):
        try:
            raw = (d / "memory.max").read_text().strip()
            cap = 14.0 if raw == "max" else int(raw) / GIB
            used = int((d / "memory.current").read_text().split()[0]) / GIB
        except (OSError, ValueError):
            continue
        out.append({"unit": d.name, "cap_gb": round(cap, 1), "used_gb": round(used, 2)})
    return out


def cgroup_populated(rel: str) -> bool:
    """Whether any process still lives in the cgroup (``cgroup.events``)."""
    try:
        for line in (cgroup_path(rel) / "cgroup.events").read_text().splitlines():
            if line.startswith("populated "):
                return line.split()[1] == "1"
    except OSError:
        return False
    return False


def group_alive(pgid: int) -> bool:
    """Whether any process is still in the process group."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def oom_kills(rel: str) -> int:
    """The scope's own OOM kill count (``memory.events``): nonzero only when
    the kernel killed inside this cgroup, at its own ``MemoryMax``."""
    n = 0
    try:
        for line in (cgroup_path(rel) / "memory.events").read_text().splitlines():
            k, _, v = line.partition(" ")
            if k in ("oom_kill", "oom_group_kill"):
                n += int(v)
    except (OSError, ValueError):
        return 0
    return n


def unit_result(unit: str) -> str:
    """systemd's verdict on a finished unit of ours: ``oom-kill`` when the
    kernel killed it at its cap. A failed scope stays loaded until reset."""
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--user", "show", unit, "-p", "Result", "--value"],
                           capture_output=True, text=True, timeout=10, close_fds=True)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def reset_failed(unit: str) -> None:
    """Unload our OWN finished unit, so failed scopes do not pile up."""
    import subprocess
    try:
        subprocess.run(["systemctl", "--user", "reset-failed", unit], capture_output=True,
                       timeout=10, close_fds=True)
    except (OSError, subprocess.SubprocessError):
        pass
