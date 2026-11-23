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
| At most two heavy jobs (above 4 GB) at once, each hard-capped; light work fills in around them (`GRACE_SCHED_MAX_HEAVY=1` restores one) | Two uncapped heavy builds took whole terminal scopes; capped, the memory budget keeps them apart |
| A 6 GB floor stays free after every admitted peak (8 until 2026-11-23) | The agents, browsers and databases live there |
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
| A job with no declaration and no history keeps the legacy guard's 11 GB cap | A build that fitted under the old guard must not die on its first run here |
| While sessions migrate, a heavy job also holds the legacy guard's free slot locks | An old copy of the guard cannot see this scheduler's jobs |
| An idle box (no heavy job running) admits on cap + floor/4 or peak + floor/2 | The full floor on a lone capped job held a 14 GB build off a 22 GB free box |
| A waiting critical heavy job holds back other heavy starts | A normal test lane took the memory persist's critical build was waiting for |
| An exclusive reservation stops every other owner's starts; running jobs drain | A release certify needs the box to itself |
| After 3 measured runs a kind's estimate may go *below* its declaration (not under 0.5 GB); the cap keeps the declaration | Declared 10 GB jobs that needed 0.2 GB waited behind the floor |
| A small job (needs 1 GB or less on each of its last runs) needs only half the floor, even beside a heavy job, and may pass a starving head | Commit hooks and 0.3 GB tests waited 7-30 min behind one 9.5 GB lane |
| Reclaim: a job that reached 70% of its estimate, then held under half its maximum for 120 s, reserves only max(used x1.3, used + 1.5 GB); its cap is unchanged | A linked test held a 9.2 GB reservation at 2.5 GB used for 17 min |
| Nothing starts while memory pressure (PSI `some avg10`) is above 10% | A static floor guesses at pressure; PSI measures it |
| While CPU pressure (PSI `some avg10`) is above 25% and a scheduled job runs, a further normal job waits; critical, small and starving jobs pass, and an empty schedule never waits | At load 34 on 32 cores a second build only splits the cores |

State lives in `/run/user/<uid>/grace-sched` (else `/tmp/grace-sched-<uid>`; `$GRACE_SCHED_DIR` overrides), refused unless it is a real directory owned by you and closed to others: plain JSON you can
`cat`. Knobs: `GRACE_SCHED_FLOOR_GB`, `GRACE_SCHED_HEAVY_GB`,
`GRACE_SCHED_DISK_FLOOR_GB`, `GRACE_SCHED_MAX_RUNNING`, `GRACE_SCHED_STARVE_SECS`,
`GRACE_SCHED_PSI_MAX` (memory pressure gate, %), `GRACE_SCHED_CPU_PSI_MAX` (CPU gate, %).
Defaults for a build: `CARGO_INCREMENTAL=0`, `CARGO_BUILD_JOBS=2`.

### Compared with other schedulers

`grace sched` is a single-box admission controller. Each rule above borrows
from a larger system, cut down to one machine and a handful of sessions:

| Idea | Where it comes from | What grace keeps, and what it drops |
|---|---|---|
| Admit on a reservation, enforce a hard cap | Kubernetes requests/limits | Request = learned estimate, limit = the scope's `MemoryMax`. No per-pod QoS classes; priority is three words |
| Learn the reservation from measured use | Kubernetes VPA, Borg's usage-based estimates | Learns from each scope's own `memory.peak`, keyed by job kind; may learn below a declaration only after 3 runs |
| Give back headroom a job no longer uses | Borg reservation reclamation | Only after the job has peaked and settled; the cap never shrinks, so a regrowing job is killed at its cap, not overcommitted |
| Let short jobs pass a blocked head | Slurm backfill | Without runtimes to plan on, "short" is "small" (memory need of 1 GB or less, measured) |
| Gate on measured pressure, not a static number | Meta oomd/senpai (PSI); Kubernetes KEP-5394 (PSI node conditions, alpha) | Only ever holds a *start*; never kills or throttles a running job |
| Hold a start while the CPU is saturated | `make -l` / `ninja -l`, HTCondor `START` on `LoadAvg`, GNU parallel `--load` | Uses CPU PSI rather than load average (load counts I/O waits and lags a minute); like `make -l`, it never holds the first job |
| Keep free memory for the rest of the box | GNU parallel `--memfree`, systemd `MemoryMin` on other units | A fixed floor plus the PSI gate, because the box's other residents (agents, browsers, databases) are not under the scheduler |

Not borrowed, on purpose: preemption (Borg, Slurm, parallel's
`--memfree` kill-and-requeue). A killed Rust build loses its whole compile, so
grace waits rather than evicts. `memory.high` soft limits (throttling a job
before the cap kills it) and I/O admission are the open items.

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
