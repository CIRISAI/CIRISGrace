# MISSION — CIRISGrace

> Mission Driven Development (MDD): the FSD names *what* we build; this
> document names *why*. Methodology:
> [`~/CIRISAgent/FSD/MISSION_DRIVEN_DEVELOPMENT.md`](../CIRISAgent/FSD/MISSION_DRIVEN_DEVELOPMENT.md)
> and the overview at [ciris.ai/mdd](https://ciris.ai/mdd).

**The daily orchestration surface.** One command that reports whether the
ecosystem is in a state you can cut from, and names what is in the way when it
is not.

**Status**: Early. All eight substrate gates run unattended in CI against no
checkout at all; six pass and two are declared gaps. The daily head recompute —
the point of the repo — is not built. See
[CIRISConformance#86](https://github.com/CIRISAI/CIRISConformance/issues/86).
**Identifier**: `ciris-grace` (Python, stdlib only).
**Last updated**: 2026-08-17 (role settled §5; gates made checkout-free).

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
state, and the sibling checkouts *when they happen to exist*. It writes: a
`ciris-readiness/v1` report and, when the head lands, `matrices/head.yaml`.

**The write surface is exactly two files, both in this repo.** No gate mutates
a sibling — not a branch, not a PR, not a pin. Everything Grace emits is a
proposal a human or a gate accepts.

**No checkout is a prerequisite.** A local sibling is an optimisation: faster,
and it works on a train. Every gate can answer from the network alone, because
a daily driver that only runs at one desk is still a memory everywhere else.

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
| `conformance_tree` | Reads `origin/main` rather than whatever branch a checkout is parked on, and needs no checkout at all — the answer is available where the person is, not only where their clone is |
| `xfail-ledger`'s empty-result guards | A parser that matches nothing reports `error`, not an empty-and-therefore-clean backlog. Same failure class as CIRISGrace#1 |
| `.github/workflows/readiness.yml` | Makes the state already computed rather than merely computable; the scheduled red **is** the notification, so noticing is not a chore anyone can forget |
| `build-chain` | Answers *where did the change stop* from GitHub directly, so the answer survives the status page being down |
| `blocked-upstream` | Turns a declared gap that has quietly landed upstream into a red build, so no fix ships without being acknowledged |
| `estate-backlog` | Distinguishes a growing backlog (health) from an untouched one (rot), instead of gating on a count that would be red every day it worked |
| `ecosystem.public_repos` | One chokepoint deciding what is visible, which is what lets the board be published without leaking a private repo |
| `grace/state.py` | Makes "red since April" and "red since breakfast" different answers, and escalates only once, so the channel stays worth reading |
| `grace/flywheel.py` | Names the eight stations so a stall is diagnosable; scores an unmeasured one `unknown` rather than green |
| `grace/dashboard.py` | Puts the loop in front of a person who has not yet decided to care |
| the daily head (unbuilt) | Removes the hand-bump that is currently the only thing advancing the set |

## 4. Dependencies & gating

- **CIRISConformance** — owns the floor and the gates' meaning. Grace is
  downstream and stays downstream.
- **Published wheels** — the gates read PyPI, not a local build; the question
  is what an installer would get, not what a developer has.
- **`gh`** — used for workflow state and issue state. Absent `gh`, the gates
  that need it report `error`, never `pass`.

## 5. Role — settled 2026-08-17

The three questions this section used to hold open are decided. They are
recorded here rather than deleted, because a decision whose reasoning is not
written down gets relitigated by whoever inherits it.

**The head lives here.** `matrices/head.yaml` is Grace's, not
CIRISConformance's — against #86 §0, which proposed a scheduled job upstream.
The reason is separation: Conformance owns what *passing means*, and a repo
that both defines the standard and computes the candidate that meets it has
merged two roles that should be able to disagree.

This does not weaken "Grace cannot grade itself", because **Grace does not
grade.** It resolves a candidate set, *invokes* Conformance's suite, and reads
`passed_all_gates` off `ciris-conformance-report/v1`. Grace records the verdict;
Conformance renders it. If that ever inverts — if Grace starts deciding what
counts as green — the constraint is broken and this paragraph is the thing that
should have stopped it.

**Grace reports; it never acts.** No bump PR, no merge, no write to any
sibling. The write surface is two files in this repo (§2). The daily value
comes from the answer being *already computed*, not from Grace having hands.
Rejected: opening draft bump PRs. It is genuinely useful and it is how a
read-only tool acquires a credential and, eventually, a mistake at 3am.

**Both cadences, because they answer different questions.** Scheduled catches
rot nobody is looking for; invoked keeps a person in the loop. So: a daily
cron *and* `workflow_dispatch` *and* the local command, one code path, one
report schema. See `.github/workflows/readiness.yml`.

**Scope: ecosystem-wide.** Grace's subject is not only the four-member
coherent set but the whole estate — ~25 tracked `CIRIS*` repositories, their CI
state, release currency, and open blockers. The substrate tier still answers
"can I cut today"; the ecosystem tier answers "what is rotting while I am not
looking".

## 6. Open questions

*Resolved since: the tier rollup (`tiers[t].passed_all_gates`, additive to
`ciris-readiness/v1`); the registry, which is discovered from the org API and
refuses to report on a truncated page rather than silently covering a subset.*

1. **Station RAG is a strict worst-of, and that may be miscalibrated.** One
   failing `Bench` workflow currently paints the whole substrate station red
   while the coherent set underneath it is entirely healthy. Weighting a
   station's checks by consequence would fix it and would also be the first
   arbitrary number in the system. Deferred to burn history — `FSD/OPERATIONS.md`
   §4.1.
2. **Mesh health has no source.** `agents.ciris.ai` is legacy and under
   maintenance; CIRISStatus is written and, as far as Grace can see, serving
   nowhere reachable. Three of eight stations are unmeasured and this is the
   one that matters, because ⑦ is where the loop touches actual users. It may
   warrant an issue upstream — **not filed**, because Grace does not act.
3. **Whether `unimplemented` survives contact with OpenSLO.** The mapping in
   `FSD/OPERATIONS.md` §2.3 models a declared gap as an *unavailable* SLI
   rather than a satisfied objective. That is a deliberate divergence from
   common practice and it should be argued before it is adopted.

## 7. References

- [CIRISConformance#86](https://github.com/CIRISAI/CIRISConformance/issues/86) — the FSD this repo implements
- [CIRISConformance](https://github.com/CIRISAI/CIRISConformance) — the standard and the gates
- [ciris.ai/mdd](https://ciris.ai/mdd) — the methodology

## Update cadence

On any change to what the gates ask. A gate whose question changed without this
file changing is a mission drift.
