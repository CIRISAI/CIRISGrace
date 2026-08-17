# state — the run history

Written by `.github/workflows/readiness.yml`, in this repo and nowhere else.

- `latest.json` — the last full `ciris-readiness/v1` report.
- `history.jsonl` — one compact line per run: statuses and metrics only.

This is the data behind "since". A board is a snapshot, and a snapshot cannot
tell **red since April** from **red since breakfast** — which is the only
distinction that decides whether you act now. It is committed rather than kept
in a CI cache because a cache evicts, and the moment the history matters is the
moment someone asks how long something has been broken.

`history.jsonl` is append-only and deliberately small: enough to compute a
streak and a trend line, not enough to reconstruct every report. If you need
the whole report from a given day, it is the build artifact for that run.

An ad-hoc local run does **not** land here. `--state DIR` reads the previous
run so you get deltas; `--save-state` is a separate flag, so someone
experimenting on a laptop cannot enter the committed record by accident.
