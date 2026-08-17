"""Substrate readiness gates.

These answer one question: *is the substrate in a state I can cut from today?*

The set under test is CIRISConformance's coherent set. Grace does **not** parse
`matrices/current.yaml`: that file's shape belongs to Conformance and has
already changed once underneath us. It is read through the contract Conformance
publishes for downstream readers:

    python tools/install_pins.py --print-set   →  ciris-coherent-set/v1

The family ships on two channels — PyPI wheels, and git tags built from source
for members whose desktop wheels stopped publishing. `channel` is the
load-bearing field: a `pypi` member's currency question is answered by PyPI, a
`git` member's by the integrator's own `Cargo.toml`. Asking PyPI about a git
member returns an answer, and that answer is abandoned and majors behind.

See CIRISGrace#1 for the failure this module is written against: the previous
regex reader matched the PyPI half of the two-channel matrix and reported
`passed_all_gates=true` over half the set.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import tempfile
from pathlib import Path

from .gate import (
    ERROR,
    FAIL,
    PASS,
    UNIMPLEMENTED,
    Context,
    Result,
    gate,
    have,
    parse_version,
    pypi,
    run,
)

CONFORMANCE = "CIRISConformance"
SET_SCHEMA = "ciris-coherent-set/v1"
INTEGRATOR = "CIRISServer"


# ── reading the published set ────────────────────────────────────────────


def read_set(ctx: Context) -> tuple[dict, str]:
    """Return (coherent_set, source).

    Runs Conformance's own emitter against `origin/main` — never the working
    tree, which can sit on a branch or be months stale.
    """
    repo = ctx.repo(CONFORMANCE)
    if (repo / ".git").exists():
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run(["git", "archive", "--format=tar", "-o", f"{tmp}/m.tar", "origin/main"], cwd=repo)
            if rc == 0:
                run(["tar", "-xf", f"{tmp}/m.tar", "-C", tmp])
                rc, out = run(["python3", f"{tmp}/tools/install_pins.py", "--print-set",
                               "--matrix", f"{tmp}/matrices/current.yaml"], timeout=120)
                if rc == 0:
                    return _validate(json.loads(out)), f"{repo}@origin/main"

    if ctx.offline:
        raise RuntimeError(f"no usable {CONFORMANCE} checkout and --offline was given")

    with tempfile.TemporaryDirectory() as tmp:
        for rel in ("tools/install_pins.py", "matrices/current.yaml"):
            rc, out = run(["gh", "api", f"repos/CIRISAI/{CONFORMANCE}/contents/{rel}", "--jq", ".content"])
            if rc != 0:
                raise RuntimeError(f"gh api {rel} failed: {out}")
            dest = Path(tmp) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(out))
        rc, out = run(["python3", f"{tmp}/tools/install_pins.py", "--print-set",
                       "--matrix", f"{tmp}/matrices/current.yaml"], timeout=120)
        if rc != 0:
            raise RuntimeError(f"--print-set failed: {out}")
        return _validate(json.loads(out)), f"github:CIRISAI/{CONFORMANCE}@main"


def _validate(doc: dict) -> dict:
    """Refuse a set we do not fully understand.

    Silently dropping a member we cannot classify is the exact bug this module
    exists to not repeat, so an unknown shape raises instead of shrinking the
    set. The next channel the family invents fails loudly here.
    """
    schema = doc.get("schema")
    if schema != SET_SCHEMA:
        raise RuntimeError(f"unexpected schema {schema!r}, expected {SET_SCHEMA!r}")
    members = doc.get("members") or {}
    if not members:
        raise RuntimeError("coherent set carries no members")
    unclassified = [n for n, m in members.items() if m.get("channel") not in ("pypi", "git")]
    if unclassified:
        raise RuntimeError(
            "member(s) with no known channel: " + ", ".join(sorted(unclassified))
        )
    return doc


def integrator_tags(ctx: Context) -> dict[str, str]:
    """Tags the integrator pins, read from CIRISServer main's Cargo.toml.

    This is the currency source a git-channel member declares. A member ahead
    of the integrator is not yet in the coherent set, so the integrator — not
    the member's own repo head — is the reference.
    """
    repo = ctx.repo(INTEGRATOR)
    text = ""
    if (repo / ".git").exists():
        rc, out = run(["git", "show", "origin/main:Cargo.toml"], cwd=repo)
        if rc == 0:
            text = out
    if not text:
        if ctx.offline:
            raise RuntimeError("no CIRISServer checkout and --offline was given")
        rc, out = run(["gh", "api", f"repos/CIRISAI/{INTEGRATOR}/contents/Cargo.toml", "--jq", ".content"])
        if rc != 0:
            raise RuntimeError(f"gh api Cargo.toml failed: {out}")
        text = base64.b64decode(out).decode()
    # Anchor on the repo path, not a bare name: the URL carries the ORG first
    # (`github.com/CIRISAI/CIRISPersist`), so a `CIRIS[A-Za-z]+` alternation
    # matches `CIRISAI` and walks off with the tag attached to the wrong key.
    pairs = re.findall(
        r'github\.com/CIRISAI/(CIRIS[A-Za-z]+)[^\n]*?\btag\s*=\s*"(v[0-9][^"]*)"', text
    )
    if not pairs:
        raise RuntimeError(
            "no `github.com/CIRISAI/<repo> … tag = \"vX\"` dependency lines found — "
            "the integrator's manifest shape changed"
        )
    return dict(pairs)


def repo_name(member: dict) -> str:
    return (member.get("repo") or "").rstrip("/").rsplit("/", 1)[-1]


# ── gates ────────────────────────────────────────────────────────────────


@gate("set-members-available", "Is every member of the coherent set actually fetchable?")
def set_members_available(ctx: Context) -> Result:
    """PyPI members must still be indexed; git members must still resolve.

    Named for the set, not for `evidence/floor_pins.txt` — the two are allowed
    to differ, and this gate is about the matrix.

    The PyPI half is the retention-rot check: ciris-server's index retains only
    a handful of releases, so a pin that installed last month can stop
    installing with nothing in any repo changing. The git half is the same
    question for a tag: does the recorded sha still exist upstream. Conformance
    ships `install_pins.py --verify-refs` for the whole-set version of this.
    """
    if ctx.offline:
        return Result("set-members-available", ERROR, "offline")
    doc, source = read_set(ctx)
    members = doc["members"]
    bad, ok = [], {}
    for name, m in sorted(members.items()):
        if m["channel"] == "pypi":
            want = m["version"]
            try:
                releases = pypi(name)["releases"]
            except Exception as exc:
                return Result("set-members-available", ERROR, f"{name}: {exc}")
            if want not in releases or not releases[want]:
                bad.append(f"{name}=={want} (evicted from the index)")
            else:
                ok[name] = f"pypi {want}"
        else:
            sha, tag, url = m.get("sha"), m.get("tag"), m.get("repo")
            rc, out = run(["git", "ls-remote", url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"], timeout=60)
            if rc != 0:
                return Result("set-members-available", ERROR, f"{name}: ls-remote failed: {out}")
            seen = {line.split()[0] for line in out.splitlines() if line.strip()}
            if not seen:
                bad.append(f"{name} {tag} (tag missing upstream)")
            elif sha and sha not in seen:
                bad.append(f"{name} {tag} moved off {sha[:8]}")
            else:
                ok[name] = f"git {tag}"
    if bad:
        return Result(
            "set-members-available",
            FAIL,
            f"{len(bad)} of {len(members)} member(s) unfetchable: " + "; ".join(bad),
            {"unfetchable": bad, "ok": ok, "source": source},
        )
    return Result(
        "set-members-available",
        PASS,
        f"all {len(members)} members fetchable ({sum(1 for m in members.values() if m['channel'] == 'pypi')} pypi, "
        f"{sum(1 for m in members.values() if m['channel'] == 'git')} git)",
        {"members": ok, "source": source},
    )


@gate("set-currency", "How far is the coherent set behind its own currency sources?")
def set_currency(ctx: Context) -> Result:
    """Per channel, because the question differs.

    `pypi` → the newest published release. `git` → the tag the integrator pins
    in CIRISServer main's Cargo.toml. Asking PyPI about a git member is the
    subtle half of CIRISGrace#1: persist and edge still have abandoned PyPI
    releases many majors behind their git tags, so the check would report the
    set going *backwards* with total confidence.
    """
    if ctx.offline:
        return Result("set-currency", ERROR, "offline")
    doc, source = read_set(ctx)
    members = doc["members"]
    try:
        tags = integrator_tags(ctx)
    except Exception as exc:
        return Result("set-currency", ERROR, f"integrator Cargo.toml: {exc}")

    behind, current, unknown = {}, {}, []
    for name, m in sorted(members.items()):
        if m["channel"] == "pypi":
            want = m["version"]
            try:
                latest = pypi(name)["info"]["version"]
            except Exception as exc:
                return Result("set-currency", ERROR, f"{name}: {exc}")
            if parse_version(latest) > parse_version(want):
                behind[name] = {"pinned": want, "latest": latest, "source": "pypi"}
            else:
                current[name] = f"pypi {want}"
        else:
            pinned, rn = m.get("tag"), repo_name(m)
            integrator = tags.get(rn)
            if not integrator:
                unknown.append(f"{name}: no tag for {rn} in the integrator's Cargo.toml")
            elif integrator != pinned:
                behind[name] = {"pinned": pinned, "latest": integrator, "source": f"{INTEGRATOR}/Cargo.toml"}
            else:
                current[name] = f"git {pinned}"

    if unknown:
        # Cannot answer ≠ answered yes.
        return Result("set-currency", FAIL, "; ".join(unknown), {"current": current, "behind": behind})
    if behind:
        detail = ", ".join(f"{n} {d['pinned']}→{d['latest']}" for n, d in sorted(behind.items()))
        return Result(
            "set-currency",
            FAIL,
            f"{len(behind)} of {len(members)} members behind: {detail}",
            {"behind": behind, "current": current, "source": source},
        )
    return Result(
        "set-currency",
        PASS,
        f"all {len(members)} members at their currency source",
        {"members": current, "source": source},
    )


@gate("coherent-resolution", "Does the set install together?")
def coherent_resolution(ctx: Context) -> Result:
    """A pip dry-run over the set's own `install_args`.

    Uses the published args rather than rebuilding `==` specs, so a git member
    is installed from its pinned sha the way CI installs it.
    """
    if ctx.offline:
        return Result("coherent-resolution", ERROR, "offline")
    doc, _ = read_set(ctx)
    args = doc.get("install_args") or []
    if not args:
        return Result("coherent-resolution", ERROR, "set carries no install_args")
    with tempfile.TemporaryDirectory() as tmp:
        rc, out = run(
            [sys.executable, "-m", "pip", "install", "--dry-run", "--quiet", "--target", tmp, *args],
            timeout=900,
        )
    if rc != 0:
        # "the resolver said no" and "the resolver never ran" are different
        # answers, and only the first one is a finding. rc 127/124 are the
        # runner's own not-found/timeout codes; a missing pip module is the
        # same class.
        broke = rc in (124, 127) or "No module named pip" in out
        return Result(
            "coherent-resolution",
            ERROR if broke else FAIL,
            "could not run the resolver" if broke else "set does not resolve",
            {"install_args": args, "pip": "\n".join(out.splitlines()[-8:]), "rc": rc},
        )
    return Result("coherent-resolution", PASS, f"{len(args)} members resolve together", {"install_args": args})


@gate("conformance-green", "Did the conformance suite last pass on main?")
def conformance_green(ctx: Context) -> Result:
    if ctx.offline:
        return Result("conformance-green", ERROR, "offline")
    if not have("gh"):
        return Result("conformance-green", ERROR, "gh not installed")
    rc, out = run(
        [
            "gh", "run", "list", "--repo", f"CIRISAI/{CONFORMANCE}",
            "--workflow", "conformance.yml", "--branch", "main", "--limit", "1",
            "--json", "status,conclusion,displayTitle,createdAt,url",
        ]
    )
    if rc != 0:
        return Result("conformance-green", ERROR, f"gh run list failed: {out}")
    runs = json.loads(out or "[]")
    if not runs:
        return Result("conformance-green", ERROR, "no runs found")
    r = runs[0]
    when = r.get("createdAt", "")[:10]
    # A run still going has no conclusion. That is not a failure, but it is
    # also not green — reporting it as `fail` with an empty conclusion read as
    # "the suite broke", which is a different and alarming claim.
    if r.get("status") != "completed":
        return Result(
            "conformance-green",
            FAIL,
            f"last main run is {r.get('status')} — not yet green (started {when})",
            {"run": r},
        )
    conclusion = r.get("conclusion")
    return Result(
        "conformance-green",
        PASS if conclusion == "success" else FAIL,
        f"last main run: {conclusion} ({when})",
        {"run": r},
    )


@gate("xfail-ledger", "Which gates are declared-but-unmet, and are their issues named?")
def xfail_ledger(ctx: Context) -> Result:
    """Every strict xfail is a filed gap. This lists them.

    A strict xfail that starts passing turns the suite red on purpose, so the
    ledger is a backlog with an enforcement mechanism attached. The matrix also
    carries `known_failures` entries with an id, url and affected tests; both
    are counted.
    """
    repo = ctx.repo(CONFORMANCE)
    tests = repo / "tests"
    if not tests.exists():
        return Result("xfail-ledger", ERROR, f"{tests} not found (clone {CONFORMANCE})")
    # Match the DECORATOR only. `xfail(strict)` appears in plenty of docstrings
    # describing history ("was xfail(strict) on persist 12.5.0"), and counting
    # those as unlinked gaps would make this gate lie.
    marker = re.compile(r"^[ \t]*@pytest\.mark\.xfail\(", re.M)
    issue = re.compile(r"(CIRIS[A-Za-z]+)#(\d+)")
    entries = []
    for path in sorted(tests.glob("test_*.py")):
        text = path.read_text(errors="replace")
        for m in marker.finditer(text):
            body = _balanced(text, m.end() - 1)
            refs = sorted({f"{a}#{b}" for a, b in issue.findall(body)})
            entries.append(
                {"test": path.name, "strict": "strict=True" in body, "issues": refs}
            )
    unlinked = [e for e in entries if not e["issues"]]
    status = PASS if not unlinked else FAIL
    return Result(
        "xfail-ledger",
        status,
        f"{len(entries)} xfail(s), {len(unlinked)} without an issue reference",
        {"xfails": entries},
    )


def _balanced(text: str, open_paren: int) -> str:
    """Return the text inside the parens starting at `open_paren`."""
    depth = 0
    for i in range(open_paren, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1 : i]
    return text[open_paren + 1 :]


@gate("constitution-pinned", "Is the Constitution pinned to a version and a commit?")
def constitution_pinned(ctx: Context) -> Result:
    """Was half of `normative-pins`, which was declared unimplemented on the
    premise that the CC version lived in prose inside the matrix pin comments.
    Conformance promoted it to a first-class field, so this is a real check now.
    """
    if ctx.offline and not ctx.has_repo(CONFORMANCE):
        return Result("constitution-pinned", ERROR, "offline and no local checkout")
    doc, source = read_set(ctx)
    cc = doc.get("constitution") or {}
    missing = [k for k in ("version", "commit", "repo") if not cc.get(k)]
    if missing:
        return Result(
            "constitution-pinned",
            FAIL,
            "constitution pin missing: " + ", ".join(missing),
            {"constitution": cc, "source": source},
        )
    return Result(
        "constitution-pinned",
        PASS,
        f"CC {cc['version']} @ {cc['commit']}",
        {"constitution": cc, "source": source},
    )


@gate("wire-vocabulary-pinned", "Is the CEG wire-vocabulary version pinned?")
def wire_vocabulary_pinned(ctx: Context) -> Result:
    """Not implemented: still prose.

    The other half of the old `normative-pins`. `WIRE_VOCABULARY_KINDS.md` is a
    markdown file with no version anything can read, and the set carries no
    field for it. See CIRISConformance#86 §4 (class 3).
    """
    return Result(
        "wire-vocabulary-pinned",
        UNIMPLEMENTED,
        "wire vocabulary is a markdown file, not a field (Conformance#86 §4)",
    )


@gate("head-advance", "Is there a newer coherent set than the one on main?")
def head_advance(ctx: Context) -> Result:
    """Not implemented: this is the daily recompute from Conformance#86 §0.

    It should resolve the newest set, run the suite against it, and write
    `matrices/head.yaml` on green. Until it exists, `set-currency` is the
    honest half: it reports the delta without proving the newer set works.
    """
    return Result(
        "head-advance",
        UNIMPLEMENTED,
        "daily head recompute not built yet (Conformance#86 §0)",
    )
