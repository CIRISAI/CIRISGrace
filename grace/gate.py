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

# Two questions, two tiers, deliberately not averaged together.
#
#   substrate  "can I cut from the coherent set today?"
#   ecosystem  "what is rotting across the estate while I am not looking?"
#
# Folding ~48 repositories into the one boolean that answers the first question
# makes that boolean permanently false, and a boolean that is always false is
# one nobody reads. So each tier rolls up on its own and the top-level
# `passed_all_gates` remains the strict conjunction for a machine that only
# wants one field.
SUBSTRATE = "substrate"
ECOSYSTEM = "ecosystem"
TIERS = (SUBSTRATE, ECOSYSTEM)


@dataclass
class Result:
    gate: str
    status: str
    summary: str
    detail: dict = field(default_factory=dict)
    # Set by the runner from the registry, so a gate cannot disagree with its
    # own registration about which question it is answering.
    tier: str = SUBSTRATE

    @property
    def ok(self) -> bool:
        return self.status == PASS


@dataclass
class Gate:
    id: str
    question: str
    fn: Callable[["Context"], Result]
    tier: str = SUBSTRATE


_REGISTRY: dict[str, Gate] = {}


def gate(gate_id: str, question: str, tier: str = SUBSTRATE) -> Callable:
    """Register a gate. `question` is what the gate asks, in plain words."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r} for gate {gate_id!r}")

    def wrap(fn: Callable[["Context"], Result]) -> Callable:
        _REGISTRY[gate_id] = Gate(id=gate_id, question=question, fn=fn, tier=tier)
        return fn

    return wrap


def registry() -> dict[str, Gate]:
    return dict(_REGISTRY)


@dataclass
class Context:
    """Where to look. Everything is a path or a URL; nothing is implicit.

    `root` is where sibling checkouts live if there are any. There need not be
    any: every gate must be able to answer from the network alone, because the
    daily driver runs on a CI runner with nothing checked out but this repo.
    A local checkout is an optimisation, never a requirement.
    """

    root: Path
    node_url: str | None = None
    offline: bool = False
    # Per-run memo. Materialising a sibling's origin/main costs a clone and a
    # subprocess; four gates ask for the same tree, and without this they each
    # pay for it. Keyed by an opaque string, owned by the gates.
    cache: dict = field(default_factory=dict, repr=False, compare=False)

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
        res.tier = g.tier
        res.detail.setdefault("question", g.question)
        res.detail.setdefault("seconds", round(time.time() - started, 2))
        results.append(res)
    return results


def _totals(results: list[Result]) -> dict:
    return {
        s: sum(1 for r in results if r.status == s)
        for s in (PASS, FAIL, UNIMPLEMENTED, ERROR)
    }


def report(results: list[Result], repo: str) -> dict:
    """The `ciris-readiness/v1` envelope.

    `passed_all_gates` is the strict conjunction over every tier, for a
    consumer that wants one field. `tiers[t].passed_all_gates` is the same
    question asked of one tier — which is what someone actually means by "can I
    cut today", since estate rot in a dormant repo is a true finding that has
    no bearing on whether the coherent set is cuttable.
    """
    by_tier = {t: [r for r in results if r.tier == t] for t in TIERS}
    return {
        "schema": SCHEMA,
        "repo": repo,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gates": [asdict(r) for r in results],
        "totals": _totals(results),
        "tiers": {
            t: {"totals": _totals(rs), "passed_all_gates": all(r.ok for r in rs)}
            for t, rs in by_tier.items()
            if rs
        },
        "passed_all_gates": all(r.ok for r in results),
    }


_MARK = {PASS: "PASS", FAIL: "FAIL", UNIMPLEMENTED: "TODO", ERROR: "ERR "}

_EMOJI = {PASS: "✅", FAIL: "❌", UNIMPLEMENTED: "🚧", ERROR: "⚠️"}


def markdown(rep: dict) -> str:
    """The board as GitHub-flavoured markdown, for a step summary.

    This is the surface someone reads on a phone, so status is an emoji in the
    first column rather than a word in the middle of a sentence: the answer has
    to survive being skimmed. Content is identical to `render` — the terminal
    and the summary must never be able to disagree.
    """
    t = rep["totals"]
    lines = [
        f"## {rep['repo']} — readiness",
        "",
        f"**{t[PASS]}/{sum(t.values())} pass** · `passed_all_gates="
        f"{str(rep['passed_all_gates']).lower()}` · {rep['generated']}",
    ]

    for alert in rep.get("alerts", []):
        lines += ["", f"> {_EMOJI[alert['severity']]} **{alert['title']}**  ",
                  f"> {alert['detail']}  ",
                  f"> _{alert['action']}_"]

    for tier, block in rep.get("tiers", {}).items():
        tt = block["totals"]
        lines += [
            "",
            f"### {tier} — {tt[PASS]}/{sum(tt.values())} pass",
            "",
            "| | gate | answer | since |",
            "|---|---|---|---|",
        ]
        for g in rep["gates"]:
            if g.get("tier") != tier:
                continue
            summary = g["summary"].replace("|", "\\|")
            lines.append(
                f"| {_EMOJI[g['status']]} | `{g['gate']}` | {summary} "
                f"| {_delta_cell(rep, g['gate'])} |"
            )

    if t[UNIMPLEMENTED]:
        lines += [
            "",
            f"{t[UNIMPLEMENTED]} gate(s) are 🚧 **unimplemented** — a declared gap, "
            "not a pass. See `evidence/blocked_upstream.tsv`.",
        ]
    return "\n".join(lines) + "\n"


def _delta_cell(rep: dict, gate_id: str) -> str:
    """What this gate did since the last run, or `·` if it did nothing."""
    for d in rep.get("deltas", {}).get("gates", []):
        if d["gate"] == gate_id:
            return f"{_EMOJI.get(d['from'], '')}→{_EMOJI.get(d['to'], '')}"
    for m in rep.get("deltas", {}).get("metrics", []):
        if m["gate"] == gate_id:
            sign = "+" if m["change"] > 0 else ""
            return f"`{m['metric']} {sign}{m['change']}`"
    return "·"


def render(results: list[Result], rep: dict | None = None) -> str:
    width = max((len(r.gate) for r in results), default=0)
    lines: list[str] = []
    for tier in TIERS:
        rs = [r for r in results if r.tier == tier]
        if not rs:
            continue
        lines.append(f"  ── {tier} " + "─" * max(0, 58 - len(tier)))
        lines += [f"  {_MARK[r.status]}  {r.gate.ljust(width)}  {r.summary}" for r in rs]
        lines.append("")
    n_pass = sum(1 for r in results if r.ok)
    lines.append(
        f"  {n_pass}/{len(results)} pass"
        f"  ·  passed_all_gates={str(all(r.ok for r in results)).lower()}"
    )
    for alert in (rep or {}).get("alerts", []):
        lines.append(f"  {_MARK[alert['severity']]}  {alert['title']} — {alert['action']}")
    return "\n".join(lines)
