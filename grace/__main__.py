"""grace — the daily driver.

    python -m grace                 run every gate, print the board
    python -m grace gates           list the gates and what each one asks
    python -m grace run <id>...     run named gates
    python -m grace --json out.json write the machine-readable report

Exit code is 0 only when every gate passed. `unimplemented` is not a pass.

No checkout of any sibling is required: a local one is used when present
because it is faster and works offline, but every gate can answer from the
network alone. That is what lets the whole board run on a CI runner.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ecosystem, state, substrate  # noqa: F401  — importing registers the gates
from .dashboard import dashboard
from .gate import ERROR, FAIL, Context, markdown, registry, render, report, run_gates

REPO = "CIRISGrace"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grace", description="daily readiness gates")
    p.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["status", "gates", "run"],
        help="status (default) runs everything; gates lists them; run takes ids",
    )
    p.add_argument("ids", nargs="*", help="gate ids, for `run`")
    p.add_argument(
        "--root",
        type=Path,
        default=Path.home(),
        help="directory holding the CIRIS* checkouts (default: ~)",
    )
    p.add_argument("--node", default=None, help="base URL of a running node, if any")
    p.add_argument("--offline", action="store_true", help="skip gates needing network")
    p.add_argument("--json", type=Path, default=None, help="write the report here")
    p.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="write the board as markdown here (e.g. $GITHUB_STEP_SUMMARY)",
    )
    p.add_argument(
        "--ignore-unimplemented",
        action="store_true",
        help=(
            "exit non-zero only on fail/error, not on a declared gap. Affects the "
            "EXIT CODE ONLY; the report still says passed_all_gates=false"
        ),
    )
    p.add_argument(
        "--state",
        type=Path,
        default=None,
        help="directory holding the previous report; enables deltas and alerts",
    )
    p.add_argument(
        "--save-state",
        action="store_true",
        help="also WRITE the run into --state. Separate flag on purpose: an "
             "ad-hoc local run should not enter the committed history",
    )
    p.add_argument("--html", type=Path, default=None, help="write the dashboard here")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "gates":
        reg = registry()
        width = max(len(k) for k in reg)
        for gid in sorted(reg):
            print(f"  {gid.ljust(width)}  {reg[gid].question}")
        return 0

    ctx = Context(root=args.root, node_url=args.node, offline=args.offline)
    only = args.ids if args.command == "run" else None
    results = run_gates(ctx, only)
    rep = report(results, REPO)

    # Deltas before rendering: the board shows what moved, not just where things
    # stand. A snapshot cannot tell "red since April" from "red since breakfast".
    prev, history = state.load(args.state) if args.state else (None, [])
    if args.state:
        rep["deltas"] = state.deltas(prev, rep)
        rep["alerts"] = state.alerts(rep, rep["deltas"], history)

    print(f"\n  {REPO} — readiness  ({ctx.root})\n")
    print(render(results, rep))
    print()

    if args.json:
        args.json.write_text(json.dumps(rep, indent=2) + "\n")
        print(f"  report → {args.json}\n")
    if args.markdown:
        with args.markdown.open("a") as fh:  # a step summary is appended to
            fh.write(markdown(rep))
    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(dashboard(rep, history))
        print(f"  dashboard → {args.html}\n")
    # Written last: a run that crashed rendering should not enter the history
    # as if it had completed.
    if args.state and args.save_state:
        state.save(args.state, rep)

    # `--ignore-unimplemented` is an EXIT-CODE policy, never a report edit. A
    # scheduled run that is red every day because two gaps are declared trains
    # people to ignore it, and then the day it goes red for a real reason
    # nobody looks. So the daily driver asks "did anything get worse?" while
    # the report it publishes keeps saying passed_all_gates=false, out loud,
    # for as long as the gaps are open.
    if args.ignore_unimplemented:
        bad = rep["totals"][FAIL] + rep["totals"][ERROR]
        return 0 if bad == 0 else 1
    return 0 if rep["passed_all_gates"] else 1


if __name__ == "__main__":
    sys.exit(main())
