# Repository Guidelines

## Project Structure & Module Organization
- `grace/gate.py` — the gate framework: `Result`, `Context`, the registry, the report envelope. Nothing repo-specific goes here.
- `grace/substrate.py` — the substrate readiness gates. One function per gate, registered with `@gate(id, question)`.
- `grace/__main__.py` — the CLI. Adding a gate must not require touching it.
- `matrices/` — where `head.yaml` will live once the daily recompute exists. The floor stays in CIRISConformance.
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
- **Gates are pure reads.** Nothing here mutates a sibling repo, opens a PR, or bumps a pin without a human.
- **Read `origin/main`, not the working tree**, when asking about another repo's shipped state. A checkout can be on a branch or months stale.
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
- No credentials in this repo. `gh` supplies GitHub auth; PyPI reads are anonymous.
- Never write to a sibling checkout. Grace reports; humans and CI act.
- Report files may name internal issue numbers and versions — treat `--json` output as internal unless reviewed.
