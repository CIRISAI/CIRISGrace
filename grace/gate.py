"""Gate framework.

A gate answers one readiness question with one of four statuses:

    pass           the question was asked and the answer is yes
    fail           the question was asked and the answer is no
    unimplemented  the gate exists but does not check anything yet
    error          the gate could not run (network, missing tool, bad input)

`unimplemented` is deliberately NOT a pass. A gate that hides an unasked
question behind a green tick is worse than no gate, so `passed_all_gates`
is true only when every gate returned `pass`. This mirrors the rule in
CIRISConformance: never skip to paper over a gap; declare it.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

PASS = "pass"
FAIL = "fail"
UNIMPLEMENTED = "unimplemented"
ERROR = "error"

SCHEMA = "ciris-readiness/v1"


@dataclass
class Result:
    gate: str
    status: str
    summary: str
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == PASS


@dataclass
class Gate:
    id: str
    question: str
    fn: Callable[["Context"], Result]


_REGISTRY: dict[str, Gate] = {}


def gate(gate_id: str, question: str) -> Callable:
    """Register a gate. `question` is what the gate asks, in plain words."""

    def wrap(fn: Callable[["Context"], Result]) -> Callable:
        _REGISTRY[gate_id] = Gate(id=gate_id, question=question, fn=fn)
        return fn

    return wrap


def registry() -> dict[str, Gate]:
    return dict(_REGISTRY)


@dataclass
class Context:
    """Where to look. Everything is a path or a URL; nothing is implicit."""

    root: Path
    node_url: str | None = None
    offline: bool = False

    def repo(self, name: str) -> Path:
        return self.root / name

    def has_repo(self, name: str) -> bool:
        return (self.root / name / ".git").exists()


# ── helpers shared by gates ──────────────────────────────────────────────


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    """Run a command, return (rc, combined output). Never raises."""
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: timed out after {timeout}s"


def have(tool: str) -> bool:
    rc, _ = run([tool, "--version"], timeout=20)
    return rc == 0


def pypi(package: str, timeout: int = 20) -> dict:
    """Fetch a package's PyPI JSON. Raises urllib errors on failure."""
    url = f"https://pypi.org/pypi/{package}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "ciris-grace"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def parse_version(v: str) -> tuple:
    """Loose PEP 440-ish sort key. Good enough for x.y.z comparisons."""
    out = []
    for part in v.split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    return tuple(out)


# ── runner + report ──────────────────────────────────────────────────────


def run_gates(ctx: Context, only: Iterable[str] | None = None) -> list[Result]:
    selected = registry()
    if only:
        want = set(only)
        unknown = want - set(selected)
        if unknown:
            raise SystemExit(f"unknown gate(s): {', '.join(sorted(unknown))}")
        selected = {k: v for k, v in selected.items() if k in want}

    results: list[Result] = []
    for gid in sorted(selected):
        g = selected[gid]
        started = time.time()
        try:
            res = g.fn(ctx)
        except Exception as exc:  # a gate crashing is an error, not a pass
            res = Result(gid, ERROR, f"{type(exc).__name__}: {exc}")
        res.detail.setdefault("question", g.question)
        res.detail.setdefault("seconds", round(time.time() - started, 2))
        results.append(res)
    return results


def report(results: list[Result], repo: str) -> dict:
    return {
        "schema": SCHEMA,
        "repo": repo,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gates": [asdict(r) for r in results],
        "totals": {
            s: sum(1 for r in results if r.status == s)
            for s in (PASS, FAIL, UNIMPLEMENTED, ERROR)
        },
        "passed_all_gates": all(r.ok for r in results),
    }


_MARK = {PASS: "PASS", FAIL: "FAIL", UNIMPLEMENTED: "TODO", ERROR: "ERR "}


def render(results: list[Result]) -> str:
    width = max((len(r.gate) for r in results), default=0)
    lines = [f"  {_MARK[r.status]}  {r.gate.ljust(width)}  {r.summary}" for r in results]
    n_pass = sum(1 for r in results if r.ok)
    lines.append("")
    lines.append(
        f"  {n_pass}/{len(results)} pass"
        f"  ·  passed_all_gates={str(all(r.ok for r in results)).lower()}"
    )
    return "\n".join(lines)
