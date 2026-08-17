# CIRISGrace

Daily readiness gates for the CIRIS ecosystem. One command that answers
*"can I cut from the substrate today, and if not, what is in the way?"*

This is the orchestration surface, not a verification authority.
[CIRISConformance](https://github.com/CIRISAI/CIRISConformance) decides what
"correct" means and proves it against real published wheels. Grace assembles,
asks, and reports. It cannot change what passing means — the dependency runs
one way, the same as a wheel to a gate.

## Install

Python 3.10+, no dependencies.

```bash
pip install -e .        # or just run it in place: python -m grace
```

## Use

```bash
python -m grace                  # run every gate, print the board
python -m grace gates            # list the gates and what each one asks
python -m grace run floor-currency conformance-green
python -m grace --json out.json  # machine-readable report
python -m grace --offline        # skip anything needing network
python -m grace --root ~/src     # where the CIRIS* checkouts live (default ~)
```

Exit code is 0 only when every gate passed.

## Gates

| id | asks |
|---|---|
| `floor-pins-indexed` | Is every pinned wheel still installable from PyPI? |
| `floor-currency` | How far is the floor behind what is published? |
| `coherent-resolution` | Does the floor set resolve together? |
| `conformance-green` | Did the conformance suite last pass on main? |
| `xfail-ledger` | Which gates are declared-but-unmet, and are their issues linked? |
| `normative-pins` | Are the CC and wire-vocabulary versions pinned? — **not implemented** |
| `head-advance` | Is there a newer coherent set than the floor? — **not implemented** |

The floor is CIRISConformance's `matrices/current.yaml`, read from
`origin/main` rather than the working tree — a checkout on a feature branch or
months stale will otherwise report against a floor nobody ships.

## Statuses

`pass` · `fail` · `unimplemented` · `error`

`unimplemented` is **not** a pass, and does not count toward
`passed_all_gates`. A gate that hides an unasked question behind a green tick
is worse than no gate. Same rule as the conformance suite's refusal to skip:
declare the gap, don't paper over it.

## What is not here yet

- **The daily head recompute.** `matrices/head.yaml` — resolve the newest
  coherent set, run the suite against it, advance on green. This is the point
  of the repo and it is not built. See
  [CIRISConformance#86](https://github.com/CIRISAI/CIRISConformance/issues/86) §0.
- **Normative pins.** The Constitution version lives in prose inside matrix pin
  comments; the wire vocabulary lives in a markdown file. Neither is a field
  anything can read.
- **Artifact assembly and the signed capability report.** #86 §3.

## Status

Early. The gates that exist run and are honest about what they do not check.
