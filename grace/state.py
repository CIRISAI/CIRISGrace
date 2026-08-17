"""What changed since last time, and what you should do about it.

A board is a snapshot, and a snapshot cannot distinguish "red since April" from
"red since this morning" — which is the only distinction that decides whether
you act now. So each run is compared against the last, and the comparison is
published alongside the board.

State lives in **this repo** and nowhere else. Grace reports; it never writes to
a sibling. `state/latest.json` is the last full report, `state/history.jsonl` is
one compact line per run — enough to answer "how long has this been red" without
retaining every report forever.

Two rules keep the alerts worth reading:

- **An alert is a change, not a condition.** Re-announcing a known failure every
  morning is how a channel becomes noise, and a noisy channel is one nobody
  reads on the morning something new breaks.
- **Except when a change has stopped happening.** A gate red for several runs in
  a row is escalated exactly once, because "nobody has picked this up" is itself
  a new fact.
"""

from __future__ import annotations

import json
from pathlib import Path

from .gate import ERROR, FAIL, PASS, UNIMPLEMENTED

# How many consecutive non-passing runs before "still broken" becomes news
# again. Three days of a red chain is a decision someone made by not deciding.
NAG_AFTER = 3

# Trend lines worth a sentence when they move. Everything a gate puts in
# `detail["metrics"]` is diffed; these get prose.
_METRIC_PROSE = {
    "open_issues": "open issues across the estate",
    "open_prs": "open pull requests",
    "stale_backlogs": "repos with an untouched backlog",
    "links_red": "broken links in the build chain",
    "outstanding": "outstanding upstream obligations",
}


def load(state_dir: Path) -> tuple[dict | None, list[dict]]:
    """→ (previous report or None, history newest-last)."""
    latest = state_dir / "latest.json"
    prev = json.loads(latest.read_text()) if latest.exists() else None
    hist_path = state_dir / "history.jsonl"
    history = []
    if hist_path.exists():
        for line in hist_path.read_text().splitlines():
            if line.strip():
                try:
                    history.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a truncated line is not a reason to lose the rest
    return prev, history


def _metrics(rep: dict) -> dict[str, dict]:
    return {
        g["gate"]: g.get("detail", {}).get("metrics", {})
        for g in rep.get("gates", [])
        if g.get("detail", {}).get("metrics")
    }


def deltas(prev: dict | None, cur: dict) -> dict:
    """What moved between two reports."""
    if not prev:
        return {"since": None, "baseline": True, "gates": [], "metrics": [],
                "appeared": [g["gate"] for g in cur.get("gates", [])], "disappeared": []}

    was = {g["gate"]: g["status"] for g in prev.get("gates", [])}
    now = {g["gate"]: g["status"] for g in cur.get("gates", [])}

    moved = [
        {"gate": gid, "from": was[gid], "to": now[gid]}
        for gid in sorted(now)
        if gid in was and was[gid] != now[gid]
    ]

    pm, cm = _metrics(prev), _metrics(cur)
    metric_moves = []
    for gid in sorted(cm):
        for key, val in sorted(cm[gid].items()):
            old = pm.get(gid, {}).get(key)
            if isinstance(old, (int, float)) and isinstance(val, (int, float)) and old != val:
                metric_moves.append({"gate": gid, "metric": key, "from": old,
                                     "to": val, "change": val - old})

    return {
        "since": prev.get("generated"),
        "baseline": False,
        "gates": moved,
        "metrics": metric_moves,
        "appeared": sorted(set(now) - set(was)),
        "disappeared": sorted(set(was) - set(now)),
    }


def _streak(history: list[dict], gate_id: str, current: str) -> int:
    """How many consecutive runs, ending now, this gate has held `current`."""
    n = 1
    for entry in reversed(history):
        if entry.get("gates", {}).get(gate_id) == current:
            n += 1
        else:
            break
    return n


def alerts(cur: dict, d: dict, history: list[dict]) -> list[dict]:
    """Actionable alerts, worst first. Each names what, why, and what to do."""
    out: list[dict] = []
    by_id = {g["gate"]: g for g in cur.get("gates", [])}

    for move in d.get("gates", []):
        gid, to, frm = move["gate"], move["to"], move["from"]
        g = by_id.get(gid, {})
        if frm == PASS and to in (FAIL, ERROR):
            out.append({
                "severity": to, "gate": gid,
                "title": f"{gid} regressed to {to}",
                "detail": g.get("summary", ""),
                "action": _action(gid, g),
            })
        elif frm in (FAIL, ERROR) and to == PASS:
            out.append({
                "severity": PASS, "gate": gid,
                "title": f"{gid} recovered",
                "detail": g.get("summary", ""),
                "action": "nothing — recorded so the streak counter resets",
            })
        elif frm == UNIMPLEMENTED and to != UNIMPLEMENTED:
            out.append({
                "severity": PASS, "gate": gid,
                "title": f"{gid} is no longer a declared gap",
                "detail": g.get("summary", ""),
                "action": "update MISSION.md §3 and the README gate table",
            })

    # A landed upstream obligation is good news that expires: it stays true
    # until someone acts, so it is surfaced from the gate's own detail rather
    # than from a transition it might have made days ago.
    bu = by_id.get("blocked-upstream", {})
    for row in bu.get("detail", {}).get("changed", []):
        if row.get("was", {}).get("files") == 0 and row.get("observed", {}).get("files", 0) > 0:
            out.append({
                "severity": PASS, "gate": "blocked-upstream",
                "title": f"{row['repo']}#{row['issue']} landed upstream",
                "detail": f"`{row['needle']}` now appears in "
                          f"{row['observed']['files']} file(s) under {row['scan_root']}",
                "action": "implement the gate it was blocking, then retire the ledger row",
            })

    # Nobody has picked this up.
    for gid, g in sorted(by_id.items()):
        if g["status"] in (FAIL, ERROR):
            streak = _streak(history, gid, g["status"])
            if streak >= NAG_AFTER and not any(a["gate"] == gid for a in out):
                out.append({
                    "severity": g["status"], "gate": gid,
                    "title": f"{gid} has been {g['status']} for {streak} runs",
                    "detail": g.get("summary", ""),
                    "action": _action(gid, g),
                })

    for m in d.get("metrics", []):
        if m["metric"] in ("stale_backlogs", "links_red") and m["change"] > 0:
            out.append({
                "severity": FAIL, "gate": m["gate"],
                "title": f"{_METRIC_PROSE.get(m['metric'], m['metric'])} rose to {m['to']}",
                "detail": f"{m['from']} → {m['to']} since {d.get('since', 'the last run')}",
                "action": "see the gate's detail for which repos",
            })

    order = {ERROR: 0, FAIL: 1, UNIMPLEMENTED: 2, PASS: 3}
    out.sort(key=lambda a: order.get(a["severity"], 9))
    return out


def _action(gate_id: str, g: dict) -> str:
    """What to actually do. Generic advice is not an action.

    Every branch here has to survive an *errored* gate, whose detail is empty:
    the advice for "this gate broke" is not the advice for "this gate found
    something", and reading a missing field produced `they are past Noned`
    before this was guarded.
    """
    d = g.get("detail", {})
    if g.get("status") == ERROR:
        return f"the gate could not run — {g.get('summary', '')[:120]}"
    if gate_id == "build-chain":
        broken = [l for l in d.get("chain", []) if l.get("state") == "red"]
        if broken:
            first = broken[0]
            return (f"start at the earliest broken link — {first['repo']} "
                    f"@{first['head_sha']}: {', '.join(first['failing'])}"
                    + (f" · {first['url']}" if first.get("url") else ""))
    if gate_id == "estate-backlog" and d.get("stale_days"):
        return f"triage or close the named backlogs; they are past {d['stale_days']}d"
    if gate_id == "blocked-upstream":
        return "reconcile evidence/blocked_upstream.tsv against what the scan found"
    if gate_id in ("set-currency", "set-members-available"):
        return "bump the coherent set in CIRISConformance matrices/current.yaml"
    if gate_id == "conformance-green":
        run = d.get("run", {})
        return f"see {run.get('url')}" if run.get("url") else "check the conformance run on main"
    return f"see `python -m grace run {gate_id}`"


def save(state_dir: Path, rep: dict) -> None:
    """Persist the report and append one compact history line."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "latest.json").write_text(json.dumps(rep, indent=2) + "\n")
    entry = {
        "generated": rep["generated"],
        "passed_all_gates": rep["passed_all_gates"],
        "totals": rep["totals"],
        "gates": {g["gate"]: g["status"] for g in rep.get("gates", [])},
        "metrics": {k: v for k, v in _metrics(rep).items()},
    }
    with (state_dir / "history.jsonl").open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
