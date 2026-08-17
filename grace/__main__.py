"""grace — the daily driver.

    python -m grace                 run every gate, print the board
    python -m grace gates           list the gates and what each one asks
    python -m grace run <id>...     run named gates
    python -m grace --json out.json write the machine-readable report

Exit code is 0 only when every gate passed. `unimplemented` is not a pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import substrate  # noqa: F401  — importing registers the gates
from .gate import Context, registry, render, report, run_gates

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

    print(f"\n  {REPO} — substrate readiness  ({ctx.root})\n")
    print(render(results))
    print()

    rep = report(results, REPO)
    if args.json:
        args.json.write_text(json.dumps(rep, indent=2) + "\n")
        print(f"  report → {args.json}\n")

    return 0 if rep["passed_all_gates"] else 1


if __name__ == "__main__":
    sys.exit(main())
