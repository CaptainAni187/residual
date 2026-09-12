from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from residual.eval.ablations import run_all
from residual.eval.score import score_run
from residual.explain.close import Close, run_close
from residual.ledger.money import Money, total
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate


def _paise(value: Any) -> Any:
    if isinstance(value, Money):
        return value.paise
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _paise(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _paise(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_paise(v) for v in value]
    return value


def _week(close: Close, index: int, start: date, end: date) -> dict[str, Any]:
    return {
        "index": index,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "gross": close.variance.gross_captured.paise,
        "landed": close.variance.cash_landed.paise,
        "gap": close.gap.paise,
        "residual": close.residual.paise,
        "checked": close.checked,
        "covered": close.fully_covered,
        "findings": [
            {
                "cause": str(f.cause),
                "title": f.title,
                "amount": f.amount.paise,
                "alarming": bool(f.alarming),
                "note": f.evidence.note,
                "sql": f.evidence.sql,
                "refs": list(f.evidence.entity_ids)[:12],
            }
            for f in close.findings
        ],
        "unresolved": [
            {"kind": u.kind, "detail": u.detail, "amount": u.amount.paise}
            for u in close.unresolved
        ],
        "risks": [
            {"kind": r.kind, "detail": r.detail, "amount": r.amount.paise}
            for r in close.risks
        ],
    }


def live_payments() -> dict[str, Any]:
    fixture = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "razorpay"
    payments = json.loads((fixture / "live_payment.json").read_text())["items"]
    refunds = json.loads((fixture / "live_refund.json").read_text())["items"]

    from residual.ingest.razorpay import payments_to_events
    from residual.position.engine import fold

    events = payments_to_events(payments, refunds)
    balances = fold(events)
    balances.check(complete=False)
    return {
        "payments": len(payments),
        "refunds": len(refunds),
        "captured": sum(1 for e in events if e.type == "payment_captured"),
        "failed": sum(1 for e in events if e.type == "payment_failed"),
        "balances": {str(a): m.paise for a, m in sorted(balances.items()) if m.paise},
        "rows": [
            {
                "id": p["id"],
                "status": p["status"],
                "method": p["method"],
                "bank": p.get("bank"),
                "amount": p["amount"],
                "billed": p.get("fee") or 0,
                "tax": p.get("tax") or 0,
                "rate": round(((p.get("fee") or 0) - (p.get("tax") or 0)) / p["amount"], 6)
                if p["amount"] and p.get("fee")
                else None,
            }
            for p in payments
        ],
    }


def build(live: dict[str, Any] | None = None) -> dict[str, Any]:
    result = simulate(BENCHMARK)
    result.require_all_scenarios_fired()
    result.log.verify_chain()
    head = str(result.log.head)
    events = result.log.events()
    warehouse = Warehouse.build(events)
    contracted = {str(m): rate for m, rate in BENCHMARK.base_rates}

    weeks: list[dict[str, Any]] = []
    closes: list[Close] = []
    for index, offset in enumerate(range(0, BENCHMARK.days, 7)):
        start = result.start + timedelta(days=offset)
        end = start + timedelta(days=6)
        close = run_close(events, start, end, contracted, warehouse)
        closes.append(close)
        weeks.append(_week(close, index, start, end))

    report = score_run(events, result.truth, result.start, BENCHMARK.days, contracted)
    ablations = [
        {"name": a.name, "ours": a.ours, "ablated": a.ablated, "verdict": a.verdict}
        for a in run_all(events, result.truth, result.start, BENCHMARK.days, contracted)
    ]

    from residual.ledger import select

    captures = list(select.captures(events))
    return {
        "generated": datetime.now(tz=UTC).date().isoformat(),
        "meta": {
            "events": len(events),
            "postings": warehouse.sql("SELECT count(*) FROM postings")[0][0],
            "weeks": len(weeks),
            "captured": total(c.gross for c in captures).paise,
            "chain_head": head[:16],
            "verifiers": closes[0].checked,
            "days": BENCHMARK.days,
        },
        "weeks": weeks,
        "evaluation": {
            "close_rate": report.close_rate,
            "windows": len(report.windows),
            "hallucinated": report.hallucinated_cause_rate,
            "precision": report.cause_precision,
            "recall": report.cause_recall,
            "exact": report.amount_exact_rate,
            "rupee_error": report.rupee_error.paise,
        },
        "ablations": ablations,
        "live": live if live is not None else live_payments(),
    }


TEMPLATE = Path(__file__).with_name("template.html")


def write(out: Path, live: dict[str, Any] | None = None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    data = build(live=live)
    blob = json.dumps(data, separators=(",", ":"))

    template = TEMPLATE.read_text()
    if "__DATA__" not in template:
        raise ValueError("template has no __DATA__ placeholder")
    page = template.replace("__DATA__", blob)

    (out / "data.json").write_text(blob)
    (out / "index.html").write_text(page)
    return out / "index.html"
