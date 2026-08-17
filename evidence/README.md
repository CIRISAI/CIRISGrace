# Evidence registry — CIRISGrace

Format and doctrine follow
[`CIRISServer/evidence/`](https://github.com/CIRISAI/CIRISServer/tree/main/evidence):
a machine-readable pointer beats a prose note, and an honest gap beats a
plausible symbol that does not hold the line.

## `blocked_upstream.tsv` — the adoption manifest

Tab-separated, one row per upstream obligation:

```
issue  repo  scan_root  glob  needle  files  lines  kind  predicate
```

Every row states a **scannable predicate**: a needle whose appearance in the
named tree means the obligation landed. Filing an issue upstream creates a
tracked obligation *there* and nothing *here*; a predicate creates one a test
can read.

`kind` ∈ `absence` (the needle is absent today, and its arrival is the fix) ·
`drift` (present, wrong count or place) · `untestable` (the repo is not in this
repo's reachable tree, so there is nothing local to scan).

Counts were measured on 2026-08-16 against CIRISConformance `origin/main`
@`3ab8b75`. Re-measure when you touch a row; a stale count is a stale claim.

## `cc_impl.tsv` — absent, deliberately

There is no `cc_impl.tsv` here, because this repo enforces no Constitution
claim. Grace reads state and reports it; it admits nothing, gates no wire
format, and carries no normative weight. Adding rows that pointed at a gate
function would be exactly the over-claim the CIRISServer evidence audit
demoted 44 rows for: a symbol that *mentions* a requirement is not a symbol
that *enforces* it.

If that changes — if a gate here ever becomes the enforcer for a claim rather
than a reporter on one — the row gets added then, and not before.

## What is not evidence

- A gate that has never been observed failing. Until you have seen it go red on
  real input, it is a hypothesis with a green tick.
- An `unimplemented` gate. It is a declared gap, which is worth publishing, but
  it proves nothing about the system.
