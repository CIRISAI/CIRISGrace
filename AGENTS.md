# Repository Guidelines

## Project Structure & Module Organization
- `grace/gate.py` — the gate framework: `Result`, `Context`, the registry, tiers, the report envelope. Nothing repo-specific goes here.
- `grace/upstream.py` — materialises a sibling's `origin/main`. The only place that fetches another repo.
- `grace/substrate.py` — substrate gates (`tier=SUBSTRATE`). One function per gate, registered with `@gate(id, question)`.
- `grace/ecosystem.py` — ecosystem gates (`tier=ECOSYSTEM`). **`public_repos` is the single chokepoint** through which a repository becomes visible; keep it that way.
- `grace/state.py` — previous-run load, deltas, alert derivation.
- `grace/flywheel.py` — the lifecycle stations of `FSD/OPERATIONS.md` §1, and which gate measures which. The only place that mapping lives.
- `grace/dashboard.py` — the published page. Self-contained HTML; no scripts, fonts, or CDN.
- `grace/__main__.py` — the CLI. Adding a gate must not require touching it.
- `FSD/OPERATIONS.md` — the flywheel, the OpenSLO v2 mapping, and the gap ledger.
- `matrices/` — where `head.yaml` will live once the daily recompute exists. The floor stays in CIRISConformance.
- `state/` — committed run history. Written only by CI, only in this repo.
- `evidence/` — the evidence registry (see `evidence/README.md`).

## Build, Test, and Development Commands
- Run in place: `python -m grace` (no install needed, no dependencies).
- List gates: `python -m grace gates`. Single gate: `python -m grace run <id>`.
- Machine output: `python -m grace --json out.json`; assert on `passed_all_gates`.
- No network: `python -m grace --offline` — network gates report `error`, never `pass`.

## Coding Style & Naming Conventions
- Python 3.10+, four-space indents, `snake_case` functions, type hints on public surfaces.
- **Stdlib only.** A daily driver that cannot run because a dependency failed to resolve is worse than no daily driver. `urllib`, `subprocess`, `json`, `re`, `pathlib` cover everything here. Matches the localization guard's constraint in the client repos.
- Gate ids are `kebab-case` and name the subject, not the verb (`floor-currency`, not `check-floor`).
- Every gate's `question` is a plain-language question ending in `?`. If it cannot be phrased as one question, it is two gates.

## Gate Rules
- **Four statuses, no partial credit**: `pass`, `fail`, `unimplemented`, `error`. `unimplemented` is not a pass and never counts toward `passed_all_gates`.
- **A gate that cannot ask its question says `unimplemented` or `error`.** Never `pass`.
- **A parser that finds nothing where the thing plainly exists must fail loudly.** A false negative is worse than no gate; see `nav-gate-registry` in CIRISClient for the pattern.
- **Gates are pure reads.** Nothing here mutates a sibling repo, opens a PR, or bumps a pin without a human. Grace's entire write surface is `state/`, `matrices/head.yaml`, and the published page — all in this repo.
- **Read `origin/main`, not the working tree**, when asking about another repo's shipped state. A checkout can be on a branch or months stale. Use `grace/upstream.py`; do not fetch a sibling any other way.
- **No gate may require a checkout.** A local sibling is an optimisation. Every gate must answer from the network alone, or the daily driver cannot run on a runner — and then the answer is only available at one desk, which is the problem this repo exists to solve.
- **Public repositories only, in the ecosystem tier.** The published page is public, so a private repo must be *structurally* unable to reach a gate. Derive every subject list from `ecosystem.public_repos`.
- **A station with no gate is `unknown`, never green** — `unimplemented`-is-not-a-pass, one level up. Scored against coverage, excluded from integrity.
- Docstrings carry the *why*: what failure the gate exists to catch, and what it deliberately does not check.

## Testing Guidelines
- Run every new gate against both a current and a deliberately stale tree before committing, and record what each returned in the PR.
- A gate is not done until you have seen it `fail` on real input. A gate only ever observed passing has not been tested.
- Heuristic gates must set `heuristic: true` in `detail` and say so in their summary or docstring.

## Commit & Pull Request Guidelines
- Conventional commits (`feat:`, `fix:`, `docs:`), atomic scope.
- A PR that adds or changes a gate updates `MISSION.md` §3 and the README gate table in the same commit. A gate whose question changed without `MISSION.md` changing is mission drift.
- When a gate's failure is caused by an upstream obligation, add a row to `evidence/blocked_upstream.tsv` with a scannable predicate rather than a prose note.

## Security & Configuration Tips
- No credentials in this repo. `gh` supplies GitHub auth; PyPI reads and upstream clones are anonymous, so no PAT is required.
- Never write to a sibling checkout. Grace reports; humans and CI act.
- Report files may name internal issue numbers and versions — treat `--json` output as internal unless reviewed. **The published page is the reviewed exception**, and it is safe only because its ecosystem tier is filtered to public non-archived repositories at a single chokepoint. Check that property first when adding a gate.
