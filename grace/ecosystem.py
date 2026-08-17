"""Ecosystem gates — what is rotting across the estate while nobody looks.

The substrate tier answers *can I cut from the coherent set today*. This tier
answers the other question: where a change has got to in the build chain, which
backlogs have stopped moving, and whether the obligations this repo declared as
blocked are still actually blocked.

**Public repositories only, structurally.** `public_repos` filters on the org
API's own `visibility` field and every gate here derives its subject list from
that one function, so a private repo cannot reach a gate — and therefore cannot
reach the published dashboard — by any path. That is what makes it safe to
publish this tier's output to a public page, and it is the property to preserve
if a gate is ever added here.

**CI state comes from GitHub, not from a status page.** ciris.ai's status
surface serves a `/api/v1/ci` projection of exactly this data, but a projection
is a second thing that can be down — it was 502 the day this module was
written — and a readiness tool that goes blind when a dashboard goes down has
the dependency backwards.
"""

from __future__ import annotations

import fnmatch
import json
import re
import time
from pathlib import Path

from . import upstream
from .gate import (
    ECOSYSTEM,
    ERROR,
    FAIL,
    PASS,
    Context,
    Result,
    gate,
    run,
)

ORG = "CIRISAI"

# The centipede, in propagation order. The order is the content: a change
# enters at verify and has to survive every downstream link, so "where is it"
# is only meaningful as a position in this list.
CHAIN = ["CIRISVerify", "CIRISPersist", "CIRISEdge", "CIRISServer", "CIRISAgent"]

# A conclusion that means a human should look. `cancelled` and `skipped` are
# neither health nor rot — they are someone pressing stop, and counting them as
# failures would make the chain permanently red for no reason.
BAD = {"failure", "timed_out", "startup_failure"}
PENDING = {"in_progress", "queued", "requested", "waiting", "pending"}

# How long an open backlog may sit untouched before it stops being a backlog
# and starts being a pile. Long enough that normal work never trips it.
STALE_DAYS = 90


# GitHub's own transient failures, which are not findings about this ecosystem.
_TRANSIENT = ("HTTP 502", "HTTP 503", "HTTP 504", "No server is currently available",
              "timed out", "connection reset", "EOF occurred", "TLS handshake")


def gh_json(args: list[str], timeout: int = 60, attempts: int = 3):
    """`gh` returning parsed JSON. Raises with gh's own message on failure.

    Retries transient upstream failures. An unattended daily job that goes red
    because GitHub returned a 503 for one second teaches the reader that red
    does not mean anything — and this actually happened on the first two-run
    test. A *persistent* outage still surfaces, as `error`, never as `pass`;
    the retry buys accuracy, not optimism.
    """
    last = ""
    for attempt in range(attempts):
        rc, out = run(["gh", *args], timeout=timeout)
        if rc == 0:
            return json.loads(out or "null")
        last = out.splitlines()[-1] if out else str(rc)
        if not any(t.lower() in out.lower() for t in _TRANSIENT):
            break  # a real error — fail now rather than three times slower
        if attempt < attempts - 1:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"gh {' '.join(args[:2])}: {last}")


_ESTATE_QUERY = """
{ organization(login:"%s") {
    repositories(first:100, privacy:PUBLIC, isArchived:false,
                 orderBy:{field:PUSHED_AT, direction:DESC}) {
      totalCount
      nodes {
        name pushedAt
        issues(states:OPEN) { totalCount }
        pullRequests(states:OPEN) { totalCount }
        newest: issues(states:OPEN, first:1,
                       orderBy:{field:UPDATED_AT, direction:DESC}) {
          nodes { updatedAt number }
        }
      } } } }
""" % ORG


def public_repos(ctx: Context) -> list[dict]:
    """Every public, non-archived repo in the org, newest push first.

    One GraphQL round trip for the whole estate — the REST equivalent is a call
    per repo, and at ~48 repos that is the difference between a gate and a
    coffee break. This is the single chokepoint through which a repository
    becomes visible to this tier; keep it that way.
    """
    if "estate" in ctx.cache:
        return ctx.cache["estate"]
    doc = gh_json(["api", "graphql", "-f", f"query={_ESTATE_QUERY}"], timeout=90)
    repos_node = doc["data"]["organization"]["repositories"]
    nodes = repos_node["nodes"]
    if repos_node["totalCount"] > len(nodes):
        # Refuse to silently report on a subset. Paging past 100 is a one-line
        # change; reporting 100 of 130 as if it were the estate is the coverage
        # shrink this codebase exists to refuse.
        raise RuntimeError(
            f"org has {repos_node['totalCount']} public repos but the query returned "
            f"{len(nodes)} — the estate outgrew the page size"
        )
    ctx.cache["estate"] = nodes
    return nodes


# ── gates ────────────────────────────────────────────────────────────────


@gate(
    "build-chain",
    "Where in verify→persist→edge→server→agent has a change stopped?",
    tier=ECOSYSTEM,
)
def build_chain(ctx: Context) -> Result:
    """The centipede's health, link by link, read from GitHub Actions itself.

    For each link: the newest run of every distinct workflow on `main`. A link
    is red if any of its workflows last concluded badly, in flight if any is
    still running — which is the literal answer to "where are changes
    populating", because an in-flight link is a change that has arrived and not
    yet been judged.

    Deliberately not checked: whether the downstream link has *adopted* the
    upstream's new version. That is `set-currency`'s question and it is
    answered against the coherent set, not against a branch.
    """
    if ctx.offline:
        return Result("build-chain", ERROR, "offline")

    links, red, flight = [], [], []
    for repo in CHAIN:
        try:
            runs = gh_json(
                ["api", f"repos/{ORG}/{repo}/actions/runs?branch=main&per_page=40",
                 "--jq", "[.workflow_runs[] | {name, status, conclusion, "
                         "sha: .head_sha, at: .created_at, url: .html_url}]"],
            )
        except Exception as exc:
            return Result("build-chain", ERROR, f"{repo}: {exc}")

        # Newest run per workflow — the list is newest-first, so first wins.
        newest: dict[str, dict] = {}
        for r in runs or []:
            newest.setdefault(r["name"], r)
        if not newest:
            return Result("build-chain", ERROR, f"{repo}: no workflow runs on main")

        bad = sorted(n for n, r in newest.items() if r["conclusion"] in BAD)
        busy = sorted(n for n, r in newest.items() if r["status"] in PENDING)
        head = max(newest.values(), key=lambda r: r["at"])
        state = "red" if bad else ("in-flight" if busy else "green")
        links.append({
            "repo": repo, "state": state, "failing": bad, "running": busy,
            "head_sha": head["sha"][:8], "head_at": head["at"][:10],
            "workflows": len(newest),
            "url": next((newest[n]["url"] for n in bad), None),
        })
        if bad:
            red.append(f"{repo}({', '.join(bad)})")
        if busy:
            flight.append(f"{repo}({len(busy)})")

    detail = {"chain": links, "metrics": {"links_red": len(red)}}
    if red:
        return Result(
            "build-chain", FAIL,
            f"chain broken at {len(red)}/{len(CHAIN)}: " + "; ".join(red)
            + (f" · in flight: {', '.join(flight)}" if flight else ""),
            detail,
        )
    if flight:
        return Result(
            "build-chain", PASS,
            f"all {len(CHAIN)} links green · changes in flight at {', '.join(flight)}",
            detail,
        )
    return Result("build-chain", PASS, f"all {len(CHAIN)} links green and settled", detail)


@gate(
    "estate-backlog",
    "Does every public repo with an open backlog still show movement on it?",
    tier=ECOSYSTEM,
)
def estate_backlog(ctx: Context) -> Result:
    """Rot detection, not a bug count.

    A growing backlog is what active development looks like, so gating on the
    count would be red every day it was working and teach everyone to skip the
    line. What is *not* normal is a repo with open issues that nobody has
    touched in a quarter: that is a backlog that has stopped being a plan.

    The counts ride along in `metrics` so the delta machinery and the dashboard
    can show the trend without this gate having to pretend the trend is a
    verdict.
    """
    if ctx.offline:
        return Result("estate-backlog", ERROR, "offline")
    try:
        repos = public_repos(ctx)
    except Exception as exc:
        return Result("estate-backlog", ERROR, str(exc))

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    stale, per_repo, issues, prs = [], {}, 0, 0
    for r in repos:
        n = r["issues"]["totalCount"]
        p = r["pullRequests"]["totalCount"]
        issues += n
        prs += p
        if not n:
            continue
        newest = r["newest"]["nodes"]
        touched = newest[0]["updatedAt"] if newest else r["pushedAt"]
        age = (now - datetime.fromisoformat(touched.replace("Z", "+00:00"))).days
        per_repo[r["name"]] = {"open_issues": n, "open_prs": p, "idle_days": age}
        if age > STALE_DAYS:
            stale.append((r["name"], n, age))

    metrics = {"open_issues": issues, "open_prs": prs, "repos": len(repos),
               "stale_backlogs": len(stale)}
    detail = {"repos": per_repo, "metrics": metrics, "stale_days": STALE_DAYS}

    if stale:
        stale.sort(key=lambda t: -t[2])
        named = ", ".join(f"{n} ({c} open, {d}d idle)" for n, c, d in stale[:5])
        more = f" +{len(stale) - 5} more" if len(stale) > 5 else ""
        return Result(
            "estate-backlog", FAIL,
            f"{len(stale)} of {len(repos)} repos have a backlog untouched >{STALE_DAYS}d: "
            f"{named}{more}",
            detail,
        )
    return Result(
        "estate-backlog", PASS,
        f"{issues} open issues, {prs} open PRs across {len(repos)} public repos; "
        f"every backlog touched within {STALE_DAYS}d",
        detail,
    )


@gate(
    "blocked-upstream",
    "Does the blocked-upstream ledger still match reality?",
    tier=ECOSYSTEM,
)
def blocked_upstream(ctx: Context) -> Result:
    """Re-run every scannable predicate in `evidence/blocked_upstream.tsv`.

    Each row names a needle whose appearance upstream means the obligation
    landed. This gate scans for it and compares against the recorded count, so
    the ledger is a claim that gets checked rather than a note that gets stale.

    **A landed obligation turns this gate RED, and that is the point.** It is
    the same state machine as a strict xfail: declared-and-absent is green and
    honest; the moment the thing exists, the build goes red until someone
    acknowledges it by implementing the gate it was blocking and retiring the
    row. There is no state in which the fix has shipped upstream and nothing
    here has noticed.
    """
    ledger = Path(__file__).resolve().parent.parent / "evidence" / "blocked_upstream.tsv"
    if not ledger.exists():
        return Result("blocked-upstream", ERROR, f"{ledger} not found")

    rows = _read_ledger(ledger)
    if not rows:
        return Result("blocked-upstream", ERROR, f"no data rows parsed from {ledger.name}")

    changed, outstanding, skipped = [], [], []
    for row in rows:
        if row["kind"] == "untestable":
            skipped.append(row)
            continue
        try:
            root, source = upstream.tree(ctx, row["repo"])
        except Exception as exc:
            return Result("blocked-upstream", ERROR, f"{row['repo']}: {exc}")
        files, lines = _scan(root / row["scan_root"], row["glob"], row["needle"])
        row["observed"] = {"files": files, "lines": lines, "source": source}
        want_f, want_l = int(row["files"]), int(row["lines"])
        if (files, lines) != (want_f, want_l):
            row["was"] = {"files": want_f, "lines": want_l}
            changed.append(row)
        else:
            outstanding.append(row)

    metrics = {"obligations": len(rows), "outstanding": len(outstanding),
               "changed": len(changed)}
    detail = {"changed": changed, "outstanding": [_brief(r) for r in outstanding],
              "untestable": [_brief(r) for r in skipped], "metrics": metrics}

    if changed:
        named = "; ".join(
            f"{r['repo']}#{r['issue']} `{r['needle']}` "
            f"{r['was']['files']}→{r['observed']['files']} files"
            for r in changed
        )
        landed = [r for r in changed if r["was"]["files"] == 0 and r["observed"]["files"] > 0]
        lede = (
            f"{len(landed)} declared gap(s) HAVE LANDED upstream — implement and retire the row"
            if landed else f"{len(changed)} predicate(s) drifted from the recorded count"
        )
        return Result("blocked-upstream", FAIL, f"{lede}: {named}", detail)

    return Result(
        "blocked-upstream", PASS,
        f"{len(outstanding)} obligation(s) still outstanding, every predicate still "
        f"absent as recorded" + (f" ({len(skipped)} untestable)" if skipped else ""),
        detail,
    )


def _read_ledger(path: Path) -> list[dict]:
    cols = ["issue", "repo", "scan_root", "glob", "needle", "files", "lines", "kind", "predicate"]
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if parts[0] == "issue":  # the header
            continue
        if len(parts) < len(cols):
            continue
        rows.append(dict(zip(cols, parts)))
    return rows


def _scan(root: Path, glob: str, needle: str) -> tuple[int, int]:
    """Count files and lines under `root` matching `glob` that hit `needle`."""
    if not root.exists():
        return 0, 0
    pat = re.compile(needle)
    files = lines = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not fnmatch.fnmatch(path.name, glob):
            continue
        hits = sum(1 for ln in path.read_text(errors="replace").splitlines() if pat.search(ln))
        if hits:
            files += 1
            lines += hits
    return files, lines


def _brief(row: dict) -> dict:
    return {k: row[k] for k in ("issue", "repo", "needle", "kind") if k in row}
