"""The wheel: which station each measurement belongs to.

`FSD/OPERATIONS.md` §1 describes the ecosystem as a closed loop — evidence
sharpens the norm, the norm drives the backlog, the backlog drives the
substrate, the substrate becomes product, product serves users over the mesh,
the mesh records what happened, and what happened is the next round's evidence.

This module is the only place that mapping lives. A gate belongs to exactly one
station; a station may also claim named links out of the build chain, because
`build-chain` spans two stations by construction (verify/persist/edge are the
substrate, server/agent are the product) and reporting it as one blob would
hide which half seized.

**A station with nothing measuring it is `unknown`, never green.** That is the
same rule as `unimplemented` not being a pass, applied one level up: the wheel
must be able to say "I cannot see this bearing", because three of its eight
stations are currently in exactly that state and a green ring would be a lie
about the most important one.
"""

from __future__ import annotations

from .gate import ERROR, FAIL, PASS, UNIMPLEMENTED

GREEN, AMBER, RED, UNKNOWN = "green", "amber", "red", "unknown"

# Weights encode a claim about which bearings cost most when they seize — a
# stalled substrate or an unreachable mesh stops more of the wheel than a
# stalled backlog. Provisional: FSD §4.1 says do not fix weights before there
# is burn history to calibrate against.
STATIONS = [
    {
        "id": "epistemics", "num": "①", "name": "Epistemics",
        "what": "validated claims, falsifiers, documented limits",
        "repos": ["RATCHET", "CIRISOntology"],
        "gates": [], "chain": [], "weight": 1,
        "gaps": ["E-1 registries do not compose",
                 "E-2 RATCHET findings are prose, not data",
                 "E-3 Lean proofs are in no lane Grace reads"],
    },
    {
        "id": "normative", "num": "②", "name": "Normative",
        "what": "CC — the Accord and CEG on one version line",
        "repos": ["CIRISConstitution"],
        "gates": ["constitution-pinned", "wire-vocabulary-pinned"],
        "chain": [], "weight": 2,
        "gaps": ["N-1 wire vocabulary is prose",
                 "N-2 nothing checks the CC pin against CC itself"],
    },
    {
        "id": "conformance", "num": "③", "name": "Conformance",
        "what": "the floor, the gates, and what passing means",
        "repos": ["CIRISConformance"],
        "gates": ["conformance-green", "coherent-resolution", "xfail-ledger",
                  "head-advance"],
        "chain": [], "weight": 3,
        "gaps": ["C-1 the floor is hand-bumped, so it rots",
                 "C-2 no client tier"],
    },
    {
        "id": "backlog", "num": "④", "name": "Backlog",
        "what": "declared, unmet, and linked to an issue",
        "repos": ["CIRISConformance", "CIRISGrace"],
        "gates": ["blocked-upstream", "estate-backlog"],
        "chain": [], "weight": 1,
        "gaps": ["B-1 one estate backlog has stalled"],
    },
    {
        "id": "substrate", "num": "⑤", "name": "Substrate",
        "what": "verify → persist → edge, as one coherent set",
        "repos": ["CIRISVerify", "CIRISPersist", "CIRISEdge"],
        "gates": ["set-members-available", "set-currency"],
        "chain": ["CIRISVerify", "CIRISPersist", "CIRISEdge"], "weight": 3,
        "gaps": [],
    },
    {
        "id": "product", "num": "⑥", "name": "Product",
        "what": "server, agent, client — what users actually get",
        "repos": ["CIRISServer", "CIRISAgent", "CIRISClient"],
        "gates": [], "chain": ["CIRISServer", "CIRISAgent"], "weight": 3,
        "gaps": ["CIRISClient has zero workflows — the least gated link"],
    },
    {
        "id": "mesh", "num": "⑦", "name": "Mesh & commerce",
        "what": "running nodes, status, billing, proxy, depot",
        "repos": ["CIRISStatus", "CIRISBilling", "CIRISProxy", "CIRISDepot"],
        "gates": [], "chain": [], "weight": 3,
        "gaps": ["M-1 mesh health is unobservable from here",
                 "M-2 the distribution path is a spec with no implementation",
                 "M-3 commerce has no readiness surface"],
    },
    {
        "id": "traces", "num": "⑧", "name": "Traces",
        "what": "the mesh's own corpus — signed CEG objects, hash-chained",
        "repos": ["CIRISServer"],
        "gates": [], "chain": [], "weight": 2,
        "gaps": ["T-1 the return arc to epistemics is not wired"],
    },
]

_RAG_FROM_STATUS = {PASS: GREEN, FAIL: RED, ERROR: RED, UNIMPLEMENTED: AMBER}
_RAG_FROM_LINK = {"green": GREEN, "red": RED, "in-flight": AMBER}
_WORST = {UNKNOWN: 0, GREEN: 1, AMBER: 2, RED: 3}

# What a station is worth to the wheel when it is not fully green. Amber is
# half credit; unknown is none — an unmeasured bearing cannot be assumed sound.
_CREDIT = {GREEN: 1.0, AMBER: 0.5, RED: 0.0, UNKNOWN: 0.0}


def assess(rep: dict) -> list[dict]:
    """Score every station against a report. → stations with rag + evidence."""
    by_id = {g["gate"]: g for g in rep.get("gates", [])}
    chain = {
        l["repo"]: l
        for l in by_id.get("build-chain", {}).get("detail", {}).get("chain", [])
    }

    out = []
    for st in STATIONS:
        evidence, rags = [], []
        for gid in st["gates"]:
            g = by_id.get(gid)
            if not g:
                continue
            rag = _RAG_FROM_STATUS[g["status"]]
            rags.append(rag)
            evidence.append({
                "kind": "gate", "id": gid, "rag": rag, "status": g["status"],
                "question": g.get("detail", {}).get("question", ""),
                "summary": g["summary"],
            })
        for repo in st["chain"]:
            link = chain.get(repo)
            if not link:
                continue
            rag = _RAG_FROM_LINK.get(link["state"], UNKNOWN)
            rags.append(rag)
            note = (", ".join(link["failing"]) if link["failing"]
                    else (f"{len(link['running'])} workflow(s) running"
                          if link["running"] else "all workflows green"))
            evidence.append({
                "kind": "link", "id": repo, "rag": rag, "status": link["state"],
                "question": f"Is {repo}'s main branch green?",
                "summary": f"{note} · {link['head_sha']} · {link['head_at']}",
            })

        rag = max(rags, key=lambda r: _WORST[r]) if rags else UNKNOWN
        out.append({**st, "rag": rag, "evidence": evidence,
                    "measured": len(evidence)})
    return out


def integrity(stations: list[dict]) -> dict:
    """Two figures, deliberately not multiplied together.

    **Coverage** — how much of the wheel can be seen at all, by weight.
    **Integrity** — of the part that can be seen, how much is sound.

    Folding them into one number would answer neither question. A single figure
    dragged down by three unmeasured stations reads as "the ecosystem is
    broken" when the truth is "we cannot see a third of it", and those two
    findings have completely different actions attached: one is a repair, the
    other is an instrument. Keeping them apart is the same discipline as
    `unimplemented` not being a pass — an unasked question gets its own state
    rather than being averaged into a verdict.

    Provisional by construction: the weights are a starting position, and the
    real number should come from OpenSLO burn history once there is any
    (FSD §4.1).
    """
    total = sum(s["weight"] for s in stations) or 1
    seen = [s for s in stations if s["rag"] != UNKNOWN]
    seen_w = sum(s["weight"] for s in seen)
    earned = sum(s["weight"] * _CREDIT[s["rag"]] for s in seen)
    return {
        "pct": round(100 * earned / seen_w) if seen_w else 0,
        "coverage": round(100 * seen_w / total),
        "measured": len(seen),
        "stations": len(stations),
        "unmeasured": sum(1 for s in stations if s["rag"] == UNKNOWN),
        "red": sum(1 for s in stations if s["rag"] == RED),
        "amber": sum(1 for s in stations if s["rag"] == AMBER),
        "green": sum(1 for s in stations if s["rag"] == GREEN),
        "provisional": True,
    }
