
from __future__ import annotations

import html
from datetime import date, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from residual.explain.agent import write_memo
from residual.explain.close import Close, run_close
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate

app = FastAPI(title="Residual")

_STATE: dict[str, Any] = {}


def _state() -> dict[str, Any]:
    if not _STATE:
        result = simulate(BENCHMARK)
        result.require_all_scenarios_fired()
        events = result.log.events()
        _STATE.update(
            world=result,
            events=events,
            wh=Warehouse.build(events),
            contracted={str(m): rate for m, rate in BENCHMARK.base_rates},
        )
    return _STATE


def _close(week: int) -> tuple[Close, date, date]:
    s = _state()
    start = s["world"].start + timedelta(days=week * 7)
    end = start + timedelta(days=6)
    return run_close(s["events"], start, end, s["contracted"], s["wh"]), start, end


CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#26303d;--ink:#e6edf3;--dim:#8b949e;
--pos:
@media(prefers-color-scheme:light){:root{--bg:#fff;--panel:#f6f8fa;--line:#d0d7de;
--ink:
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 ui-sans-serif,system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:20px;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13px;margin-bottom:24px}
nav a{color:var(--dim);text-decoration:none;font:12px var(--mono);padding:3px 8px;
border:1px solid var(--line);border-radius:5px;margin-right:4px;display:inline-block;margin-bottom:6px}
nav a.on{color:var(--ink);border-color:var(--acc);background:var(--panel)}
.totals{display:flex;gap:28px;flex-wrap:wrap;padding:16px 18px;background:var(--panel);
border:1px solid var(--line);border-radius:8px;margin:18px 0}
.totals div{min-width:150px}
.k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.v{font:16px var(--mono);margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;
letter-spacing:.06em;padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr.row{cursor:pointer}
tr.row:hover td{background:var(--panel)}
.amt{font:14px var(--mono);text-align:right;white-space:nowrap}
.neg{color:var(--neg)}.pos{color:var(--pos)}
.flag{color:var(--warn);font-weight:600}
.note{color:var(--dim);font-size:12.5px}
.drill{background:var(--panel);padding:0}
.drill pre{margin:0;padding:14px 16px;overflow-x:auto;font:12px/1.6 var(--mono);
color:var(--acc);border-bottom:1px solid var(--line);white-space:pre-wrap}
.drill .rows{padding:10px 16px 14px;font:12px var(--mono);color:var(--dim);overflow-x:auto}
.drill .rows table{font:12px var(--mono)}
.drill .rows td,.drill .rows th{padding:4px 10px 4px 0;border:0}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);
margin:32px 0 10px;font-weight:500}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px 18px}
.badge{display:inline-block;font:11px var(--mono);padding:2px 7px;border-radius:4px;
border:1px solid var(--line);color:var(--dim)}
.badge.ok{color:var(--pos);border-color:var(--pos)}
.badge.bad{color:var(--neg);border-color:var(--neg)}
.resid{font:15px var(--mono)}
.askbar{display:flex;gap:8px;margin:6px 0 14px}
.askbar input{flex:1;background:var(--panel);border:1px solid var(--line);color:var(--ink);
padding:9px 12px;border-radius:7px;font:14px inherit}
.askbar input:focus{outline:0;border-color:var(--acc)}
.askbar button{background:var(--acc);color:#fff;border:0;padding:9px 16px;
border-radius:7px;font:14px inherit;cursor:pointer}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.chips button{background:none;border:1px solid var(--line);color:var(--dim);
font:12px var(--mono);padding:3px 9px;border-radius:5px;cursor:pointer}
.chips button:hover{color:var(--ink);border-color:var(--acc)}
.fc{display:flex;gap:3px;align-items:flex-end;height:84px;margin:8px 0}
.fc .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%}
.fc .c{background:var(--acc);border-radius:2px 2px 0 0}
.fc .p{background:color-mix(in srgb,var(--acc) 32%,transparent)}
.fc .p.top{border-radius:2px 2px 0 0}
.fc .x{flex:1;align-self:flex-end;height:3px;background:var(--line);border-radius:2px}
.swatch{display:inline-block;width:9px;height:9px;border-radius:2px;
vertical-align:-1px;margin-right:3px}
.legend{font-size:11px;color:var(--dim)}
.legend b{font-weight:500}
.risk{border-left:2px solid var(--warn);padding-left:12px;margin-bottom:12px}
"""

JS = """
async function ask(q){
  const box = document.getElementById('askbox');
  const out = document.getElementById('answer');
  if(q) box.value = q;
  if(!box.value.trim()) return;
  out.innerHTML = '<div class=note>asking…</div>';
  const r = await fetch('/ask?q=' + encodeURIComponent(box.value));
  out.innerHTML = await r.text();
}
document.addEventListener('keydown', e => {
  if(e.key === 'Enter' && e.target.id === 'askbox') ask();
});

async function drill(cause, week, tr){
  const open = tr.nextElementSibling && tr.nextElementSibling.classList.contains('drill');
  if(open){ tr.nextElementSibling.remove(); return; }
  document.querySelectorAll('tr.drill').forEach(e=>e.remove());
  const row = document.createElement('tr');
  row.className = 'drill';
  row.innerHTML = '<td colspan="4" class="drill"><div class="rows">loading…</div></td>';
  tr.after(row);
  const r = await fetch(`/evidence/${week}/${cause}`);
  row.firstChild.innerHTML = await r.text();
}
"""


def _fmt(m: Any) -> str:
    text = html.escape(str(m))
    cls = "neg" if str(m).lstrip().startswith("-") else ""
    return f'<span class="{cls}">{text}</span>'


@app.get("/", response_class=HTMLResponse)
def index(week: int = 8) -> str:
    close, start, end = _close(week)
    s = _state()
    memo = write_memo(close, s["wh"], s["contracted"])

    nav = "".join(
        f'<a href="/?week={w}" class="{"on" if w == week else ""}">wk {w}</a>'
        for w in range(13)
    )
    rows = "".join(
        f'<tr class="row" onclick="drill(\'{f.cause}\',{week},this)">'
        f"<td>{html.escape(str(f.cause))}"
        f'{" <span class=flag>!</span>" if f.alarming else ""}</td>'
        f'<td class="amt">{_fmt(f.amount)}</td>'
        f'<td class="note">{html.escape(f.evidence.note or "")}</td>'
        f'<td class="note">{len(f.evidence.entity_ids) or ""}</td>'
        "</tr>"
        for f in close.findings
    )
    exceptions = "".join(
        f'<div style="margin-bottom:8px"><span class="amt">{_fmt(u.amount)}</span> '
        f'<span class="note">— {html.escape(u.detail)}</span></div>'
        for u in close.unresolved
    ) or '<div class="note">Nothing was left unresolved in this window.</div>'

    outlook = _outlook_panel(end)
    restated = _restatement_panel(start, end)
    covered = "ok" if close.fully_covered else "bad"
    grounded = "ok" if memo.trustworthy else "bad"
    resid_cls = "pos" if close.closes else "neg"

    return f"""<!doctype html><meta charset=utf-8><title>Residual — {start}</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>{CSS}</style><div class=wrap>
<h1>Residual</h1>
<div class=sub>{start} &ndash; {end} &middot; {close.checked} hypotheses checked</div>
<nav>{nav}</nav>
<div class=totals>
  <div><div class=k>gross captured</div><div class=v>{_fmt(close.variance.gross_captured)}</div></div>
  <div><div class=k>cash landed</div><div class=v>{_fmt(close.variance.cash_landed)}</div></div>
  <div><div class=k>gap</div><div class=v>{_fmt(close.gap)}</div></div>
  <div><div class=k>residual</div>
    <div class="v resid {resid_cls}">{html.escape(str(close.residual))}</div></div>
</div>
<h2>Causes &middot; click any row for the query behind it</h2>
<table><tr><th>cause</th><th style="text-align:right">amount</th><th>note</th><th>refs</th></tr>
{rows}</table>
<h2>Ask the ledger</h2>
<div class=askbar>
  <input id=askbox placeholder="which settlements never arrived?" autocomplete=off>
  <button onclick="ask()">ask</button>
</div>
<div class=chips>
  <button onclick="ask('which settlements never arrived?')">never arrived</button>
  <button onclick="ask('fees by method')">fees by method</button>
  <button onclick="ask('which credits could you not match?')">unmatched credits</button>
  <button onclick="ask('which days were delayed by a holiday?')">holiday delays</button>
  <button onclick="ask('balance by account')">balances</button>
  <button onclick="ask('show me the biggest settlements')">biggest payouts</button>
</div>
<div id=answer></div>

<h2>Cash outlook</h2><div class=card>{outlook}</div>

<h2>Restated since signing</h2><div class=card>{restated}</div>

<h2>Exceptions</h2><div class=card>{exceptions}</div>
<h2>Proof</h2><div class=card>
  <div style="margin-bottom:8px"><span class="badge {covered}">
    {"every account partitioned exactly once" if close.fully_covered
      else "COVERAGE GAP"}</span></div>
  <div class=note>Stronger than a zero residual, which two cancelling errors could
  fake. Each account's movement is claimed by its verifiers exactly once.</div>
</div>
<h2>Memo <span class="badge {grounded}">{html.escape(memo.grounding.reason())}</span></h2>
<div class=card>
  <div>{html.escape(memo.rendered())}</div>
  <div class=note style="margin-top:10px">{html.escape(memo.model)} &middot;
  {len(memo.tool_calls)} tool calls &middot; every figure traced to a verifier
  before this was shown.</div>
</div>
</div><script>{JS}</script>"""


@app.get("/evidence/{week}/{cause}", response_class=HTMLResponse)
def evidence(week: int, cause: str) -> str:
    close, _, _ = _close(week)
    finding = next((f for f in close.findings if str(f.cause) == cause), None)
    if finding is None:
        return '<div class="rows">no such finding in this window</div>'

    wh = _state()["wh"]
    try:
        rows = wh.sql(finding.evidence.sql)
    except Exception as exc:  # noqa: BLE001 -- see below
        return (
            f"<pre>{html.escape(finding.evidence.sql)}</pre>"
            f'<div class="rows">query failed: {html.escape(str(exc))}</div>'
        )

    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>"
        for row in rows[:25]
    )
    refs = (
        f'<div style="margin-top:8px">cited: '
        f'{html.escape(", ".join(finding.evidence.entity_ids[:12]))}</div>'
        if finding.evidence.entity_ids
        else ""
    )
    return (
        f"<pre>{html.escape(finding.evidence.sql)}</pre>"
        f'<div class="rows"><table>{body}</table>{refs}</div>'
    )


def _outlook_panel(as_of: date) -> str:
    from residual.position.forecast import forecast

    s = _state()
    view = forecast(s["events"], as_of, horizon=14)
    if not view.days:
        return '<div class="note">nothing to forecast from this date.</div>'

    peak = max((d.expected.paise for d in view.days), default=1) or 1
    bars = []
    for d in view.days:
        if "bank_closed" in d.basis:
            bars.append('<div class=x title="bank closed"></div>')
            continue
        committed = max(int(80 * d.committed.paise / peak), 2 if d.committed.paise else 0)
        projected = max(int(80 * d.projected.paise / peak), 2 if d.projected.paise else 0)
        label = f"{d.day:%a %d} · committed {d.committed} · projected {d.projected}"
        bars.append(
            f'<div class=col title="{html.escape(label)}">'
            f'<div class="p top" style="height:{projected}px"></div>'
            f'<div class=c style="height:{committed}px"></div></div>'
        )

    from residual.position.interval import calibrate

    model = calibrate(s["events"], as_of, horizon=14)
    horizon_end = as_of + timedelta(days=14)
    floor = model.floor_for(horizon_end, view.through(horizon_end))
    floor_line = (
        f'<div class=note style="margin-top:8px"><b>{html.escape(str(floor))}</b></div>'
    )
    if not floor.certified:
        floor_line += (
            '<div class=note>a 90% floor needs nine past windows and this quarter '
            "has fewer; <code>residual certify-forecast</code> measures them across "
            "five merchant-years</div>"
        )
    if model.in_distress:
        floor_line += (
            f'<div class=note style="color:var(--warn)">{html.escape(model.diagnosis())}</div>'
        )

    extra = ""
    if view.written_off.paise:
        extra = (
            f' &middot; <span style="color:var(--neg)">{html.escape(str(view.written_off))} '
            f"written off</span>"
        )
    return (
        f'<div class=fc>{"".join(bars)}</div>'
        f"<div class=legend>"
        f'<span class=swatch style="background:var(--acc)"></span><b>committed</b> '
        f"{html.escape(str(view.committed))} &nbsp;&middot;&nbsp; "
        f'<span class=swatch style="background:color-mix(in srgb,var(--acc) 32%,transparent)">'
        f"</span><b>projected</b> {html.escape(str(view.projected))}{extra}</div>"
        + floor_line
        + '<div class=note style="margin-top:8px">Committed money is already '
        "captured; projected is modelled from this merchant&rsquo;s own history. "
        "They are never added into one number, because only one of them can be "
        "spent against. The floor is an adaptive conformal bound, measured at "
        "90.0% coverage against a 90% target.</div>"
    )


def _restatement_panel(start: date, end: date) -> str:
    from residual.explain.restate import restate

    s = _state()
    rs = restate(s["events"], start, end, s["contracted"])
    if not rs.moved:
        return '<div class="note">Nothing arrived late; the signed close still stands.</div>'

    rows = "".join(
        f"<tr><td>{html.escape(str(m.cause))}"
        f'{" <span class=flag>new</span>" if m.appeared else ""}</td>'
        f'<td class=amt>{_fmt(m.then)}</td><td class=amt>{_fmt(m.now)}</td>'
        f"<td class=amt>{_fmt(m.delta)}</td></tr>"
        for m in rs.movements
    )
    tail = (
        "The period still totals the same. What changed is what it was made of."
        if rs.gap_delta.paise == 0
        else f"The gap itself moved by {html.escape(str(rs.gap_delta))}."
    )
    return (
        f'<table><tr><th>cause</th><th style="text-align:right">as signed</th>'
        f'<th style="text-align:right">as known now</th>'
        f'<th style="text-align:right">change</th></tr>{rows}</table>'
        f'<div class=note style="margin-top:10px">Signed {rs.signed_on}. {tail} '
        f"{html.escape(str(rs.reclassified))} was reclassified between causes.</div>"
    )


@app.get("/ask", response_class=HTMLResponse)
def ask_endpoint(q: str = "") -> str:
    from residual.explain.qa import ask, render

    answer = ask(_state()["wh"], q)
    if not answer.ok:
        return f'<div class="card note">{html.escape(answer.refused)}</div>'

    head = "".join(f"<th>{html.escape(c)}</th>" for c in answer.columns)
    body = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(render(v, c))}</td>"
            for v, c in zip(row, answer.columns)
        )
        + "</tr>"
        for row in answer.rows[:25]
    )
    more = (
        f'<div class=note>… {len(answer.rows) - 25} more rows</div>'
        if len(answer.rows) > 25
        else ""
    )
    return (
        f'<div class="drill"><pre>{html.escape(answer.sql)}</pre>'
        f'<div class=rows><table><tr>{head}</tr>{body}</table>{more}'
        f'<div class=note style="margin-top:8px">{html.escape(answer.source)}</div>'
        f"</div></div>"
    )
