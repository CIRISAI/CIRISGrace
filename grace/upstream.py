"""Materialising a sibling repository's shipped state.

Every gate that asks about another repo asks about `origin/main`, never a
working tree: a sibling checkout is routinely parked on a feature branch, and
answering from it means answering about someone's unmerged work.

**No checkout is a prerequisite.** A local one is used when present because it
is free and works offline; absent one, an anonymous shallow clone. That is a
deliberate choice over the GitHub contents API: it needs no token, and it
fetches the whole tree in one round trip rather than one call per file, so the
same primitive can serve a gate that wants two files and a gate that wants to
scan 62 of them. `gh` is tried last, and only to cover a repo that has gone
private.

Trees are memoised on the Context. Without that, the four gates that want
CIRISConformance each pay for their own clone.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

from .gate import Context, run

ORG = "CIRISAI"


def tree(ctx: Context, repo: str) -> tuple[Path, str]:
    """Materialise `CIRISAI/<repo>` at origin/main. → (path, source)"""
    key = f"tree:{repo}"
    if key in ctx.cache:
        return ctx.cache[key]

    local = ctx.repo(repo)
    if (local / ".git").exists():
        dst = Path(scratch())
        rc, _ = run(["git", "archive", "--format=tar", "-o", f"{dst}/m.tar", "origin/main"], cwd=local)
        if rc == 0:
            rc, _ = run(["tar", "-xf", f"{dst}/m.tar", "-C", str(dst)])
            if rc == 0:
                return _memo(ctx, key, dst, f"{local}@origin/main")

    if ctx.offline:
        raise RuntimeError(f"no usable {repo} checkout and --offline was given")

    url = f"https://github.com/{ORG}/{repo}"
    dst = Path(scratch()) / repo
    rc, anon = run(["git", "clone", "--depth", "1", "--branch", "main", "-q", url, str(dst)], timeout=180)
    if rc == 0:
        return _memo(ctx, key, dst, f"{url}@main (shallow)")

    rc, out = run(["gh", "repo", "clone", f"{ORG}/{repo}", str(dst), "--", "--depth", "1", "-q"], timeout=180)
    if rc != 0:
        raise RuntimeError(f"cannot materialise {repo}@main: anonymous clone: {anon}; gh: {out}")
    return _memo(ctx, key, dst, f"gh:{ORG}/{repo}@main (shallow)")


def _memo(ctx: Context, key: str, path: Path, source: str) -> tuple[Path, str]:
    ctx.cache[key] = (path, source)
    return path, source


def scratch() -> str:
    """A temp dir that outlives the call. Gates hand each other paths into the
    materialised tree, so it cannot be a `with` block scoped to one function."""
    tmp = tempfile.mkdtemp(prefix="grace-")
    atexit.register(shutil.rmtree, tmp, True)
    return tmp
