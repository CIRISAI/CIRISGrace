"""Substrate readiness gates.

These answer one question: *is the substrate in a state I can cut from today?*

The floor is CIRISConformance's `matrices/current.yaml` — the consciously
chosen set that a release must clear. These gates compare the world against
that floor and report the delta. They do not change it.
"""

from __future__ import annotations

import base64
import json
import re
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
PIN_RE = re.compile(r'^\s{2}(ciris-[a-z-]+):\s+"([0-9][0-9A-Za-z.\-]*)"', re.M)


def read_floor(ctx: Context) -> tuple[dict[str, str], str]:
    """Return ({package: version}, source). Local checkout first, then GitHub.

    Parsed with a regex rather than a YAML library on purpose: the file is
    stdlib-parseable, and the pin comments are long enough that a YAML load
    buys nothing here.
    """
    repo = ctx.repo(CONFORMANCE)
    rel = "matrices/current.yaml"

    # Prefer origin/main over the working tree. A local checkout can sit on a
    # feature branch, or simply be months behind — reading the dirty tree is
    # how you end up confidently reporting against a floor nobody ships.
    if (repo / ".git").exists():
        rc, out = run(["git", "show", f"origin/main:{rel}"], cwd=repo)
        if rc == 0 and out:
            return dict(PIN_RE.findall(out)), f"{repo}@origin/main"

    local = repo / rel
    if local.exists():
        return dict(PIN_RE.findall(local.read_text())), f"{local} (working tree)"

    if ctx.offline:
        raise RuntimeError(f"{local} not found and --offline was given")

    rc, out = run(
        [
            "gh",
            "api",
            f"repos/CIRISAI/{CONFORMANCE}/contents/matrices/current.yaml",
            "--jq",
            ".content",
        ]
    )
    if rc != 0:
        raise RuntimeError(f"no local matrix and gh api failed: {out}")
    text = base64.b64decode(out).decode()
    return dict(PIN_RE.findall(text)), "github:CIRISAI/CIRISConformance@main"


def _balanced(text: str, open_paren: int) -> str:
    """Return the text inside the parens starting at `open_paren`.

    Naive but sufficient here: the decorator arguments are single-line or
    simply nested, and a stray paren inside a reason string only widens the
    slice, which cannot produce a false "no issue reference".
    """
    depth = 0
    for i in range(open_paren, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1 : i]
    return text[open_paren + 1 :]


@gate("floor-pins-indexed", "Is every pinned wheel still installable from PyPI?")
def floor_pins_indexed(ctx: Context) -> Result:
    """The retention-rot gate.

    ciris-server's PyPI index retains only a handful of releases, so a pin
    that was fine last month can become uninstallable without anything in
    any repo changing. That failure shows up downstream as every
    cohabitation cell failing identically at pip-install time.
    """
    if ctx.offline:
        return Result("floor-pins-indexed", ERROR, "offline")
    pins, source = read_floor(ctx)
    missing, checked = {}, {}
    for pkg, want in pins.items():
        try:
            releases = pypi(pkg)["releases"]
        except Exception as exc:
            return Result("floor-pins-indexed", ERROR, f"{pkg}: {exc}")
        # A version with no files left is indexed but not installable.
        if want not in releases or not releases[want]:
            missing[pkg] = want
        checked[pkg] = want
    if missing:
        detail = ", ".join(f"{p}=={v}" for p, v in sorted(missing.items()))
        return Result(
            "floor-pins-indexed",
            FAIL,
            f"{len(missing)} pin(s) no longer installable: {detail}",
            {"missing": missing, "source": source},
        )
    return Result(
        "floor-pins-indexed",
        PASS,
        f"{len(checked)} pins installable",
        {"pins": checked, "source": source},
    )


@gate("floor-currency", "How far is the floor behind what is published?")
def floor_currency(ctx: Context) -> Result:
    """Reports the delta, and fails only when a pin has fallen behind.

    This is the number that says whether the floor is governing the stack
    or trailing it.
    """
    if ctx.offline:
        return Result("floor-currency", ERROR, "offline")
    pins, source = read_floor(ctx)
    behind, current = {}, {}
    for pkg, want in pins.items():
        try:
            data = pypi(pkg)
        except Exception as exc:
            return Result("floor-currency", ERROR, f"{pkg}: {exc}")
        latest = data["info"]["version"]
        if parse_version(latest) > parse_version(want):
            behind[pkg] = {"pinned": want, "latest": latest}
        else:
            current[pkg] = want
    if behind:
        detail = ", ".join(
            f"{p} {d['pinned']}→{d['latest']}" for p, d in sorted(behind.items())
        )
        return Result(
            "floor-currency",
            FAIL,
            f"{len(behind)} of {len(pins)} pins behind: {detail}",
            {"behind": behind, "current": current, "source": source},
        )
    return Result("floor-currency", PASS, f"all {len(pins)} pins current", {"pins": current})


@gate("coherent-resolution", "Does the floor set resolve together?")
def coherent_resolution(ctx: Context) -> Result:
    """A pip dry-run over the whole set.

    Resolution failure is the ResolutionImpossible case that shows up when
    one member's ceiling excludes another member's floor.
    """
    if ctx.offline:
        return Result("coherent-resolution", ERROR, "offline")
    pins, _ = read_floor(ctx)
    specs = [f"{p}=={v}" for p, v in sorted(pins.items())]
    with tempfile.TemporaryDirectory() as tmp:
        rc, out = run(
            ["pip", "install", "--dry-run", "--quiet", "--target", tmp, *specs],
            timeout=300,
        )
    if rc != 0:
        tail = "\n".join(out.splitlines()[-6:])
        return Result(
            "coherent-resolution",
            FAIL,
            "set does not resolve",
            {"specs": specs, "pip": tail},
        )
    return Result("coherent-resolution", PASS, f"{len(specs)} wheels resolve together", {"specs": specs})


@gate("conformance-green", "Did the conformance suite last pass on main?")
def conformance_green(ctx: Context) -> Result:
    if ctx.offline:
        return Result("conformance-green", ERROR, "offline")
    if not have("gh"):
        return Result("conformance-green", ERROR, "gh not installed")
    rc, out = run(
        [
            "gh",
            "run",
            "list",
            "--repo",
            f"CIRISAI/{CONFORMANCE}",
            "--workflow",
            "conformance.yml",
            "--branch",
            "main",
            "--limit",
            "1",
            "--json",
            "conclusion,displayTitle,createdAt,url",
        ]
    )
    if rc != 0:
        return Result("conformance-green", ERROR, f"gh run list failed: {out}")
    runs = json.loads(out or "[]")
    if not runs:
        return Result("conformance-green", ERROR, "no runs found")
    r = runs[0]
    status = PASS if r.get("conclusion") == "success" else FAIL
    return Result(
        "conformance-green",
        status,
        f"last main run: {r.get('conclusion')} ({r.get('createdAt', '')[:10]})",
        {"run": r},
    )


@gate("xfail-ledger", "Which gates are declared-but-unmet, and are their issues open?")
def xfail_ledger(ctx: Context) -> Result:
    """Every strict xfail in the suite is a filed gap. This lists them.

    A strict xfail that starts passing turns the suite red on purpose, so
    this ledger is the backlog with an enforcement mechanism attached.
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
    summary = f"{len(entries)} xfail(s), {len(unlinked)} without an issue reference"
    status = PASS if not unlinked else FAIL
    return Result("xfail-ledger", status, summary, {"xfails": entries})


@gate("normative-pins", "Are the Constitution and wire-vocabulary versions pinned?")
def normative_pins(ctx: Context) -> Result:
    """Not implemented: there is no machine-readable source yet.

    The CC version is tracked in prose inside the matrix pin comments
    (e.g. `CC unchanged (1.0-rc2, commit 5818a75)`), and the wire vocabulary
    lives in a markdown file. Both need to become head fields before this
    gate can ask anything. See CIRISConformance#86 §4 (class 3).
    """
    return Result(
        "normative-pins",
        UNIMPLEMENTED,
        "CC + wire-vocabulary versions are prose, not fields (Conformance#86 §4)",
    )


@gate("head-advance", "Is there a newer coherent set than the floor?")
def head_advance(ctx: Context) -> Result:
    """Not implemented: this is the daily recompute from Conformance#86 §0.

    It should resolve the newest set, run the suite against it, and write
    `matrices/head.yaml` on green. Until it exists, `floor-currency` is the
    honest half of the answer: it reports the delta without proving the
    newer set actually works.
    """
    return Result(
        "head-advance",
        UNIMPLEMENTED,
        "daily head recompute not built yet (Conformance#86 §0)",
    )
