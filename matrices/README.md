# matrices

Empty on purpose.

The **floor** — the consciously chosen set a release must clear — lives in
[CIRISConformance](https://github.com/CIRISAI/CIRISConformance)'s
`matrices/current.yaml` and stays there. Grace reads it from `origin/main`; it
does not own it and does not write it.

The **head** — the newest coherent set, recomputed daily and advanced only on a
green suite — will be written here as `head.yaml` when that job exists. It does
not exist yet. See
[CIRISConformance#86](https://github.com/CIRISAI/CIRISConformance/issues/86) §0
and `evidence/blocked_upstream.tsv`.

Until then `grace run floor-currency` reports the delta between the floor and
what is published, which is the honest half of the answer: it shows the gap
without proving the newer set actually works.
