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
python -m grace --markdown b.md  # the board as markdown (e.g. a step summary)
python -m grace --offline        # skip anything needing network
python -m grace --root ~/src     # where the CIRIS* checkouts live (default ~)
```

Exit code is 0 only when every gate passed.

## From the road

The board runs itself. `.github/workflows/readiness.yml` runs every gate daily
at 11:00 UTC and on demand, and publishes three ways:

- **[cirisai.github.io/CIRISGrace](https://cirisai.github.io/CIRISGrace)** — the
  flywheel: eight stations of the ecosystem lifecycle as one turning wheel, each
  expandable to the gates behind its RAG colour and linking out to its repos.
  See [`FSD/OPERATIONS.md`](FSD/OPERATIONS.md).
- the **job summary**, which renders in the GitHub mobile app;
- `ciris-readiness/v1` JSON as a build artifact.

`workflow_dispatch` is the button. Nothing needs installing and nothing needs
cloning.

**Deltas.** Each run is compared against the last and the comparison is
published with the board, because a snapshot cannot tell "red since April" from
"red since breakfast" — which is the only distinction that decides whether you
act now. History lives in `state/`, committed here. Alerts are **changes**, not
conditions; a failure standing for three runs is escalated once more, because
"nobody picked this up" is itself a new fact.

**No checkout is required, of anything.** A local sibling is used when present
because it is faster and works offline, but every gate can answer from the
network alone: the upstream tree is materialised with an anonymous shallow
clone, so no token is needed either. The CI lane deliberately points `--root`
at a directory that does not exist, so it can only ever pass for reasons a
phone-only run would also have.

The scheduled run uses `--ignore-unimplemented`, which changes **the exit code
only** — a declared gap does not turn the lane red. The published report still
says `passed_all_gates=false` for as long as the gaps are open. A lane that is
red every morning for a reason everybody already knows is a lane nobody reads
on the morning it goes red for a new one.

## Gates

Two tiers, rolled up separately. `substrate` answers *can I cut today*;
`ecosystem` answers *what is rotting while I am not looking*. Folding ~48
repositories into the first question's boolean would make it permanently false,
and a boolean that is always false is one nobody reads.

**substrate**

| id | asks |
|---|---|
| `set-members-available` | Is every member of the coherent set actually fetchable? |
| `set-currency` | How far is the set behind its own currency sources? |
| `coherent-resolution` | Does the set install together? |
| `conformance-green` | Did the conformance suite last pass on main? |
| `xfail-ledger` | Which gates are declared-but-unmet, and are their issues named? |
| `constitution-pinned` | Is the Constitution pinned to a version and a commit? |
| `wire-vocabulary-pinned` | Is the CEG wire-vocabulary version pinned? — **not implemented** |
| `head-advance` | Is there a newer coherent set than the one on main? — **not implemented** |

**ecosystem** — public, non-archived repositories only

| id | asks |
|---|---|
| `build-chain` | Where in verify→persist→edge→server→agent has a change stopped? |
| `estate-backlog` | Does every public repo with an open backlog still show movement on it? |
| `blocked-upstream` | Does the blocked-upstream ledger still match reality? |

`blocked-upstream` re-runs every scannable predicate in
`evidence/blocked_upstream.tsv`. **A landed obligation turns it red**, on
purpose: it is the same state machine as a strict xfail, so there is no state
in which the fix shipped upstream and nothing here noticed.

## Where the set comes from

Grace does **not** parse `matrices/current.yaml`. That file's shape belongs to
CIRISConformance and has already changed underneath us once. The set is read
through the contract Conformance publishes for downstream readers:

```console
$ python tools/install_pins.py --print-set     # → ciris-coherent-set/v1
```

run against `origin/main` rather than the working tree, because a checkout can
sit on a feature branch or be months stale.

The family ships on **two channels**, and `channel` is the load-bearing field:

- `pypi` — currency is the newest published release.
- `git` — built from a pinned tag and sha; currency is the tag the
  **integrator** pins in `CIRISServer` main's `Cargo.toml`. Asking PyPI about a
  git member returns an answer, and that answer is an abandoned release many
  majors behind.

A member arriving with a channel Grace does not recognise raises rather than
being skipped, so the next channel the family invents cannot silently shrink
the set. See [CIRISGrace#1](https://github.com/CIRISAI/CIRISGrace/issues/1) for
the failure this rule was written against.

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
