# MISSION — CIRISGrace

> Mission Driven Development (MDD): the FSD names *what* we build; this
> document names *why*. Methodology:
> [`~/CIRISAgent/FSD/MISSION_DRIVEN_DEVELOPMENT.md`](../CIRISAgent/FSD/MISSION_DRIVEN_DEVELOPMENT.md)
> and the overview at [ciris.ai/mdd](https://ciris.ai/mdd).

**The daily orchestration surface.** One command that reports whether the
ecosystem is in a state you can cut from, and names what is in the way when it
is not.

**Status**: Early. The substrate gates run; the daily head recompute — the
point of the repo — is not built. See
[CIRISConformance#86](https://github.com/CIRISAI/CIRISConformance/issues/86).
**Identifier**: `ciris-grace` (Python, stdlib only).
**Last updated**: 2026-08-16 (repo created).

---

## 1. MISSION (WHY)

Meta-Goal M-1: *promote sustainable adaptive coherence — the living conditions
under which diverse sentient beings may pursue their own flourishing in justice
and wonder.*

The ecosystem's claim is that a consequential action must arrive as an
externally verifiable artifact rather than a claim. The build is the last place
in the stack still running on trust: whether the substrate is coherent today,
whether the floor still installs, whether a declared gap has quietly closed —
all of it currently lives in one person's head, refreshed by hand.

That is a coherence failure of the ordinary kind. A person who has to
re-derive the state of eight repositories before starting work has less
attention left for the work, and the parts they skip are the parts that rot.
Grace exists so that state is a command, not a memory.

**Two constraints it inherits, not invents:**

- **`unimplemented` is not a pass.** A gate that hides an unasked question
  behind a green tick is worse than no gate. This is the conformance suite's
  refusal-to-skip rule, applied to tooling.
- **Grace cannot grade itself.** It assembles and asks; CIRISConformance
  decides what passing means. A producer that defines its own standard is not
  accountable to anything.

## 2. The composition contract (WHO / WHAT / HOW)

**WHO — protocols.** Grace reads: CIRISConformance's `matrices/current.yaml`
(the floor, from `origin/main`), PyPI's release index, GitHub Actions run
state, and the sibling checkouts. It writes: a `ciris-readiness/v1` report and,
when the head lands, `matrices/head.yaml`. It has no authority over any of
them; every write is a proposal a human or a gate accepts.

**WHAT — schemas.** `Result{gate, status, summary, detail}` and the report
envelope `{schema, repo, generated, gates[], totals, passed_all_gates}`. The
report envelope deliberately mirrors CIRISConformance's
`ciris-conformance-report/v1`, including `passed_all_gates`, so a consumer can
assert on one field instead of parsing output.

**HOW — logic.** One question per gate, four possible answers, no partial
credit. Gates are pure reads; nothing here mutates a sibling repo. Network
access is opt-out via `--offline` so the tool still answers on a train.

## 3. Mission alignment per component

| Component | How it advances M-1 |
|---|---|
| `grace/gate.py` | Makes "we did not check that" a first-class, visible state |
| `read_set` + `_validate` | Reads the set through Conformance's published contract and **refuses** a shape it does not fully understand, so coverage cannot shrink quietly |
| `set-members-available` · `set-currency` | Turn set rot from something discovered by six red CI cells into something reported before work starts, per channel |
| `constitution-pinned` | Makes the normative half of the set a checked field rather than prose in a comment |
| the report envelope | Lets a machine consume readiness, so the human is not the integration point |
| `grace sched` (the build scheduler) | Turns the shared box's capacity from a negotiation between sessions, where each lesson was learned by an OOM, a full disk or a killed build, into a checked rule that names why a job waits; the box's state is a command, not one coordinator's memory |
| the daily head (unbuilt) | Removes the hand-bump that is currently the only thing advancing the set |

## 4. Dependencies & gating

- **CIRISConformance** — owns the floor and the gates' meaning. Grace is
  downstream and stays downstream.
- **Published wheels** — the gates read PyPI, not a local build; the question
  is what an installer would get, not what a developer has.
- **`gh`** — used for workflow state and issue state. Absent `gh`, the gates
  that need it report `error`, never `pass`.

## 5. Open questions

1. Where does the head live — here or in CIRISConformance? Filed as #86; the
   argument for here is neutrality, the argument against is a second repo that
   can rot. Unresolved.
2. Does Grace ever *act* (open the bump PR), or only report? Acting makes it
   useful daily; acting also makes it a thing that can be wrong at 3am.
3. What is the right cadence — a scheduled run, or a command someone invokes
   deliberately at the start of the day? Both are defensible: a cron catches
   rot nobody is looking for, an invoked command keeps a person in the loop.

## 6. References

- [CIRISConformance#86](https://github.com/CIRISAI/CIRISConformance/issues/86) — the FSD this repo implements
- [CIRISConformance](https://github.com/CIRISAI/CIRISConformance) — the standard and the gates
- [ciris.ai/mdd](https://ciris.ai/mdd) — the methodology

## Update cadence

On any change to what the gates ask. A gate whose question changed without this
file changing is a mission drift.
