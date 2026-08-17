# FSD — Constitutional-mesh operations under Grace

**One flywheel, eight stations, one composite error budget.**

| | |
|---|---|
| **Status** | DRAFT — for discussion, not locked |
| **Owner** | CIRISGrace (orchestration surface; owns no standard) |
| **Date** | 2026-08-17 |
| **Reads against** | CIRISConformance `3658d42` · CIRISServer `a15f4710` · CIRISAgent `048be65c` · CIRISVerify `82960ea7` · org estate at 48 public non-archived repos |
| **Conforms to** | [OpenSLO](https://openslo.com/) `openslo.com/v2alpha` |
| **Implements** | the operations half of [CIRISConformance#86](https://github.com/CIRISAI/CIRISConformance/issues/86) |

Every factual claim is marked **[today]** (observed in the tree or over the API
at the commits above, by a Grace gate that ran) or **[proposed]** (does not
exist). Nothing marked [today] is aspirational. Where a claim could not be
checked at all it is marked **[unknown]**, which is a finding, not a hedge.

---

## 1. The flywheel

The ecosystem is not a pipeline with a start and an end. It is a loop, and the
loop is the product: evidence sharpens the norm, the norm drives the backlog,
the backlog drives the substrate, the substrate becomes product, product serves
users over the mesh, the mesh records what happened, and what happened is the
next round's evidence.

```
        ┌────────────────────────────────────────────────────────────┐
        │                                                            │
        ▼                                                            │
  ①  EPISTEMICS                                                      │
     RATCHET · CIRISOntology · evidence/*.tsv                        │
        │  validated claims, falsifiers, documented limits           │
        ▼                                                            │
  ②  NORMATIVE                                                       │
     CIRISConstitution (CC = Accord + CEG)                           │
        │  one version line                                          │
        ▼                                                            │
  ③  CONFORMANCE                                                     │
     CIRISConformance — the floor, the gates, what passing means      │
        │  matrices/current.yaml · ciris-conformance-report/v1        │
        ▼                                                            │
  ④  BACKLOG                                    ⑧  TRACES           │
     issues · xfail ledger · blocked_upstream        the mesh's own   │
        │  declared, unmet, issue-linked            corpus: signed    │
        ▼                                          CEG objects,       │
  ⑤  SUBSTRATE                                    hash-chained audit  │
     CIRISVerify → CIRISPersist → CIRISEdge            ▲              │
        │  wheels + git tags = the coherent set          │            │
        ▼                                                │            │
  ⑥  PRODUCT                                             │            │
     CIRISServer · CIRISAgent · CIRISClient              │            │
        │  attested capability, signed manifest          │            │
        ▼                                                │            │
  ⑦  MESH & COMMERCE ───────────────────────────────────┘            │
     running nodes · CIRISStatus · Billing · Proxy · Depot            │
        │                                                            │
        └────────────────────────────────────────────────────────────┘
                  traces become evidence; the wheel turns
```

The flywheel metaphor is load-bearing, not decorative. A flywheel stores
energy and releases it smoothly; it also has the property that **one seized
bearing stops the whole wheel, and you cannot tell which one from the fact that
it stopped.** Naming the stations is what makes the stall diagnosable.

### 1.1 Station inventory — artifacts and gaps

Each station is judged on whether its output is an **externally verifiable
artifact** or a **claim**. That is the ecosystem's own thesis applied to its own
operations, and it is the only criterion used below.

#### ① Epistemics — RATCHET · CIRISOntology · the evidence registries

| Artifact | State |
|---|---|
| `RATCHET/VALIDATED_FINDINGS.md`, `KNOWN_LIMITATIONS.md` | **[today]** prose, human-read |
| `RATCHET/formal/` — Lean 4 proof obligations (TC, DP, CA, NEW) | **[today]** machine-checked |
| `RATCHET/evidence/` | **[today]** present |
| `CIRISOntology/*.lean`, `lakefile.toml`, `GATES.md`, `Audit/` | **[today]** Lean 4, machine-checked |
| `evidence/cc_impl.tsv` — claim → `path#symbol` @ `crate@version` | **[today]** in CIRISServer, CIRISPersist, CIRISVerify, CIRISEdge, CIRISAgent |
| `evidence/blocked_upstream.tsv` — scannable predicates | **[today]** in CIRISServer, CIRISClient, CIRISGrace |
| A machine-readable index of *which* claims have evidence anywhere | **[proposed]** — see gap E-1 |

**Gap E-1 — the registries do not compose.** Five repos carry a `cc_impl.tsv`
and each is validated locally (CIRISServer's `tools/check_evidence.py` resolves
every `path#symbol` at the pinned crate version). Nothing joins them, so
"which CC claims have no enforcing symbol *anywhere* in the estate" is a
question no artifact answers. **[today]** Grace does not ask it either.

**Gap E-2 — RATCHET's findings do not reach the norm as data.** **[today]**
RATCHET's outputs are markdown, and its own documentation frames the consumer
as CIRISAgent's design, not CIRISConstitution. The arrow ①→② in the diagram is
carried by a person reading a file. A validated finding that contradicts a
constitutional claim has no mechanism to say so.

**Gap E-3 — two proof systems, no shared gate.** **[today]** CIRISOntology and
RATCHET both use Lean 4. Neither is in any CI lane Grace can see, so "do the
proofs still build" is **[unknown]**.

#### ② Normative — CIRISConstitution

| Artifact | State |
|---|---|
| `VERSION` — the single version line | **[today]** a readable file |
| `constitution/`, `manifests/`, `validation/`, `tools/` | **[today]** present |
| CC version as a field in the coherent set | **[today]** `constitution: {version, repo, commit}` — landed via CIRISConformance#88 |
| CEG wire-vocabulary version as a field | **[proposed]** — `WIRE_VOCABULARY_KINDS.md` is markdown with no readable version |

**Gap N-1 — the wire vocabulary is prose.** **[today]** Verified by Grace: the
predicate `^wire_vocabulary:` matches **0 files** under CIRISConformance
`matrices/*.yaml`. `grace run wire-vocabulary-pinned` therefore returns
`unimplemented`, which is the honest answer and not a passing one. This is the
outstanding half of CIRISConformance#86 §4 class 3; the constitution half is
discharged and is now a real gate.

**Gap N-2 — nothing checks CC's pin against CC itself.** **[today]** The
coherent set pins `CC 1.0-rc2 @ 5818a75`. CIRISConstitution ships a `VERSION`
file. **[unknown]** whether they agree — no gate compares them, so the
normative pin can drift from the normative source silently. This is a
one-gate fix and is proposed as `constitution-current` in §4.

#### ③ Conformance — CIRISConformance

| Artifact | State |
|---|---|
| `matrices/current.yaml` — the floor, two-channel (`pypi` \| `git`) | **[today]** |
| `tools/install_pins.py --print-set` → `ciris-coherent-set/v1` | **[today]** the downstream reader's contract |
| `ciris-conformance-report/v1` with `passed_all_gates` | **[today]** the certification surface |
| 62 test files; markers `substrate`, `fabric` | **[today]** |
| `matrices/head.yaml` + daily recompute | **[proposed]** — #86 §0 |
| `run-against-client.yml`, `client` marker, `test_6xx` band | **[proposed]** — #86 §2 |

**[today]** `conformance.yml` last passed on `main` on 2026-08-17. The floor is
green. That is what makes this document possible: a head cannot be advanced off
a red suite, so Phase 0 was blocked until now and is not blocked any more.

**Gap C-1 — the floor is hand-bumped, so it rots.** **[today]** Verified by
Grace: the predicate `head\.yaml` matches **0 files** under
`.github/workflows/`. The recorded failure this causes is in the ledger — on
2026-08-16 the pinned `ciris-server==0.5.131` was no longer installable from
PyPI at all, because that index retains ~5 releases.

**Gap C-2 — no client tier.** **[today]** Verified: `run-against-client` matches
0 files; the literal `"client"` matches 0 occurrences in `conftest.py`. A client
regression cannot turn a consumer's tag-run red.

#### ④ Backlog

| Artifact | State |
|---|---|
| Open issues across 48 public repos | **[today]** readable in one org GraphQL call |
| `@pytest.mark.xfail(strict=True, reason=…CIRISx#N)` | **[today]** 16 in Conformance, **0 without an issue reference** |
| `evidence/blocked_upstream.tsv` | **[today]** 4 obligations, every predicate re-verified absent |
| Shared xfail ↔ `SubstrateGate` registry | **[proposed]** — #86 §3 |

**[today]** This is the healthiest station in the wheel. The xfail→issue rule is
enforced and holding at 100%, and the blocked-upstream predicates are now
re-scanned automatically rather than trusted.

**Gap B-1 — one estate backlog has stalled.** **[today]** Verified by Grace:
`CIRISOssicle` has 1 open issue untouched for 94 days. Everything else in the
estate has moved inside 90 days.

#### ⑤ Substrate — CIRISVerify → CIRISPersist → CIRISEdge

| Artifact | State |
|---|---|
| The coherent set: 4 members, 2 `pypi` + 2 `git` | **[today]** all fetchable, all at their currency source |
| Cumulative build cache keys (`KEY_VERIFY`…`KEY_SERVER`) | **[today]** CIRISServer#99 |
| Coherent resolution (`pip install --dry-run` over the set) | **[today]** resolves |

**[today]** Chain health as measured: CIRISVerify **green**, CIRISPersist
**green**, CIRISEdge **red** (`Bench`).

#### ⑥ Product — CIRISServer · CIRISAgent · CIRISClient

| Artifact | State |
|---|---|
| `ciris-client-manifest.json` on tags | **[today]** CIRISServer `client-artifacts.yml` |
| `scripts/sync-client-version.sh` + 3 enforcement points | **[today]** scar tissue for a human transport |
| 4 byte-identical locale bundles + mutation-tested guard | **[today]** |
| `generated-api` (730 files, ~48k LOC) | **[today]** committed, **no regeneration task in the build graph** |
| `ciris-capability/v1` — signed, merged, attached | **[proposed]** — #86 §4 |

**[today]** Chain health: CIRISServer **red** (`CI`, `bench`), CIRISAgent **red**
(`CodeQL`, `Safety evidence sync`, `Windows Desktop UI`). **[today]** CIRISClient
has **zero workflows** — the client is the least gated link in the chain and the
one #86 spends two sections on.

#### ⑦ Mesh & commerce — running nodes · CIRISStatus · Billing · Proxy · Depot

| Artifact | State |
|---|---|
| CIRISStatus: `/health`, `/v1/status`, `/api/v1/status`, `/status/history`, `/status/events`, `/status/vantage`, `/api/v1/ci`, `/api/v1/scoring`, SSE + WS | **[today]** implemented in the repo |
| Signed `observation:reachability:v1` CEG attestations, hybrid Ed25519 + ML-DSA-65 | **[today]** |
| Multi-vantage disagreement surface | **[today]** — agreement implicates the component, disagreement implicates the path |
| Statuspage-v2 `indicator` + per-pool `min_available` capability rollup | **[today]** — the headline derives from capabilities, not from the unhappiest component |
| A reachable production status URL | **[unknown]** — see gap M-1 |
| `CIRISDepot` — the artifact distribution path | **[proposed]** — `SPEC.md` only; no implementation |

**Gap M-1 — mesh health is unobservable from here.** **[today]**
`agents.ciris.ai/lens/api/v1/…` returns **502**; it is legacy and under
maintenance. No other status host resolves. So the one station that reports on
the *running* system is the one station Grace cannot read, and every
mesh-derived objective in §3 is consequently `[unknown]` rather than green.
**This is the single highest-value gap in the wheel**, because ⑦ is where the
loop's only contact with actual users happens.

**Gap M-2 — the distribution path is a document.** **[today]** CIRISDepot
contains `LICENSE`, `README.md`, `SPEC.md` and nothing else. Until it exists,
the distribution path is GitHub Releases and PyPI — the two things its spec
says it replaces, and the two things whose retention policy already caused C-1.

**Gap M-3 — commerce has no readiness surface.** **[today]** CIRISBilling and
CIRISProxy are running services with their own DBs and egress, and CIRISBilling
ships `OBSERVABILITY.md`. Neither exposes anything Grace reads. Their lane type
per #86 §4.1 is `contract`, and no contract lane exists.

#### ⑧ Traces — the mesh's own corpus

| Artifact | State |
|---|---|
| Traces stored in the mesh itself — CIRISServer's corpus, not a separate repo | **[today]** |
| Signed CEG objects; `FederationDirectory::put_attestation`; PQC-mandatory at the v9.0.0 ingest gate (CC 5.3.2.4.3.1) | **[today]** |
| Hash-chained audit — a transparency log by another name | **[today]** |
| Content-addressed signed blobs, Merkle manifests with inclusion proofs | **[today]** `test_110_ccs_blob_integrity`, `test_578` |
| A query path from mesh traces back to ① | **[proposed]** — see gap T-1 |

**Gap T-1 — the return arc is not wired.** **[today]** The primitives exist and
are already substrate-grade. What does not exist is anything that reads the
mesh's accumulated traces and produces an input to RATCHET or to a
constitutional claim. ⑧→① is the arc that makes this a flywheel rather than a
pipeline, and it is the arc with the least machinery on it.

### 1.2 The wheel, scored

**[today]** As computed by `grace/flywheel.py` on 2026-08-17. This table is a
transcription, not an assertion — `flywheel.assess()` is the source of truth
and the published page renders the same numbers. If they ever disagree, the
code is right and this section is stale.

| # | Station | Checks | RAG | Why |
|---|---|---|---|---|
| ① | Epistemics | 0 | ⬛ unmeasured | no gate exists |
| ② | Normative | 2 | 🟡 amber | `wire-vocabulary-pinned` is a declared gap |
| ③ | Conformance | 4 | 🟡 amber | floor green, but `head-advance` is a declared gap |
| ④ | Backlog | 2 | 🔴 red | `estate-backlog` — one repo idle 94 days |
| ⑤ | Substrate | 5 | 🔴 red | set is clean; CIRISEdge `Bench` is failing |
| ⑥ | Product | 2 | 🔴 red | CIRISServer `CI`/`bench`, CIRISAgent ×3 |
| ⑦ | Mesh & commerce | 0 | ⬛ unmeasured | nothing reachable to gate on |
| ⑧ | Traces | 0 | ⬛ unmeasured | no gate exists |

**Integrity 21% · coverage 67%.** These are deliberately two numbers, not one.
Integrity is scored over the five stations that have a gate; coverage is how
much of the wheel has one at all. A single blended figure would answer neither
question — "a third of it is broken" and "a third of it is invisible" have
completely different repairs, and averaging them hides both.

**An unmeasured station scores zero, never green.** It is excluded from
integrity (you cannot judge what you cannot see) but counted against coverage.
This is `unimplemented`-is-not-a-pass applied one level up.

Note ⑤: the coherent set itself is entirely healthy — all four members
fetchable, all at their currency source, and they resolve together. The station
is red purely on CIRISEdge's `Bench` workflow. The strict worst-of rollup is
intentional but it does mean a station's colour can be dominated by its least
important check; §4.1 defers calibration until there is burn history to
calibrate against.

---

## 2. Mapping to OpenSLO v2alpha

`SLO2` is [OpenSLO](https://openslo.com/) — the vendor-neutral, YAML,
Kubernetes-shaped open specification for declaring service level objectives.
**[today]** v1.0 is released and `openslo.com/v2alpha` is the in-development
line; this document targets v2alpha and will pin to v2 when it stabilises.
**[today]** the org contains **zero** OpenSLO documents, so everything in this
section is **[proposed]**.

### 2.1 Why it fits

The mapping is unusually clean, and that is a reason to adopt rather than
invent:

| OpenSLO v2alpha kind | Flywheel meaning |
|---|---|
| `Service` | one station of the wheel |
| `SLI` | one Grace gate — it already returns a measurement with a good/total or threshold shape |
| `SLO` | the readiness objective for a station, with a target and a window |
| **composite `SLO`** | **the flywheel itself** — many objectives, one error budget, burns if *any* objective burns |
| `AlertPolicy` / `AlertCondition` | `grace/state.py` — the existing alert derivation |
| `AlertNotificationTarget` | the scheduled-run failure mail; later, an issue |
| `DataSource` | GitHub Actions API · org GraphQL · PyPI · CIRISStatus |

The composite SLO is the point. **[today]** OpenSLO composites exist precisely
to monitor an end-to-end user journey across independently-measured stages,
they burn one shared budget if any objective burns, and `compositeWeight` tunes
each objective's contribution. That is a flywheel expressed in someone else's
already-standard vocabulary — which is the same argument this ecosystem makes
everywhere else about not defining your own standard.

### 2.2 Field-level correspondence

Grace's `ciris-readiness/v1` and OpenSLO line up as follows. Nothing here
requires changing what a gate asks; it requires declaring what a gate *means*.

| `ciris-readiness/v1` | OpenSLO v2alpha | Note |
|---|---|---|
| gate `id` | `SLI.metadata.name` | already kebab-case and stable |
| gate `question` | `SLO.spec.description` | already a plain-language sentence |
| `status ∈ {pass,fail}` | `sli` good/total event | a binary SLI, `budgetingMethod: Occurrences` |
| `status = unimplemented` | **no OpenSLO equivalent** | see §2.3 — this is the one real impedance mismatch |
| `status = error` | measurement unavailable | must burn budget, not be dropped |
| `detail.metrics.*` | threshold `sli` with `op` + `value` | `links_red`, `open_issues`, `stale_backlogs`, `outstanding` |
| `tier` | `Service` grouping | `substrate` and `ecosystem` become services |
| `deltas` / `alerts` | `AlertPolicy` + `AlertCondition` | already computed; needs the declarative form |
| history retention | `timeWindow` (`isRolling`, `duration`) | `state/history.jsonl` is the data behind it |

### 2.3 The one thing OpenSLO does not have

**`unimplemented` has no OpenSLO representation, and it must not be mapped to
"good".** OpenSLO's world is good events and bad events; a question nobody
asked is neither. Mapping a declared gap onto a satisfied objective would
reintroduce, at the SLO layer, exactly the failure this ecosystem refuses at
the test layer — a green tick over an unasked question.

**[proposed]** The resolution: a declared gap is modelled as an objective whose
SLI is **unavailable**, not as one that is met. Under `Occurrences` an
unavailable SLI contributes no good events, so the objective cannot reach its
target and the composite budget reflects an unmeasured station honestly.
Encoding it as `target: 0` would be worse — it would make the gap permanently
satisfiable and therefore invisible.

This is a divergence from common OpenSLO practice and it is deliberate. It
should be written down in the adopting document rather than discovered later by
someone wondering why the budget never recovers.

### 2.4 Sketch — the composite

**[proposed]** One `SLO` per station, and one composite over all eight:

```yaml
apiVersion: openslo.com/v2alpha
kind: SLO
metadata:
  name: constitutional-mesh-flywheel
spec:
  description: The wheel turns — evidence to norm to backlog to substrate to
    product to mesh to traces to evidence.
  service: ciris-mesh
  budgetingMethod: Occurrences        # all composite objectives must agree
  timeWindow:
    - duration: 28d
      isRolling: true
  objectives:
    - displayName: substrate is cuttable
      sliRef: grace-substrate-tier
      target: 0.99
      compositeWeight: 3              # a stalled substrate stalls everything
    - displayName: build chain intact
      sliRef: grace-build-chain
      target: 0.95
      compositeWeight: 2
    - displayName: ledger matches reality
      sliRef: grace-blocked-upstream
      target: 0.99
      compositeWeight: 1
    - displayName: mesh reachable
      sliRef: grace-mesh-health        # SLI unavailable today — gap M-1
      target: 0.99
      compositeWeight: 3
```

`compositeWeight` is where the flywheel's physics get written down: a seized
bearing at ⑤ or ⑦ should cost more budget than a stalled backlog at ④, because
it stops more of the wheel. **[proposed]** and the weights above are a starting
position, not a decided one.

---

## 3. Operations under Grace

### 3.1 What Grace is, restated against the wheel

Grace is the **instrument**, not a station. It reads every station and reports;
it is not in the loop it measures. Three constraints follow and none is
negotiable:

1. **Grace cannot grade itself.** It resolves candidates and invokes
   CIRISConformance's suite, then reads `passed_all_gates` off
   `ciris-conformance-report/v1`. Conformance renders the verdict; Grace
   records it.
2. **Grace never writes to a sibling.** Its entire write surface is
   `matrices/head.yaml`, `state/`, and the published page — all in its own repo.
3. **`unimplemented` is not a pass**, at the gate layer and at the SLO layer
   (§2.3).

### 3.2 What runs, and where

**[today]** All of this exists and is verified:

| | |
|---|---|
| Gates | 8 substrate + 3 ecosystem, in two tiers with independent rollups |
| Checkout-free | every gate answers from the network alone; a sibling checkout is an optimisation |
| Auth | anonymous shallow clone for trees, ambient token for the API — **no PAT required** |
| Runtime | ~40 s for the full board |
| Cadence | daily cron at 11:00 UTC, `workflow_dispatch`, and the local command — one code path |
| Surfaces | terminal · `$GITHUB_STEP_SUMMARY` · `ciris-readiness/v1` JSON · the published page |
| History | `state/latest.json` + `state/history.jsonl`, committed here |
| Exit policy | red on `fail`/`error` only; a declared gap never turns the lane red, and the report still says `passed_all_gates=false` |

### 3.3 The published page

**[proposed]** `cirisai.github.io/CIRISGrace` renders §1's flywheel as the
primary object — stations as arcs, each linking to its repositories, each
expandable to the gates and artifacts behind its RAG state, with the composite
budget from §2.4 at the centre.

**Public inputs only, structurally.** The ecosystem tier derives every subject
from one function that filters the org API on `visibility: PUBLIC` and
`isArchived: false`. A private repo cannot reach a gate and therefore cannot
reach the page. This is what reconciles a public dashboard with the repository
rule that report output is internal until reviewed — and it is the property to
check first if a gate is ever added.

### 3.4 Cadence and the notification

The scheduled red **is** the notification; nobody has to remember to look. That
only works if red means something changed, so:

- alerts are **changes**, not conditions — re-announcing a known failure every
  morning is how a channel becomes noise;
- **except** a failure standing for ≥3 runs, escalated exactly once, because
  "nobody picked this up" is itself a new fact.

---

## 4. Gaps, owners, and phasing

Every gap from §1.1, with the station it seizes and what would discharge it.

| Gap | Station | Discharged when | Phase |
|---|---|---|---|
| **M-1** mesh unobservable | ⑦ | a reachable status URL Grace can gate on | **0** |
| **C-1** floor hand-bumped | ③ | `head.yaml` written by a green recompute | **0** |
| **N-2** CC pin vs CC source | ② | `constitution-current` gate compares the pin to `CIRISConstitution/VERSION` | **0** |
| **B-1** stalled backlog | ④ | CIRISOssicle triaged or closed | **0** |
| **N-1** wire vocabulary is prose | ② | `wire_vocabulary:` exists as a matrix field | 1 |
| **C-2** no client tier | ③ | `run-against-client.yml` + `client` marker | 1 |
| **E-1** registries do not compose | ① | a joined claim→evidence index across the five `cc_impl.tsv` | 2 |
| **E-3** proofs ungated | ① | CIRISOntology and RATCHET Lean builds in a lane Grace reads | 2 |
| **M-3** commerce has no surface | ⑦ | a `contract` lane for Billing and Proxy | 2 |
| **M-2** Depot is a spec | ⑦ | an implementation behind `SPEC.md` | 3 |
| **E-2** RATCHET findings not data | ① | a machine-readable findings artifact CC can consume | 3 |
| **T-1** return arc unwired | ⑧ | a query path from mesh traces to ① | 3 |

**Phase 0 is four gaps and three of them are small.** N-2 and B-1 are a gate and
a triage. C-1 is unblocked for the first time, because the floor went green
today. M-1 is the one that needs a decision rather than an afternoon.

### 4.1 Sequencing — do not invert

1. **Make the wheel observable** (M-1, N-2) — you cannot set an objective on a
   station you cannot measure, and an SLO over an unmeasured station is a
   fiction with a number attached.
2. **Declare the SLOs** (§2) once the SLIs exist and have history to calibrate
   against. **[today]** `state/history.jsonl` starts accumulating that history
   from the first scheduled run.
3. **Then** weight the composite. Weights chosen before there is burn data are
   guesses that will be defended as decisions.

---

## 5. Acceptance criteria

1. Every station in §1 has at least one gate, or an explicit `unimplemented`
   naming the issue that blocks it. No station is silently unmeasured.
2. The flywheel renders publicly, from public inputs only, and each station
   links to the repositories and gates behind its RAG state.
3. The composite budget in §2.4 is computed from real SLI history, not
   asserted.
4. `unimplemented` never satisfies an objective, at any layer.
5. A gap in §4 cannot be closed without the row moving — the table is the
   backlog, and it is checked, not narrated.
6. Grace still writes to nothing but its own repo.

---

## 6. Open questions

1. **Where does the mesh status URL live, and who operates it?** M-1 blocks
   more than it looks like it does. `agents.ciris.ai` is legacy and under
   maintenance; CIRISStatus is written and, as far as Grace can see, not
   serving anywhere reachable. This may need an issue filed against
   CIRISStatus — **not filed**, because Grace does not act and the call is not
   mine to make.
2. **Do the composite weights in §2.4 stand?** They encode a claim about which
   stations matter most, and that claim should be argued rather than defaulted.
3. **Does the head live here?** Settled as yes (`MISSION.md` §5), against
   #86 §0 which proposed it upstream. The reasoning is recorded there; this
   note exists so the divergence is not discovered as a surprise.

## 7. References

- [OpenSLO](https://openslo.com/) · [OpenSLO/OpenSLO](https://github.com/OpenSLO/OpenSLO) — the `openslo.com/v2alpha` line
- [CIRISConformance#86](https://github.com/CIRISAI/CIRISConformance/issues/86) — the FSD this operationalises
- `MISSION.md` §5 — Grace's settled role
- `evidence/blocked_upstream.tsv` — the scannable half of §4

## Update cadence

On any change to the wheel's shape or a station's artifacts. A gap that closed
without its row moving is exactly the drift this document exists to catch.
