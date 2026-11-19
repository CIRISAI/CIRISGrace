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
| `set-members-available` | Is every member of the coherent set actually fetchable? |
| `set-currency` | How far is the set behind its own currency sources? |
| `coherent-resolution` | Does the set install together? |
| `conformance-green` | Did the conformance suite last pass on main? |
| `xfail-ledger` | Which gates are declared-but-unmet, and are their issues named? |
| `constitution-pinned` | Is the Constitution pinned to a version and a commit? |
| `wire-vocabulary-pinned` | Is the CEG wire-vocabulary version pinned? — **not implemented** |
| `head-advance` | Is there a newer coherent set than the one on main? — **not implemented** |

## The build scheduler (`grace sched`)

The shared build box runs many sessions' Rust builds on 31 GB. `grace sched`
admits each job only when the box can take it, runs it inside its own capped
scope, measures what it really used, and releases it when it exits.

```bash
python -m grace.sched run --owner edge --kind "edge check" --peak 6 -- cargo check --all-targets
python -m grace.sched run --priority critical --owner server -- cargo test --test drive_crud
python -m grace.sched status            # who runs, who waits, and why
python -m grace.sched doctor            # stuck waiters, unscheduled builds, leaked legacy slots, disk
python -m grace.sched reserve verify --minutes 20    # promise the heavy slot
python -m grace.sched history --kind "edge check"   # measured peaks and durations
bin/cargo_guard.sh cargo build ...      # drop-in for the legacy cargo_guard.sh
```

The rules, each from a real incident (see `grace/sched/policy.py`):

| Rule | Why |
|---|---|
| A slot is a lease (pid + start time), not an open file | A child that inherited the legacy guard's flock held its slot for an hour |
| Children start with no inherited descriptors | The same leak, closed at the source |
| Admit on *projected* memory: available now minus the headroom running jobs still grow into | Granting on `free -g` overcommitted three jobs and the reaper killed one |
| One heavy job (above 4 GB) at a time; light work fills in around it | Two heavy builds at once took whole terminal scopes |
| An 8 GB floor stays free after every admitted peak | The agents, browsers and databases live there |
| A disk floor (30 GB) | The disk filled to 75 MB and every session's writes failed |
| Peaks are learned from the scope's own `memory.peak` | A 4.9 GB spike under a 4 GB grant slipped past a 1 s poll |
| A cap kill is recorded at the cap | The next estimate learns the job needs more |
| A timeout kills this job's own scope only | A watchdog's `pkill -f "cargo build --release"` killed every session's release build |
| Priority (critical, normal, low), then first come; a reservation is kept | Critical-path work and promised slots |
| A starving heavy job stops light backfill behind it | Fairness |
| "Done" is the job exiting; every event goes to `events.jsonl` | Peers had to remember to report |
| `status` names why each waiter waits; `doctor` flags a wait with no reason | A session waited 10 min on a free box |
| Builds under the legacy guard count as running at their own cap | It stays safe while sessions migrate |
| Admission reserves each job's hard cap, not its estimate | Light jobs each under their own cap could together eat the floor |
| No hard cap, no run (exit 78), unless `GRACE_SCHED_ALLOW_UNCAPPED=1` | An uncapped job can outgrow what admission reserved |
| A cap kill needs the kernel's word (`memory.events` or `Result=oom-kill`) | An outside SIGKILL recorded as a cap kill inflated the next estimate |
| A lease outlives a dead supervisor while its job still runs | Reclaiming it hid a live build from admission |
| The scheduler changes nothing outside its job's scope | It once marked the caller's whole unit OOM-avoid |

State lives in `/run/user/<uid>/grace-sched` (else `/tmp/grace-sched-<uid>`; `$GRACE_SCHED_DIR` overrides), refused unless it is a real directory owned by you and closed to others: plain JSON you can
`cat`. Knobs: `GRACE_SCHED_FLOOR_GB`, `GRACE_SCHED_HEAVY_GB`,
`GRACE_SCHED_DISK_FLOOR_GB`, `GRACE_SCHED_MAX_RUNNING`, `GRACE_SCHED_STARVE_SECS`.
Defaults for a build: `CARGO_INCREMENTAL=0`, `CARGO_BUILD_JOBS=2`.

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
