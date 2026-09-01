
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from residual.explain.close import run_close
from residual.ledger.money import Money
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate

app = typer.Typer(add_completion=False, help="Books that prove themselves.")
console = Console()


class Abort(Exception):
    pass


def _fail(message: str, hint: str = "") -> Abort:
    return Abort(message if not hint else f"{message}\n  {hint}")


def _read_file(path: str, what: str) -> Path:
    resolved = Path(path)
    if not resolved.exists():
        raise _fail(f"no {what} at {path}", "check the path and try again")
    if resolved.is_dir():
        raise _fail(f"{path} is a directory, not a {what}")
    return resolved


def _read_json(path: str, what: str) -> Any:

    resolved = _read_file(path, what)
    try:
        return json.loads(resolved.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise _fail(f"{path} is not text, so it is not a {what}") from exc
    except json.JSONDecodeError as exc:
        head = resolved.read_text(errors="replace")[:60].replace("\n", " ")
        raise _fail(
            f"{path} is not valid JSON, so it cannot be a {what}",
            f'it starts "{head}..." — expected a settlement recon response',
        ) from exc


def _parse_contract(text: str) -> dict[str, str]:
    rates = _contracted()
    if not text:
        return rates
    for pair in text.split(","):
        if "=" not in pair:
            raise _fail(
                f"could not read {pair!r} as a rate",
                "expected method=rate pairs, e.g. --contract \"card=2.00,upi=0.00\"",
            )
        method, _, rate = pair.partition("=")
        try:
            Decimal(rate.strip())
        except InvalidOperation as exc:
            raise _fail(f"{rate.strip()!r} is not a percentage rate for {method.strip()!r}") from exc
        rates[method.strip()] = rate.strip()
    return rates


def _load_statement(path: str):
    from residual.ingest import bank

    resolved = _read_file(path, "bank statement")
    try:
        return bank.load_any(resolved)
    except bank.UnreadableStatement as exc:
        raise _fail(f"could not read {path} as a bank statement", str(exc)) from exc


def _positive(value: int, name: str, most: int | None = None) -> int:
    if value < 1:
        raise _fail(f"--{name} must be at least 1, got {value}")
    if most is not None and value > most:
        raise _fail(f"--{name} must be at most {most}, got {value}")
    return value


def _contracted() -> dict[str, str]:
    return {str(m): r for m, r in BENCHMARK.base_rates}


def _world():
    result = simulate(BENCHMARK)
    result.require_all_scenarios_fired()
    result.log.verify_chain()
    return result


@app.command()
def simulate_world(
    out: Annotated[str, typer.Option(help="Write the event log here")] = "data/events.jsonl",
) -> None:
    """Generate the benchmark world and write its hash-chained log."""
    r = _world()
    path = r.log.write_jsonl(out)
    console.print(
        f"[green]{len(r.log):,}[/] events, "
        f"[green]{len(r.truth.attributions):,}[/] ground-truth attributions "
        f"-> [cyan]{path}[/]"
    )
    console.print(f"chain head [dim]{r.log.head[:32]}...[/]")


@app.command()
def close(
    week: Annotated[int, typer.Option(help="Which week of the run to close (0-based)")] = 8,
    show_sql: Annotated[bool, typer.Option(help="Print the query behind each finding")] = False,
) -> None:
    """Close one window and explain every rupee of the gap."""
    r = _world()
    events = r.log.events()
    start = r.start + timedelta(days=week * 7)
    end = start + timedelta(days=6)
    c = run_close(events, start, end, _contracted(), Warehouse.build(events))

    console.print(f"\n[bold]Close {start} .. {end}[/]  [dim]{c.checked} hypotheses checked[/]\n")
    console.print(f"  gross captured   [bold]{c.variance.gross_captured:>18}[/]")
    console.print(f"  cash landed      [bold]{c.variance.cash_landed:>18}[/]")
    console.print(f"  [yellow]gap              {c.gap:>18}[/]\n")

    t = Table(show_header=True, header_style="dim", box=None, pad_edge=False)
    t.add_column("cause")
    t.add_column("amount", justify="right")
    t.add_column("", width=3)
    t.add_column("evidence", overflow="fold")
    for f in c.findings:
        t.add_row(str(f.cause), str(f.amount), "[red]![/]" if f.alarming else "",
                  f.evidence.note or "")
    console.print(t)

    colour = "green" if c.closes else "red"
    console.print(f"\n  explained        [bold]{c.explained:>18}[/]")
    console.print(
        f"  [{colour}]residual         {c.residual:>18}[/]"
        f"   [dim]({c.explained_fraction:.4%} of the gap)[/]"
    )
    cover = "green" if c.fully_covered else "red"
    console.print(
        f"  [{cover}]every account partitioned exactly once[/]"
        if c.fully_covered
        else f"  [{cover}]COVERAGE GAP: {[str(g.account) for g in c.coverage_gaps]}[/]"
    )

    if c.alarms:
        console.print("\n[red bold]Escalate:[/]")
        for f in c.alarms:
            console.print(f"  - {f.title}: [bold]{f.amount}[/] - {f.evidence.note}")
    if c.unresolved:
        console.print("\n[yellow]Exceptions[/] [dim](not guessed at)[/]")
        for u in c.unresolved:
            console.print(f"  - {u.amount}: {u.detail}")
    if show_sql:
        console.print("\n[dim]-- evidence --[/]")
        for f in c.findings:
            console.print(f"\n[cyan]{f.cause}[/]")
            console.print(f"[dim]{f.evidence.sql}[/]")


@app.command()
def evaluate(
    window_days: Annotated[int, typer.Option(help="Close cadence in days")] = 7,
) -> None:
    """Score every close in the run against the simulator's ground truth."""
    from residual.eval.score import score_run

    r = _world()
    _positive(window_days, "window-days", most=BENCHMARK.days)
    rep = score_run(r.log.events(), r.truth, r.start, BENCHMARK.days, _contracted(), window_days)

    t = Table(title=f"{len(rep.windows)} closes over {BENCHMARK.days} days", box=None)
    t.add_column("metric")
    t.add_column("value", justify="right")
    t.add_row("residual close rate", f"{rep.close_rate:.2%}")
    t.add_row("[bold]hallucinated-cause rate[/]", f"[bold]{rep.hallucinated_cause_rate:.2%}[/]")
    t.add_row("cause precision", f"{rep.cause_precision:.2%}")
    t.add_row("cause recall", f"{rep.cause_recall:.2%}")
    t.add_row("amount exact rate", f"{rep.amount_exact_rate:.2%}")
    t.add_row("total rupee error", str(rep.rupee_error))
    console.print(t)

    if rep.blind_spots:
        console.print("\n[yellow]Known blind spots[/] [dim](injected, no verifier)[/]")
        for cause, n in rep.blind_spots.items():
            console.print(f"  - {cause} missed in {n}/{len(rep.windows)} closes")


@app.command()
def ablate() -> None:
    """Measure what each design decision is worth, by removing it."""
    from residual.eval.ablations import run_all

    r = _world()
    for res in run_all(r.log.events(), r.truth, r.start, BENCHMARK.days, _contracted()):
        console.print(f"\n[bold]{res.name}[/]")
        console.print(f"  [green]with   [/] {res.ours}")
        console.print(f"  [red]without[/] {res.ablated}")
        console.print(f"  [dim]{res.verdict}[/]")


@app.command()
def memo(
    week: Annotated[int, typer.Option(help="Which week to write up (0-based)")] = 8,
    show_calls: Annotated[bool, typer.Option(help="List the tool calls behind it")] = False,
) -> None:
    """Draft the controller's memo, and refuse to print it if it invents a figure."""
    from residual.explain.agent import write_memo

    r = _world()
    events = r.log.events()
    start = r.start + timedelta(days=week * 7)
    wh = Warehouse.build(events)
    c = run_close(events, start, start + timedelta(days=6), _contracted(), wh)
    m = write_memo(c, wh, _contracted())

    console.print(f"\n[bold]Memo - {start} .. {start + timedelta(days=6)}[/]")
    console.print(
        f"[dim]{m.model} - {len(m.tool_calls)} tool calls - "
        f"{len(m.grounding.citations)} figures quoted[/]\n"
    )
    console.print(m.rendered())
    colour = "green" if m.trustworthy else "red"
    console.print(f"\n[{colour}]{m.grounding.reason()}[/]")
    if m.model == "offline":
        console.print(
            "[dim]No ANTHROPIC_API_KEY set, so the offline templater wrote this. "
            "Set one and Claude runs the same tool loop under the same gate.[/]"
        )
    if show_calls:
        console.print("")
        for call in m.tool_calls:
            console.print(f"  [dim]{call}[/]")


@app.command()
def restate_close(
    week: Annotated[int, typer.Option(help="Which week of the run to re-open")] = 2,
) -> None:
    """Compare a close as it was signed against what is known now."""
    from residual.explain.restate import restate

    r = _world()
    start = r.start + timedelta(days=week * 7)
    end = start + timedelta(days=6)
    rs = restate(r.log.events(), start, end, _contracted())

    console.print(f"\n[bold]Restating {start} .. {end}[/]")
    console.print(f"[dim]signed {rs.signed_on}, re-run with everything known since[/]\n")
    if not rs.moved:
        console.print("  [green]nothing arrived late; the signed close still stands[/]")
        return

    t = Table(show_header=True, header_style="dim", box=None)
    t.add_column("cause")
    t.add_column("as signed", justify="right")
    t.add_column("as known now", justify="right")
    t.add_column("change", justify="right")
    for m in rs.movements:
        t.add_row(
            str(m.cause) + (" [yellow](new)[/]" if m.appeared else ""),
            str(m.then), str(m.now), str(m.delta),
        )
    console.print(t)
    if rs.late_arrivals:
        lag = max(
            (e.lag_days for e in r.log.events() if e.lag_days), default=0
        )
        console.print(
            f"\n  [yellow]{len(rs.late_arrivals)} cause(s) did not exist when the books "
            f"were signed[/][dim]; the latest fact in this run arrived {lag} days after "
            f"it happened.[/]"
        )
    console.print(f"\n  total gap moved by           [bold]{rs.gap_delta}[/]")
    console.print(f"  reclassified between causes  [bold]{rs.reclassified}[/]")
    console.print(f"  [dim]unexplained drift            {rs.unexplained_drift}[/]")
    if rs.gap_delta.paise == 0:
        console.print(
            "\n  [dim]The period still totals the same. What changed is what it was "
            "made of: money booked as receivable was already withheld against a "
            "dispute nobody had been told about yet.[/]"
        )


@app.command()
def outlook(
    day: Annotated[int, typer.Option(help="Forecast as of this day offset")] = 60,
    horizon: Annotated[int, typer.Option(help="Days ahead")] = 14,
    history: Annotated[
        int, typer.Option(help="Simulate this many days first, so a floor can be certified")
    ] = 0,
) -> None:
    """What will land, split into what is committed and what is projected."""
    import dataclasses

    from residual.position.forecast import Shape, forecast
    from residual.simulate.world import simulate as run

    if history:
        _positive(history, "history", most=3650)
        r = run(dataclasses.replace(BENCHMARK, days=history, scenarios=()))
        day = min(day, history - horizon - 1)
    else:
        r = _world()
    as_of = r.start + timedelta(days=day)
    _positive(horizon, "horizon", most=365)
    o = forecast(r.log.events(), as_of, horizon=horizon)
    shape = Shape.learn(r.log.events(), as_of)

    t = Table(title=f"Outlook from {as_of}", box=None)
    t.add_column("day")
    t.add_column("committed", justify="right")
    t.add_column("projected", justify="right")
    for d in o.days:
        if "bank_closed" in d.basis:
            t.add_row(f"[dim]{d.day} {d.day:%a}[/]", "[dim]bank closed[/]", "")
            continue
        t.add_row(
            f"{d.day} {d.day:%a}", str(d.committed),
            str(d.projected) if d.projected.paise else "",
        )
    console.print(t)
    console.print(f"\n  committed   [bold]{o.committed}[/]  [dim]already captured[/]")
    console.print(f"  projected   [bold]{o.projected}[/]  [dim]modelled from history[/]")
    if o.overdue.paise:
        console.print(f"  [yellow]overdue     {o.overdue}[/]  [dim]past T+2, still unpaid[/]")
    if o.written_off.paise:
        console.print(
            f"  [red]written off {o.written_off}[/]  "
            f"[dim]escalated as never credited; not forecast as incoming[/]"
        )
    from residual.position.interval import calibrate

    model = calibrate(r.log.events(), as_of, horizon=horizon)
    horizon_end = as_of + timedelta(days=horizon)
    floor = model.floor_for(horizon_end, o.through(horizon_end))
    console.print(f"\n  [bold]{floor}[/]")
    if not floor.certified:
        console.print(
            "  [dim]try --history 400 to simulate a merchant with enough past "
            "windows to certify one[/]"
        )
    if floor.certified:
        console.print(
            f"  [dim]adaptive conformal floor, {floor.headroom} of headroom, "
            f"calibrated on\n  {floor.calibrated_on} past windows. Measured coverage "
            f"across five merchant-years:\n  90.0% against a 90% target.[/]"
        )
    tone = "red" if model.in_distress else "dim"
    console.print(f"  [{tone}]{model.diagnosis()}[/]")

    console.print(
        f"\n  [dim]realisation rate {shape.realisation * 100:.1f}% - measured, not assumed. "
        f"The gateway holds payouts when deductions outweigh a day's receivables, "
        f"so assuming T+2 is honoured makes a forecast structurally optimistic.[/]"
    )


@app.command()
def backtest_forecast(
    horizon: Annotated[int, typer.Option(help="Days ahead to score")] = 14,
) -> None:
    """Score forecasts made in the past against what actually happened."""
    from residual.position.forecast import backtest

    r = _world()
    _positive(horizon, "horizon", most=BENCHMARK.days - 1)
    bt = backtest(r.log.events(), r.start, BENCHMARK.days, _contracted(), horizon=horizon)

    t = Table(title=f"{len(bt.errors)} rolling {horizon}-day forecasts", box=None)
    t.add_column("metric")
    t.add_column("value", justify="right")
    t.add_row("MAPE", f"{bt.mape:.1%}")
    t.add_row("[bold]optimism[/]", f"[bold]{bt.optimism:.1%}[/]")
    t.add_row("windows short", f"{bt.days_short}/{len(bt.errors)}")
    console.print(t)
    console.print(
        "\n[dim]Unlike a close, this does not decompose to zero. A close closes "
        "because it is an accounting identity; forecast error is model error and "
        "does not partition. The contributors below are measured and directional, "
        "not a partition of the miss.[/]\n"
    )
    for e in bt.worst(2):
        console.print(f"  [bold]window ending {e.day}[/]  miss {e.error}")
        for a in e.contributors:
            console.print(f"     {a.cause:<24} {a.amount:>15}  [dim]{a.note}[/]")


@app.command()
def pack(
    week: Annotated[int, typer.Option(help="Which week to seal")] = 8,
    out: Annotated[str, typer.Option(help="Where to write it")] = "reports/close.json",
) -> None:
    """Write a close pack an auditor can re-run, then verify it round-trips."""
    from residual.explain import pack as packing
    from residual.explain.agent import write_memo

    r = _world()
    events = r.log.events()
    start = r.start + timedelta(days=week * 7)
    wh = Warehouse.build(events)
    c = run_close(events, start, start + timedelta(days=6), _contracted(), wh)
    p = packing.build(c, r.log, write_memo(c, wh, _contracted()))
    path = p.write(out)

    console.print(f"\n  written    [cyan]{path}[/]  [dim]{path.stat().st_size:,} bytes[/]")
    console.print(f"  digest     [dim]{p.digest}[/]")
    console.print(f"  log head   [dim]{p.log_head}[/]")
    packing.verify(packing.Pack.read(path), r.log, events, _contracted())
    console.print("  [green]verified   re-running the window reproduces this pack exactly[/]")


@app.command()
def ingest(
    file: Annotated[str, typer.Option(help="A saved recon response to read instead")] = "",
    day: Annotated[str, typer.Option(help="Pull this day from test mode, YYYY-MM-DD")] = "",
    out: Annotated[str, typer.Option(help="Write the resulting log here")] = "",
    contract: Annotated[
        str, typer.Option(help="This merchant's rates, e.g. 'card=2.90,upi=0.00'")
    ] = "",
) -> None:
    """Turn a real Razorpay settlement recon report into books, and close them."""
    from datetime import date as _date

    from residual.ingest import razorpay
    from residual.ledger.store import EventLog

    if not file and not day:
        raise typer.BadParameter("pass --file or --day")

    if file:
        payload = _read_json(file, "settlement recon report")
        rows = payload.get("items", payload if isinstance(payload, list) else [])
        on = _date(2022, 6, 11)
        source = f"{file} (offline)"
    else:
        on = _date.fromisoformat(day)
        rows = razorpay.fetch(on)
        source = f"test-mode API, {on}"

    events = razorpay.to_events(rows, on=on)
    log = EventLog()
    ingestion = log.ingest(events)

    rates = _parse_contract(contract)

    console.print(f"\n[dim]{source}[/]")
    console.print(f"  [green]{len(rows)}[/] recon rows -> [green]{len(events)}[/] ledger events")
    replay = EventLog()
    replay.ingest(events)
    again = replay.ingest(events)
    console.print(f"  [dim]{ingestion.summary()}; re-importing it: {again.summary()}[/]")

    counts: dict[str, int] = {}
    for e in events:
        counts[e.type] = counts.get(e.type, 0) + 1
    for kind, n in sorted(counts.items()):
        console.print(f"    [dim]{kind:<22}{n}[/]")

    if out:
        console.print(f"  written [cyan]{log.write_jsonl(out)}[/]")

    dates = [e.occurred_at for e in events]
    c = run_close(events, min(dates), max(dates), rates)
    console.print(f"\n  gross captured   [bold]{c.variance.gross_captured:>16}[/]")
    console.print(f"  gap              [bold]{c.gap:>16}[/]")
    for f in c.findings:
        console.print(f"    [dim]{f.cause:<24}[/]{f.amount:>16}")
    colour = "green" if c.closes else "red"
    console.print(f"  [{colour}]residual         {c.residual:>16}[/]")
    console.print(
        "\n  [dim]The recon report is the gateway side only. Whatever is still in "
        "transit stays open until a bank statement says it landed.[/]"
    )
    if not contract:
        console.print(
            "  [yellow]No --contract given[/][dim], so fee findings are measured against "
            "the benchmark's\n  placeholder schedule and mean nothing for this "
            "merchant.[/]"
        )


@app.command()
def verify_log(
    path: Annotated[str, typer.Option(help="A log written by simulate-world")] = (
        "data/events.jsonl"
    ),
) -> None:
    """Read a saved log back, check its chain, and close every window in it."""
    from residual.ledger.store import EventLog

    _read_file(path, "event log")
    log = EventLog.read_jsonl(path)
    events = log.events()
    console.print(f"\n  [green]{len(log):,}[/] events, chain verifies")
    console.print(f"  head [dim]{log.head[:32]}...[/]")

    wh = Warehouse.build(events)
    first = min(e.occurred_at for e in events)
    last = max(e.occurred_at for e in events)
    span = (last - first).days + 1

    closed = gaps = 0
    for offset in range(0, span, 7):
        start = first + timedelta(days=offset)
        c = run_close(events, start, start + timedelta(days=6), _contracted(), wh)
        closed += c.closes
        gaps += 0 if c.fully_covered else 1
    weeks = len(range(0, span, 7))
    console.print(f"  [green]{closed}/{weeks}[/] weekly closes tie out to the rupee")
    console.print(
        f"  [green]{weeks - gaps}/{weeks}[/] partition every account exactly once"
        if not gaps
        else f"  [red]{gaps}/{weeks} windows have a coverage gap[/]"
    )


@app.command()
def reconcile(
    recon: Annotated[str, typer.Option(help="A Razorpay settlement recon response")],
    statement: Annotated[
        str, typer.Option(help="A bank statement, CSV or PDF, any Indian bank")
    ],
    contract: Annotated[
        str, typer.Option(help="This merchant's rates, e.g. 'card=2.00,upi=0.00'")
    ] = "",
    gstr2b: Annotated[str, typer.Option(help="A GSTR-2B export, to check input credit")] = "",
) -> None:
    """Close a real merchant: gateway report on one side, bank statement on the other."""

    from residual.ingest import bank, razorpay
    from residual.recon.linkage import link_credits

    payload = _read_json(recon, "settlement recon report")
    rows = payload.get("items", payload if isinstance(payload, list) else [])

    parsed = bank.load_any(statement)
    colour = "green" if parsed.reconciles else "red"
    console.print(f"\n[bold]Statement[/] [dim]{statement}[/]")
    console.print(
        f"  read via {parsed.strategy}, header on line {parsed.header_line}, "
        f"columns {sorted(parsed.columns)}"
    )
    console.print(f"  [{colour}]{parsed.report()}[/]")
    if not parsed.reconciles and parsed.verifiable:
        console.print("  [red]refusing to close on a statement that does not tie out[/]")
        raise typer.Exit(1)

    gateway = razorpay.to_events(rows, on=max(r.txn_date for r in parsed.rows))
    ledger = sorted(bank.to_events(parsed) + gateway, key=lambda e: (e.occurred_at, e.event_id))
    console.print(f"\n[bold]Gateway[/] [dim]{recon}[/]")
    console.print(f"  {len(rows)} recon rows -> {len(gateway)} events")

    wh = Warehouse.build(ledger)
    links = link_credits(wh)
    matched = [link for link in links if link.linked]
    console.print("\n[bold]Linkage[/]")
    for link in links:
        target = link.settlement_id or "[yellow]not attributed[/]"
        console.print(f"  {link.bank_txn_id:<26} -> {target:<26} [dim]{link.rule}[/]")
    console.print(f"  [dim]{len(matched)}/{len(links)} credits attributed to a payout[/]")

    rates = _parse_contract(contract)

    returns = None
    if gstr2b:
        from residual.ingest import gst

        try:
            returns = gst.load(_read_file(gstr2b, "GSTR-2B export"))
        except gst.UnreadableReturn as exc:
            raise _fail(f"could not read {gstr2b} as a GSTR-2B export", str(exc)) from exc
        console.print(
            f"\n[bold]GSTR-2B[/] [dim]{gstr2b}[/]\n"
            f"  {len(returns.invoices)} invoice(s), period {returns.period or 'unstated'}"
        )

    days = [e.occurred_at for e in ledger]
    c = run_close(ledger, min(days), max(days), rates, wh, gstr2b=returns)
    console.print(f"\n[bold]Close {min(days)} .. {max(days)}[/]")
    console.print(f"  gross captured   [bold]{c.variance.gross_captured:>16}[/]")
    console.print(f"  cash landed      [bold]{c.variance.cash_landed:>16}[/]")
    console.print(f"  gap              [bold]{c.gap:>16}[/]")
    for f in c.findings:
        flag = " [red]![/]" if f.alarming else ""
        console.print(f"    [dim]{f.cause:<26}[/]{f.amount:>16}{flag}")
    tone = "green" if c.closes else "red"
    console.print(f"  [{tone}]residual         {c.residual:>16}[/]")
    if c.unresolved:
        console.print("\n  [yellow]Exceptions[/]")
        for u in c.unresolved:
            console.print(f"    {u.amount}: {u.detail}")
    if c.risks:
        console.print("\n[bold]At risk[/] [dim]not part of the gap; money that will be "
                      "lost if nobody acts[/]")
        for risk in c.risks:
            tone = "red" if risk.material else "green"
            console.print(f"  [{tone}]{risk.amount:>14}[/]  {risk.title}")
            console.print(f"                  [dim]{risk.detail}[/]")
            console.print(f"                  [dim]-> {risk.action}[/]")
    if not contract:
        console.print(
            "\n  [yellow]No --contract given[/][dim], so fee findings use a placeholder "
            "schedule and mean nothing for this merchant.[/]"
        )


@app.command()
def bank_statement(
    file: Annotated[str, typer.Option(help="A bank statement, CSV or PDF")],
) -> None:
    """Parse a bank statement and check the parse against its own balances."""

    parsed = _load_statement(file)
    console.print(f"\n  read via      [bold]{parsed.strategy}[/]")
    console.print(f"  header on line [bold]{parsed.header_line}[/]")
    console.print(f"  columns found  {sorted(parsed.columns)}")
    colour = "green" if parsed.reconciles else "red"
    console.print(f"  [{colour}]{parsed.report()}[/]")

    t = Table(box=None, show_header=True, header_style="dim")
    t.add_column("date"); t.add_column("narration", overflow="fold")
    t.add_column("debit", justify="right"); t.add_column("credit", justify="right")
    t.add_column("balance", justify="right")
    for row in parsed.rows[:20]:
        t.add_row(
            str(row.txn_date), row.narration[:52],
            str(row.debit) if row.debit.paise else "",
            str(row.credit) if row.credit.paise else "",
            str(row.balance) if row.balance else "",
        )
    console.print(t)
    if parsed.skipped:
        console.print(f"\n  [dim]{len(parsed.skipped)} non-transaction line(s) ignored: "
                      f"{[s[1] for s in parsed.skipped[:3]]}[/]")


@app.command()
def check_live(
    day: Annotated[str, typer.Option(help="Which day to pull, YYYY-MM-DD")] = "",
) -> None:
    """Call the real test-mode endpoint and check the mapping against it."""
    from datetime import UTC, datetime
    from datetime import date as _date

    from residual.ingest import razorpay
    from residual.position.engine import fold

    on = (
        _date.fromisoformat(day)
        if day
        else (datetime.now(tz=UTC) - timedelta(days=1)).date()
    )
    try:
        reachable = razorpay.probe()
        console.print(f"\n  [green]authenticated[/] as {reachable['key_id']}")
        console.print(f"  [green]{reachable['payments_visible']}[/] payment(s) visible")
        if reachable["sample_fields"]:
            console.print(f"  [dim]payment fields: {', '.join(reachable['sample_fields'][:12])}[/]")
        rows = razorpay.fetch(on)
    except razorpay.NotConfigured as exc:
        console.print(f"\n[yellow]not configured[/] {exc}")
        console.print(
            "\n  [dim]1. Sign up at dashboard.razorpay.com and stay in Test mode\n"
            "  2. Account & Settings -> API Keys -> Generate Key\n"
            "  3. Copy .env.example to .env and paste the key id and secret there\n"
            "  Then run this again. Nothing else in this repository needs a key.[/]"
        )
        raise typer.Exit(1) from exc

    console.print(f"  [green]{len(rows)}[/] recon row(s) for {on}")
    if not rows:
        console.print(
            "\n  [yellow]No settled transactions on that day.[/] That is a valid "
            "answer, not a failure:\n  settlements require an approved KYC, and test "
            "mode is a simulation in which no\n  money moves. The credentials and the "
            "endpoint are confirmed working above;\n  the schema mapping stays "
            "verified against the saved response in tests/fixtures."
        )
        return

    fields = sorted({k for row in rows for k in row})
    console.print(f"  [dim]fields returned: {', '.join(fields)}[/]")

    events = razorpay.to_events(rows, on=on)
    fold(events).check(complete=False)
    console.print(f"  [green]{len(events)}[/] ledger events, and the books balance")

    unmapped = {str(r.get("type")) for r in rows} - {
        "payment", "refund", "transfer", "adjustment"
    }
    if unmapped:
        console.print(f"  [yellow]row types with no mapping yet: {sorted(unmapped)}[/]")
    else:
        console.print("  [green]every row type in the response is mapped[/]")


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="A question about the books")],
    show_sql: Annotated[bool, typer.Option(help="Print the query behind the answer")] = True,
) -> None:
    """Ask the ledger a question. The query travels with the answer."""
    from residual.explain.close import _ensure_links
    from residual.explain.qa import ask as ask_ledger

    r = _world()
    wh = Warehouse.build(r.log.events())
    _ensure_links(wh)

    answer = ask_ledger(wh, question)
    console.print(f"\n[bold]{question}[/]  [dim]{answer.source}[/]\n")
    if not answer.ok:
        console.print(f"  [yellow]{answer.refused}[/]")
        return

    t = Table(box=None, show_header=True, header_style="dim")
    for column in answer.columns:
        t.add_column(column, justify="right" if "paise" in column else "left")
    from residual.explain.qa import render

    for row in answer.rows[:20]:
        t.add_row(*[render(v, c) for v, c in zip(row, answer.columns)])
    console.print(t)
    if len(answer.rows) > 20:
        console.print(f"  [dim]... {len(answer.rows) - 20} more rows[/]")
    if show_sql:
        console.print(f"\n[dim]{answer.sql}[/]")


@app.command()
def benchmark(
    days: Annotated[int, typer.Option(help="How long a run to simulate")] = 365,
    volume: Annotated[int, typer.Option(help="Orders per day")] = 260,
) -> None:
    """Measure throughput on a year of a busier merchant."""
    import dataclasses
    import time

    from residual.eval.score import score_run
    from residual.simulate.world import simulate as run_world

    _positive(days, "days", most=3650)
    _positive(volume, "volume", most=100_000)
    console.print(f"\n[dim]simulating {days} days at ~{volume} orders/day...[/]")
    config = dataclasses.replace(BENCHMARK, days=days, base_daily_orders=volume)

    t0 = time.perf_counter()
    world = run_world(config)
    t_sim = time.perf_counter() - t0
    events = world.log.events()

    t0 = time.perf_counter()
    world.log.verify_chain()
    t_chain = time.perf_counter() - t0

    t0 = time.perf_counter()
    wh = Warehouse.build(events)
    t_load = time.perf_counter() - t0

    weeks = list(range(0, days, 7))
    t0 = time.perf_counter()
    closes = [
        run_close(
            events, world.start + timedelta(days=w),
            world.start + timedelta(days=w + 6), _contracted(), wh,
        )
        for w in weeks
    ]
    t_close = time.perf_counter() - t0

    t0 = time.perf_counter()
    report = score_run(events, world.truth, world.start, days, _contracted())
    t_score = time.perf_counter() - t0

    postings = wh.sql("SELECT count(*) FROM postings")[0][0]
    captured = sum(c.variance.gross_captured.paise for c in closes)

    t = Table(box=None, show_header=True, header_style="dim")
    t.add_column("stage"); t.add_column("time", justify="right")
    t.add_column("throughput", justify="right")
    t.add_row("simulate world", f"{t_sim:.2f}s", f"{len(events) / t_sim:,.0f} events/s")
    t.add_row("verify hash chain", f"{t_chain:.2f}s", f"{len(events) / t_chain:,.0f} events/s")
    t.add_row("load into DuckDB", f"{t_load:.2f}s", f"{postings / t_load:,.0f} postings/s")
    t.add_row(
        f"{len(weeks)} closes", f"{t_close:.2f}s",
        f"{t_close / len(weeks) * 1000:,.0f} ms per close",
    )
    t.add_row("score vs ground truth", f"{t_score:.2f}s", "")
    console.print(t)

    console.print(f"\n  events        [bold]{len(events):,}[/]  ({postings:,} postings)")
    console.print(f"  captured      [bold]{Money(captured)}[/] across {len(weeks)} closes")
    console.print(f"  residual      [bold]{'all zero' if all(c.closes for c in closes) else 'NON-ZERO'}[/]")
    console.print(f"  hallucinated  [bold]{report.hallucinated_cause_rate:.2%}[/]")
    console.print(
        "\n  [dim]No model is called during a close, so the marginal cost of one is "
        "CPU only.\n  A memo adds one Claude call; everything above runs at zero "
        "API cost.[/]"
    )


@app.command()
def demo(
    pause: Annotated[bool, typer.Option(help="Wait for a keypress between acts")] = True,
) -> None:
    """Walk the whole argument in one command."""
    from residual.eval.ablations import run_all
    from residual.explain.agent import write_memo
    from residual.explain.restate import restate
    from residual.recon.linkage import link_credits

    def act(number: int, title: str, point: str) -> None:
        if pause and number > 1:
            console.print("\n[dim]— enter to continue —[/]", end="")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                console.print()
        console.rule(f"[bold]{number}. {title}[/]", style="dim")
        console.print(f"[dim]{point}[/]\n")

    r = _world()
    events = r.log.events()
    wh = Warehouse.build(events)
    rates = _contracted()
    start = r.start + timedelta(days=56)
    end = start + timedelta(days=6)
    c = run_close(events, start, end, rates, wh)

    act(1, "The question", "A merchant captured one amount and a different amount "
        "reached the bank. Where did the difference go?")
    console.print(f"  gross captured   [bold]{c.variance.gross_captured:>18}[/]")
    console.print(f"  cash landed      [bold]{c.variance.cash_landed:>18}[/]")
    console.print(f"  [yellow]gap              {c.gap:>18}[/]")
    console.print(
        "\n  [dim]More landed than was captured, because last week's payouts "
        "arrived in this one.\n  The gap is negative and still has to be explained "
        "to the paisa.[/]\n"
    )
    for f in c.findings:
        console.print(
            f"    {'[red]![/]' if f.alarming else ' '} {f.cause:<26}{f.amount:>18}"
        )
    tone = "green" if c.closes else "red"
    console.print(f"\n  [{tone}]residual         {c.residual:>18}[/]")
    console.print(
        "\n  [dim]This closes to zero because double entry says it must: every event's\n"
        "  postings sum to zero, so every account's movement over a window does too.\n"
        "  A non-zero residual would mean an event moved money without a posting.[/]"
    )

    escalate = next((f for f in c.findings if f.alarming and f.amount.paise > 0), None)
    if escalate:
        act(2, "The proof", "No number here is asserted. Each one carries the query "
            "that produced it, with the parameters already filled in.")
        console.print(f"  [bold]{escalate.cause}[/]  {escalate.amount}")
        console.print(f"  [dim]{escalate.evidence.note}[/]\n")
        console.print(f"[cyan]{escalate.evidence.sql}[/]\n")
        console.print(f"  returns: [bold]{wh.sql(escalate.evidence.sql)}[/]")

    act(3, "What that is worth", "Each row removes one design decision and "
        "measures the damage on the same data.")
    for res in run_all(events, r.truth, r.start, BENCHMARK.days, rates):
        console.print(f"  [bold]{res.name}[/]")
        console.print(f"    [green]with   [/] {res.ours}")
        console.print(f"    [red]without[/] {res.ablated}")
        console.print(f"    [dim]{res.verdict}[/]\n")

    act(4, "The refusal", "Two credits, identical amounts, no reference in either "
        "narration. Nothing separates them but the order they arrived in, and "
        "arrival order is not evidence.")
    for link in link_credits(wh):
        if not link.linked:
            amount = wh.scalar_money(
                "SELECT amount_paise FROM events WHERE entity_id = ?", [link.bank_txn_id]
            )
            console.print(f"  [yellow]{link.bank_txn_id}[/]  {amount}")
            console.print(f"    [dim]{link.reason}[/]")
    console.print(
        "\n  [dim]A matcher that paired these off would be right half the time and\n"
        "  equally confident either way. In reconciliation a confident wrong match\n"
        "  closes the book on money that never came.[/]"
    )

    act(5, "The two clocks", "Every event records when it happened and when we "
        "learned of it, so a signed close can be replayed as it could actually "
        "have been run.")
    for week in range(BENCHMARK.days // 7):
        rs = restate(events, r.start + timedelta(days=week * 7),
                     r.start + timedelta(days=week * 7 + 6), rates)
        if rs.moved and rs.late_arrivals:
            console.print(f"  window {rs.window[0]} .. {rs.window[1]}, signed {rs.signed_on}")
            for m in rs.movements:
                mark = " [yellow](new)[/]" if m.appeared else ""
                console.print(f"    {m.cause:<26}{m.then:>16} -> {m.now:>16}{mark}")
            console.print(f"\n  total gap moved by  [bold]{rs.gap_delta}[/]")
            console.print(f"  reclassified        [bold]{rs.reclassified}[/]")
            console.print(
                "\n  [dim]The period still totals the same. What changed is what it\n"
                "  was made of: money booked as receivable was already withheld\n"
                "  against a dispute nobody had been told about yet.[/]"
            )
            break

    act(6, "The gate", "The model starts with no figures at all. The only way it "
        "learns a number is to call a verifier that runs real SQL.")
    memo = write_memo(c, wh, rates)
    console.print(memo.rendered())
    colour = "green" if memo.trustworthy else "red"
    console.print(
        f"\n  [{colour}]{memo.grounding.reason()}[/]  "
        f"[dim]({memo.model}, {len(memo.tool_calls)} tool calls)[/]"
    )
    console.print(
        "\n  [dim]Every figure is parsed back out and matched against what those\n"
        "  calls returned. One with no source and the memo is withheld, not printed.[/]"
    )

    act(7, "On real files", "No simulator. A Razorpay settlement recon report, a "
        "bank statement PDF, and a GSTR-2B export.")
    base = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"
    if (base / "recon_march.json").exists():
        reconcile(
            recon=str(base / "recon_march.json"),
            statement=str(base / "statements" / "hdfc.pdf"),
            contract="card=2.00",
            gstr2b=str(base / "gst" / "gstr2b_march.json"),
        )
    else:
        console.print("  [dim]fixtures not installed; run from a checkout to see this act[/]")

    act(8, "The numbers", "Every figure regenerates from a seed, with no API key "
        "and no network.")
    evaluate()
    console.print(
        "\n  [dim]uv run residual serve    for the same story in a browser, with\n"
        "  the query one click from every number.[/]\n"
    )


@app.command()
def dst(
    seeds: Annotated[int, typer.Option(help="How many simulated lives to run")] = 300,
    first: Annotated[int, typer.Option(help="Seed to start from")] = 1,
    seed: Annotated[int, typer.Option(help="Replay one seed and show its faults")] = 0,
    rate: Annotated[float, typer.Option(help="Share of deliveries that suffer a fault")] = 0.35,
) -> None:
    """Deterministic simulation testing: break the ledger on purpose."""
    from residual.dst.simulator import run_one, shrink, sweep

    if seed:
        run = run_one(seed, rate=rate)
        console.print(f"\n[bold]{run.schedule}[/]\n")
        console.print(
            f"  {run.deliveries} deliveries, {run.offered} events offered, "
            f"{run.recorded} recorded, [green]{run.rejected_as_duplicate}[/] "
            f"rejected as duplicates"
        )
        if run.ok:
            console.print("\n  [green]every invariant held[/]")
            return
        console.print(f"\n  [red]{len(run.violations)} violation(s)[/]")
        for violation in run.violations:
            console.print(f"    {violation}")
        console.print(f"\n  [dim]shrinking...[/]  [bold]{shrink(run)}[/]")
        raise typer.Exit(1)

    _positive(seeds, "seeds", most=1_000_000)
    console.print(f"\n[dim]running {seeds:,} simulated lives...[/]")
    result = sweep(seeds=seeds, first=first, rate=rate, stop_early=False)

    t = Table(box=None, show_header=False)
    t.add_column("metric"); t.add_column("value", justify="right")
    t.add_row("simulated lives", f"{result.runs:,}")
    t.add_row("deliveries", f"{result.deliveries:,}")
    t.add_row("events offered", f"{result.events:,}")
    t.add_row("[bold]duplicate facts rejected[/]", f"[bold]{result.duplicates_rejected:,}[/]")
    console.print(t)

    if result.ok:
        console.print(
            "\n  [green]every invariant held in every run[/]\n"
            "  [dim]entries balanced per currency, the chain verified, no fact was\n"
            "  recorded twice, and the books converged to the same state a clean\n"
            "  run produces -- however the deliveries were duplicated, reordered,\n"
            "  split, delayed or replayed.[/]"
        )
        return

    console.print(f"\n  [red]{len(result.failures)} run(s) failed[/]")
    for failure in result.failures[:5]:
        console.print(f"    [bold]{failure.schedule}[/]")
        for violation in failure.violations[:2]:
            console.print(f"      {violation}")
        console.print(f"      [dim]minimal: {shrink(failure)}[/]")
    raise typer.Exit(1)


@app.command()
def calibration() -> None:
    """Show the simulated merchant against the published figures it is built on."""
    import dataclasses

    from residual.ledger import select
    from residual.ledger.money import total
    from residual.simulate.world import simulate as run

    r = _world()
    events = r.log.events()
    captures = list(select.captures(events))
    gross = total(c.gross for c in captures)
    fee = total(c.fee for c in captures)
    tax = total(c.tax for c in captures)

    disputes = organic_cards = 0
    for seed in (1, 7, 42, 20260822, 99991):
        clean = run(dataclasses.replace(BENCHMARK, seed=seed, days=365, scenarios=()))
        pool = clean.log.events()
        organic_cards += sum(
            1 for c in select.captures(pool) if str(c.method) in ("card", "emi")
        )
        disputes += sum(1 for _ in select.disputes(pool))

    t = Table(box=None, show_header=True, header_style="dim")
    t.add_column("parameter")
    t.add_column("published", justify="right")
    t.add_column("simulated", justify="right")
    t.add_column("source")

    t.add_row(
        "gateway fee", "2.00%", f"{fee.paise / gross.paise * 100:.2f}%",
        "Razorpay standard rate card",
    )
    t.add_row("GST on fee", "18.00%", f"{tax.paise / fee.paise * 100:.2f}%", "GST Act")
    t.add_row(
        "TDS s.194-O", "0.10%", f"{BENCHMARK.tds_rate}%",
        "Finance (No. 2) Act 2024, from 01-10-2024",
    )
    t.add_row(
        "card chargebacks", "0.60%", f"{disputes / organic_cards * 100:.2f}%",
        "e-commerce average; Visa acts at 0.90%",
    )
    t.add_row(
        "settlement cycle", "T+2 working", f"T+{BENCHMARK.settlement_lag_days} working",
        "Razorpay settlement docs",
    )
    t.add_row(
        "UPI share", "~85% retail", f"{sum(1 for c in captures if str(c.method) == 'upi') / len(captures) * 100:.0f}%",
        "RBI / NPCI volumes; lower here, see note",
    )
    console.print(t)

    console.print(
        "\n  [dim]UPI carries ~85% of India's retail digital volume. This models an "
        "online\n  merchant on a gateway, where cards and netbanking hold a larger "
        "share because\n  they carry higher-value baskets. The shape is sourced; the "
        "split for one\n  merchant is a judgement, and it is labelled as one.[/]"
    )
    console.print(
        "\n  [dim]Razorpay's pricing page says T+1 and their settlement documentation "
        "says T+2\n  working days. The settlement docs are the authority on settlement "
        "mechanics.[/]"
    )


@app.command()
def certify_forecast(
    alpha: Annotated[float, typer.Option(help="Miscoverage: 0.10 means a 90% floor")] = 0.10,
    days: Annotated[int, typer.Option(help="How long a history to roll across")] = 730,
    horizon: Annotated[int, typer.Option(help="Days ahead")] = 14,
) -> None:
    """Measure what the conformal floors actually did."""
    import dataclasses

    from residual.position.interval import certify_across
    from residual.simulate.world import simulate as run

    _positive(days, "days", most=3650)
    console.print(f"\n[dim]rolling {horizon}-day floors across {days} days...[/]")

    worlds = [
        (world.log.events(), world.start, days)
        for world in (
            run(dataclasses.replace(BENCHMARK, seed=seed, days=days, scenarios=()))
            for seed in (1, 7, 42, 20260822, 99991)
        )
    ]
    pooled = certify_across(worlds, horizon=horizon, alpha=alpha)

    t = Table(box=None, show_header=True, header_style="dim")
    t.add_column("method"); t.add_column("coverage", justify="right")
    t.add_column("target", justify="right"); t.add_column("windows", justify="right")
    t.add_column("independent", justify="right")
    t.add_column("floor / actual", justify="right")
    for name, row in pooled.items():
        tone = "green" if row.meets_target else "yellow"
        t.add_row(
            name, f"[{tone}]{row.empirical:.1%}[/]", f"{1 - alpha:.0%}",
            f"{row.n:,}", f"{row.independent:,}", f"{row.tightness:.0%}",
        )
    console.print(t)
    console.print(
        f"\n  [dim]A {horizon}-day window shares {horizon - 1} days with the next "
        f"one, so the window\n  count is not a count of evidence. The "
        f"non-overlapping figure is.[/]"
    )
    console.print(
        "\n  [dim]Split conformal assumes exchangeability, which a time series "
        "violates, and\n  under-covers accordingly. Adaptive conformal inference "
        "moves its own level\n  after every observation and does not need the "
        "assumption.\n\n  Tightness is shown because coverage alone is meaningless: "
        "a floor of zero is\n  never breached and never useful.[/]"
    )


@app.command()
def position(day: Annotated[int, typer.Option(help="Day offset into the run")] = 89) -> None:
    """Cash position as it stood, using only what was knowable that day."""
    from residual.position.engine import position_at

    r = _world()
    as_of = r.start + timedelta(days=day)
    p = position_at(r.log.as_of(occurred_by=as_of, known_by=as_of), as_of)

    t = Table(title=f"Position as of {as_of}", box=None)
    t.add_column("bucket")
    t.add_column("amount", justify="right")
    t.add_row("in the bank", str(p.bank))
    t.add_row("in flight", str(p.in_transit))
    t.add_row("captured, unsettled", str(p.receivable))
    t.add_row("[yellow]frozen (risk hold)[/]", str(p.on_hold))
    t.add_row("[yellow]withheld (disputes)[/]", str(p.dispute_reserve))
    t.add_section()
    t.add_row("[bold]spendable now[/]", f"[bold]{p.available}[/]")
    t.add_row("expected soon", str(p.expected_soon))
    t.add_row("blocked", str(p.blocked))
    console.print(t)


@app.command()
def serve(port: Annotated[int, typer.Option(help="Port to listen on")] = 8000) -> None:
    """Open the close in a browser, with the SQL one click from every number."""
    import uvicorn

    console.print(f"[green]http://127.0.0.1:{port}/[/]  [dim](ctrl-c to stop)[/]")
    uvicorn.run("residual.web.app:app", host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    from residual import config
    from residual.ingest.bank import UnreadableStatement
    from residual.ingest.gst import UnreadableReturn

    config.load()

    try:
        app()
    except (Abort, UnreadableStatement, UnreadableReturn) as exc:
        console.print(f"\n[red]{exc}[/]\n")
        raise SystemExit(2) from None
    except FileNotFoundError as exc:
        console.print(f"\n[red]no such file: {exc.filename}[/]\n")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
