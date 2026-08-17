"""The board as a page, for cirisai.github.io/CIRISGrace.

Read on a phone, usually before the reader has decided to care. So the order is
the order of the decisions someone actually makes: *is anything on fire* (the
alerts), *can I cut today* (the substrate verdict), *where did the chain stop*
(the pipeline), then the full board, then the trend.

Self-contained by construction — one file, no scripts, no fonts, no CDN. A
dashboard that needs the network to render is a dashboard that is down exactly
when the network is, which is one of the things it exists to report on.

**Public inputs only.** Everything here comes from the report, and the report's
ecosystem tier is filtered to public non-archived repositories at
`ecosystem.public_repos`. That single chokepoint is what makes publishing this
page compatible with the rule that report output is internal until reviewed: a
private repo is structurally unable to reach it.
"""

from __future__ import annotations

import html
import math
from datetime import datetime, timezone

from . import flywheel
from .gate import ERROR, FAIL, PASS, UNIMPLEMENTED

_DOT = {PASS: "ok", FAIL: "bad", ERROR: "bad", UNIMPLEMENTED: "gap"}
_WORD = {PASS: "pass", FAIL: "fail", ERROR: "error", UNIMPLEMENTED: "declared gap"}

_CSS = """
:root{--bg:#fbfbfa;--fg:#1a1a17;--dim:#6b6b63;--line:#e3e3dd;--card:#fff;
--ok:#1a7f4b;--bad:#c0392b;--gap:#b8860b;--accent:#2b5f8f;--okbg:#e8f5ee;--badbg:#fbeae8;--gapbg:#fdf4e0}
@media (prefers-color-scheme:dark){:root{--bg:#12120f;--fg:#e8e8e2;--dim:#93938a;
--line:#2a2a25;--card:#1a1a16;--ok:#4ade80;--bad:#f87171;--gap:#fbbf24;--accent:#7dd3fc;
--okbg:#132a1e;--badbg:#2c1614;--gapbg:#2a2211}}
*{box-sizing:border-box}
body{margin:0;padding:1.1rem;background:var(--bg);color:var(--fg);
font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif;
-webkit-text-size-adjust:100%}
main{max-width:60rem;margin:0 auto}
h1{font-size:1.15rem;margin:0;letter-spacing:-.01em}
h2{font-size:.75rem;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
margin:2rem 0 .6rem;font-weight:600}
.sub{color:var(--dim);font-size:.8rem;margin:.2rem 0 0}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.86em}
/* min() so the track can collapse below its own minimum on a narrow phone —
   a bare minmax(13rem,1fr) forces two columns and scrolls the whole page
   sideways, which then clips the flywheel's labels. */
.verdicts{display:grid;gap:.6rem;margin-top:1.1rem;
grid-template-columns:repeat(auto-fit,minmax(min(13rem,100%),1fr))}
.v{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.85rem 1rem}
.v .q{font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;color:var(--dim)}
.v .a{font-size:1.5rem;font-weight:650;margin-top:.15rem;letter-spacing:-.02em}
.v .n{color:var(--dim);font-size:.78rem}
.ok{color:var(--ok)}.bad{color:var(--bad)}.gap{color:var(--gap)}
.alert{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--dim);
border-radius:8px;padding:.7rem .9rem;margin-bottom:.5rem}
.alert.bad{border-left-color:var(--bad)}.alert.ok{border-left-color:var(--ok)}
.alert .t{font-weight:620}
.alert .d{color:var(--dim);font-size:.86rem;margin-top:.12rem}
.alert .x{font-size:.86rem;margin-top:.3rem}
.alert .x b{font-weight:600}
.chain{display:flex;flex-wrap:wrap;gap:.4rem;align-items:stretch}
.link{flex:1 1 7.5rem;background:var(--card);border:1px solid var(--line);
border-top:3px solid var(--dim);border-radius:8px;padding:.55rem .65rem;min-width:7.5rem}
.link.ok{border-top-color:var(--ok)}.link.bad{border-top-color:var(--bad)}
.link.flight{border-top-color:var(--accent)}
.link .r{font-weight:600;font-size:.82rem;word-break:break-word}
.link .s{font-size:.72rem;color:var(--dim);margin-top:.15rem}
.link .w{font-size:.7rem;color:var(--bad);margin-top:.2rem;word-break:break-word}
table{width:100%;border-collapse:collapse;margin-top:.3rem}
td,th{text-align:left;padding:.45rem .4rem;border-bottom:1px solid var(--line);
vertical-align:top;font-size:.87rem}
th{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);font-weight:600}
td.st{white-space:nowrap;font-weight:600;width:1%}
/* nowrap belongs to the gate id alone. Putting it on the cell also froze the
   question underneath it, which forced the table wider than a phone and
   scrolled the whole page sideways. */
td.g{width:1%}
td.g code{white-space:nowrap}
.tw{overflow-x:auto}
.q{color:var(--dim);font-size:.8rem}
.pill{display:inline-block;padding:.05rem .4rem;border-radius:99px;font-size:.72rem;
font-weight:600;white-space:nowrap}
.pill.ok{background:var(--okbg);color:var(--ok)}
.pill.bad{background:var(--badbg);color:var(--bad)}
.pill.gap{background:var(--gapbg);color:var(--gap)}
.trend{display:grid;gap:.6rem;
grid-template-columns:repeat(auto-fit,minmax(min(11rem,100%),1fr))}
.tr{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:.7rem .85rem}
.tr .k{font-size:.72rem;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.tr .n{font-size:1.3rem;font-weight:650;letter-spacing:-.02em}
.tr svg{display:block;margin-top:.3rem;width:100%;height:26px;overflow:visible}
footer{margin-top:2.2rem;padding-top:.9rem;border-top:1px solid var(--line);
color:var(--dim);font-size:.78rem}
a{color:var(--accent)}
.wheel{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:.5rem;margin-top:.4rem}
.wheel svg{display:block;width:100%;height:auto}
.wheel text{fill:var(--fg);font-family:inherit}
.wheel .lbl{font-size:25px;font-weight:640}
.wheel .sub{fill:var(--dim);font-size:19px}
.wheel .num{font-size:26px;font-weight:700;fill:#fff}
.wheel .ctr{font-size:62px;font-weight:700;letter-spacing:-.03em}
.wheel .ctrk{fill:var(--dim);font-size:20px;letter-spacing:.09em}
.wheel a{cursor:pointer}
.wheel a:hover .lbl{fill:var(--accent);text-decoration:underline}
.st{background:var(--card);border:1px solid var(--line);border-radius:10px;
margin-bottom:.5rem;overflow:hidden}
.st>summary{cursor:pointer;padding:.65rem .85rem;display:flex;gap:.6rem;
align-items:baseline;list-style:none}
.st>summary::-webkit-details-marker{display:none}
.st>summary::after{content:"▸";margin-left:auto;color:var(--dim);font-size:.8rem}
.st[open]>summary::after{content:"▾"}
.st .n{font-weight:650}
.st .w{color:var(--dim);font-size:.8rem}
.st .body{padding:0 .85rem .8rem;border-top:1px solid var(--line);padding-top:.6rem}
.dot{width:.62rem;height:.62rem;border-radius:99px;flex:none;display:inline-block}
.dot.green{background:var(--ok)}.dot.red{background:var(--bad)}
.dot.amber{background:var(--gap)}.dot.unknown{background:var(--dim);opacity:.45}
.ev{display:flex;gap:.5rem;padding:.32rem 0;font-size:.85rem;align-items:baseline}
.ev .k{color:var(--dim);font-size:.78rem}
.gaps{margin:.5rem 0 0;padding:.5rem .7rem;background:var(--gapbg);border-radius:7px;
font-size:.83rem}
.gaps b{font-weight:620}.gaps ul{margin:.25rem 0 0;padding-left:1.1rem}
.repos{margin-top:.5rem;font-size:.83rem}
.repos a{margin-right:.55rem;white-space:nowrap}
@media(max-width:30rem){body{padding:.8rem}.v .a{font-size:1.3rem}}
"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def dashboard(rep: dict, history: list[dict] | None = None) -> str:
    history = history or []
    gates = rep.get("gates", [])
    by_id = {g["gate"]: g for g in gates}
    p: list[str] = []

    p.append("<!doctype html><html lang=en><head><meta charset=utf-8>")
    p.append('<meta name=viewport content="width=device-width,initial-scale=1">')
    p.append(f"<title>{_e(rep['repo'])} — readiness</title>")
    p.append(f"<style>{_CSS}</style></head><body><main>")

    gen = rep.get("generated", "")
    p.append(f"<h1>{_e(rep['repo'])} — ecosystem readiness</h1>")
    p.append(f'<p class=sub>generated {_e(gen)} · {_e(_ago(gen))}</p>')

    # ── the two verdicts, kept apart on purpose ──────────────────────────
    p.append("<div class=verdicts>")
    for tier, block in rep.get("tiers", {}).items():
        t = block["totals"]
        good = block["passed_all_gates"]
        cls = "ok" if good else ("gap" if not (t[FAIL] or t[ERROR]) else "bad")
        answer = "yes" if good else ("gaps only" if cls == "gap" else "no")
        q = ("Can I cut from the set today?" if tier == "substrate"
             else "Is the estate holding?")
        p.append(
            f'<div class=v><div class=q>{_e(q)}</div>'
            f'<div class="a {cls}">{_e(answer)}</div>'
            f'<div class=n>{t[PASS]}/{sum(t.values())} gates pass'
            + (f" · {t[FAIL] + t[ERROR]} red" if t[FAIL] + t[ERROR] else "")
            + (f" · {t[UNIMPLEMENTED]} declared gap" if t[UNIMPLEMENTED] else "")
            + "</div></div>"
        )
    p.append("</div>")

    # ── alerts ───────────────────────────────────────────────────────────
    alerts = rep.get("alerts", [])
    if alerts:
        p.append(f"<h2>Needs attention · {len(alerts)}</h2>")
        for a in alerts:
            cls = "ok" if a["severity"] == PASS else "bad"
            p.append(
                f'<div class="alert {cls}"><div class=t>{_e(a["title"])}</div>'
                f'<div class=d>{_e(a["detail"])}</div>'
                f'<div class=x><b>Do:</b> {_e(a["action"])}</div></div>'
            )
    elif rep.get("deltas", {}).get("baseline"):
        p.append("<h2>Needs attention</h2><div class=alert><div class=t>Baseline run"
                 "</div><div class=d>No previous report to compare against; the next "
                 "run will show deltas.</div></div>")
    else:
        p.append("<h2>Needs attention</h2><div class=alert><div class=t>Nothing new"
                 "</div><div class=d>No gate changed state since the last run.</div></div>")

    # ── the flywheel ─────────────────────────────────────────────────────
    stations = flywheel.assess(rep)
    integ = flywheel.integrity(stations)
    p.append("<h2>The flywheel</h2>")
    p.append(f'<div class=wheel>{_wheel_svg(stations, integ)}</div>')
    p.append(
        f'<p class=sub><b>Integrity</b> is scored over the {integ["measured"]} '
        f'stations that have a gate; <b>coverage</b> is how much of the wheel has one '
        f'at all. They are kept apart on purpose — "a third of it is broken" and '
        f'"a third of it is invisible" have different repairs. Weights are '
        f'provisional (FSD §4.1). Tap a station for the evidence behind its colour.</p>'
    )
    for st in stations:
        p.append(_station_card(st))

    # ── the chain ────────────────────────────────────────────────────────
    chain = by_id.get("build-chain", {}).get("detail", {}).get("chain", [])
    if chain:
        p.append("<h2>Build chain</h2><div class=chain>")
        for link in chain:
            st = link["state"]
            cls = {"green": "ok", "red": "bad", "in-flight": "flight"}.get(st, "")
            note = (", ".join(link["failing"])[:70] if link["failing"]
                    else (f"{len(link['running'])} running" if link["running"] else ""))
            p.append(
                f'<div class="link {cls}"><div class=r>{_e(link["repo"])}</div>'
                f'<div class=s>{_e(st)} · <span class=mono>{_e(link["head_sha"])}</span>'
                f' · {_e(link["head_at"])}</div>'
                + (f"<div class=w>{_e(note)}</div>" if note else "")
                + "</div>"
            )
        p.append("</div>")

    # ── the full board ───────────────────────────────────────────────────
    for tier in rep.get("tiers", {}):
        p.append(f'<h2>{_e(tier)}</h2><div class=tw><table><tr><th></th><th>gate</th>'
                 "<th>answer</th><th>since</th></tr>")
        for g in gates:
            if g.get("tier") != tier:
                continue
            cls = _DOT[g["status"]]
            p.append(
                f'<tr><td class=st><span class="pill {cls}">{_e(_WORD[g["status"]])}</span></td>'
                f'<td class=g><code>{_e(g["gate"])}</code>'
                f'<div class=q>{_e(g.get("detail", {}).get("question", ""))}</div></td>'
                f'<td>{_e(g["summary"])}</td>'
                f'<td class=mono>{_e(_since(rep, g["gate"]))}</td></tr>'
            )
        p.append("</table></div>")

    # ── trend ────────────────────────────────────────────────────────────
    tiles = _tiles(rep, history)
    if tiles:
        p.append("<h2>Trend</h2><div class=trend>")
        for key, label, value, series in tiles:
            p.append(f'<div class=tr><div class=k>{_e(label)}</div>'
                     f'<div class=n>{_e(value)}</div>{_spark(series)}</div>')
        p.append("</div>")

    p.append(
        "<footer>Grace assembles and asks; "
        '<a href="https://github.com/CIRISAI/CIRISConformance">CIRISConformance</a> '
        "decides what passing means. A <span class=gap>declared gap</span> is not a "
        "pass and never counts toward <code>passed_all_gates</code>.<br>"
        f'Public non-archived repositories only · <code>{_e(rep.get("schema", ""))}</code> · '
        '<a href="https://github.com/CIRISAI/CIRISGrace">source</a></footer>'
    )
    p.append("</main></body></html>")
    return "".join(p)


_RAG_HEX = {"green": "var(--ok)", "amber": "var(--gap)", "red": "var(--bad)",
            "unknown": "var(--dim)"}


def _wheel_svg(stations: list[dict], integ: dict) -> str:
    """Eight stations on a rim, arrowheads showing which way the wheel turns.

    Drawn rather than listed because the loop *is* the claim: a reader who sees
    a ring understands that ⑧ feeds ① without being told, and that a seized
    bearing anywhere stops everything downstream of it.
    """
    # The viewBox is wider than the wheel needs: the longest station name sits
    # due west at text-anchor=end, so the left margin has to hold a whole label
    # or it clips. Sized against "Mesh & commerce" at 25px.
    W, H, CX, CY, R, LR = 1060, 800, 530, 392, 232, 296
    s: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="Ecosystem flywheel, {integ["pct"]}% integrity">'
    ]
    # the rim
    s.append(f'<circle cx="{CX}" cy="{CY}" r="{R}" fill="none" '
             f'stroke="var(--line)" stroke-width="26" />')

    n = len(stations)
    for i, st in enumerate(stations):
        a = math.radians(-90 + i * (360 / n))
        x, y = CX + R * math.cos(a), CY + R * math.sin(a)

        # rotation arrowhead, sitting between this station and the next
        am = math.radians(-90 + (i + 0.5) * (360 / n))
        ax, ay = CX + R * math.cos(am), CY + R * math.sin(am)
        deg = math.degrees(am) + 90
        s.append(f'<path d="M -9 -7 L 9 0 L -9 7 Z" fill="var(--dim)" '
                 f'transform="translate({ax:.1f},{ay:.1f}) rotate({deg:.1f})" />')

        lx, ly = CX + LR * math.cos(a), CY + LR * math.sin(a)
        cos = math.cos(a)
        anchor = "middle" if abs(cos) < 0.25 else ("start" if cos > 0 else "end")
        # push the label clear of the node when it sits due north or south
        dy = -52 if (abs(cos) < 0.25 and math.sin(a) < 0) else (
            64 if abs(cos) < 0.25 else -4)
        opacity = ' fill-opacity="0.42"' if st["rag"] == "unknown" else ""

        s.append(f'<a href="#st-{_e(st["id"])}">')
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="31" '
                 f'fill="{_RAG_HEX[st["rag"]]}"{opacity} '
                 f'stroke="var(--card)" stroke-width="5" />')
        s.append(f'<text class="num" x="{x:.1f}" y="{y + 9:.1f}" '
                 f'text-anchor="middle">{_e(st["num"])}</text>')
        s.append(f'<text class="lbl" x="{lx:.1f}" y="{ly + dy:.1f}" '
                 f'text-anchor="{anchor}">{_e(st["name"])}</text>')
        note = (f'{st["measured"]} check' + ("s" if st["measured"] != 1 else "")
                if st["measured"] else "unmeasured")
        s.append(f'<text class="sub" x="{lx:.1f}" y="{ly + dy + 24:.1f}" '
                 f'text-anchor="{anchor}">{_e(note)}</text>')
        s.append("</a>")

    s.append(f'<text class="ctrk" x="{CX}" y="{CY - 42}" '
             f'text-anchor="middle">INTEGRITY</text>')
    s.append(f'<text class="ctr" x="{CX}" y="{CY + 16}" '
             f'text-anchor="middle">{integ["pct"]}%</text>')
    s.append(f'<text class="sub" x="{CX}" y="{CY + 48}" text-anchor="middle">'
             f'of the {integ["measured"]} of {integ["stations"]} stations we can see'
             f'</text>')
    s.append(f'<text class="sub" x="{CX}" y="{CY + 72}" text-anchor="middle">'
             f'coverage {integ["coverage"]}% · {integ["unmeasured"]} unmeasured</text>')
    s.append("</svg>")
    return "".join(s)


def _station_card(st: dict) -> str:
    p = [f'<details class=st id="st-{_e(st["id"])}"'
         + (" open" if st["rag"] in ("red", "unknown") else "") + ">"]
    label = {"green": "green", "amber": "gaps declared", "red": "red",
             "unknown": "unmeasured"}[st["rag"]]
    p.append(f'<summary><span class="dot {st["rag"]}"></span>'
             f'<span class=n>{_e(st["num"])} {_e(st["name"])}</span>'
             f'<span class=w>{_e(label)}</span></summary><div class=body>')
    p.append(f'<div class=w>{_e(st["what"])}</div>')

    for ev in st["evidence"]:
        p.append(f'<div class=ev><span class="dot {ev["rag"]}"></span>'
                 f'<span><code>{_e(ev["id"])}</code> '
                 f'<span class=k>{_e(ev["question"])}</span><br>{_e(ev["summary"])}'
                 f'</span></div>')
    if not st["evidence"]:
        p.append('<div class=ev><span class="dot unknown"></span><span>'
                 'No gate measures this station. It is scored as <b>unknown</b>, '
                 'not green — an unmeasured bearing cannot be assumed sound.'
                 '</span></div>')

    if st["gaps"]:
        p.append("<div class=gaps><b>Declared gaps</b><ul>"
                 + "".join(f"<li>{_e(g)}</li>" for g in st["gaps"])
                 + "</ul></div>")

    p.append('<div class=repos>' + "".join(
        f'<a href="https://github.com/CIRISAI/{_e(r)}">{_e(r)}&nbsp;↗</a>'
        for r in st["repos"]) + "</div>")
    p.append("</div></details>")
    return "".join(p)


def _since(rep: dict, gate_id: str) -> str:
    d = rep.get("deltas", {})
    for m in d.get("gates", []):
        if m["gate"] == gate_id:
            return f"{m['from']}→{m['to']}"
    for m in d.get("metrics", []):
        if m["gate"] == gate_id:
            return f"{m['metric']} {'+' if m['change'] > 0 else ''}{m['change']}"
    return "·"


def _tiles(rep: dict, history: list[dict]) -> list[tuple]:
    from .state import _METRIC_PROSE, _metrics

    cur = _metrics(rep)
    out = []
    for gid, metrics in sorted(cur.items()):
        for key, val in sorted(metrics.items()):
            if key not in _METRIC_PROSE or not isinstance(val, (int, float)):
                continue
            series = [
                h["metrics"][gid][key]
                for h in history
                if isinstance(h.get("metrics", {}).get(gid, {}).get(key), (int, float))
            ] + [val]
            out.append((key, _METRIC_PROSE[key], val, series))
    return out


def _spark(series: list[float]) -> str:
    """A sparkline, or a flat rule when there is not yet a line to draw."""
    if len(series) < 2:
        return ('<svg viewBox="0 0 100 26" preserveAspectRatio="none" aria-hidden="true">'
                '<line x1="0" y1="13" x2="100" y2="13" stroke="var(--line)" '
                'stroke-width="1.5" /></svg>')
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1
    step = 100 / (len(series) - 1)
    pts = " ".join(
        f"{i * step:.1f},{23 - (v - lo) / span * 20:.1f}" for i, v in enumerate(series)
    )
    rising = series[-1] > series[0]
    colour = "var(--bad)" if rising else "var(--ok)"
    return (
        f'<svg viewBox="0 0 100 26" preserveAspectRatio="none" aria-hidden="true">'
        f'<polyline points="{pts}" fill="none" stroke="{colour}" stroke-width="1.5" '
        f'vector-effect="non-scaling-stroke" stroke-linejoin="round" /></svg>'
    )


def _ago(iso: str) -> str:
    try:
        then = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return ""
    mins = (datetime.now(timezone.utc) - then).total_seconds() / 60
    if mins < 90:
        return f"{int(mins)} min ago"
    if mins < 60 * 36:
        return f"{int(mins / 60)} h ago"
    return f"{int(mins / 1440)} days ago"
