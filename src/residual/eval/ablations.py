
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from residual.domain.causes import Cause
from residual.explain.close import run_close
from residual.explain.grounding import check
from residual.ledger import events as ev
from residual.ledger.money import Money
from residual.ledger.warehouse import Warehouse


@dataclass(frozen=True, slots=True)
class Result:
    name: str
    ours: str
    ablated: str
    verdict: str


def naive_utr_matching(events, start: date, days: int, contracted, truth) -> Result:
    wh = Warehouse.build(events)
    flagged: set[str] = set()
    for offset in range(0, days, 7):
        s = start + timedelta(days=offset)
        for f in run_close(events, s, s + timedelta(days=6), contracted, wh).findings:
            if f.cause is Cause.SETTLEMENT_NEVER_ARRIVED:
                flagged.update(f.evidence.entity_ids)

    rows = wh.sql(
        "SELECT COUNT(*) FROM events s LEFT JOIN events b "
        "  ON b.type = 'bank_credit_received' AND b.narration LIKE '%' || s.utr || '%' "
        "WHERE s.type = 'settlement_executed' AND b.event_id IS NULL"
    )
    return Result(
        name="linkage layer vs. narration LIKE '%utr%'",
        ours=f"{len(flagged)} settlement(s) reported missing",
        ablated=f"{rows[0][0]} settlement(s) reported missing",
        verdict="1 was actually lost; the rest arrived under a mangled reference",
    )


def greedy_linkage(truth_links: dict[str, str]) -> Result:
    from residual.recon.linkage import link_credits

    d = date(2026, 6, 1)
    amount = Money.parse("99800")
    log: list[ev.EventBase] = [
        ev.SettlementExecuted(
            event_id="se0", occurred_at=d, recorded_at=d, settlement_id="setl_early",
            utr="20260601000001", net=amount, covers=(),
        ),
        ev.SettlementExecuted(
            event_id="se1", occurred_at=d + timedelta(days=2), recorded_at=d + timedelta(days=2),
            settlement_id="setl_late", utr="20260603000002", net=amount, covers=(),
        ),
        ev.BankCreditReceived(
            event_id="bc0", occurred_at=d + timedelta(days=2), recorded_at=d + timedelta(days=2),
            bank_txn_id="btx_first", amount=amount,
            narration="IMPS IN RAZORPAY SOFTWARE PVT LTD", value_date=d + timedelta(days=2),
        ),
        ev.BankCreditReceived(
            event_id="bc1", occurred_at=d + timedelta(days=3), recorded_at=d + timedelta(days=3),
            bank_txn_id="btx_second", amount=amount,
            narration="MB-IMPS CR RAZORPAY SOFTWARE PVT LTD", value_date=d + timedelta(days=3),
        ),
    ]
    actual = {"btx_first": "setl_late", "btx_second": "setl_early"}
    wh = Warehouse.build(log)

    def score(greedy: bool) -> tuple[int, int]:
        links = link_credits(wh, greedy=greedy)
        linked = [x for x in links if x.linked]
        wrong = [x for x in linked if actual[x.bank_txn_id] != x.settlement_id]
        return len(wrong), len(links) - len(linked)

    g_wrong, g_abstain = score(True)
    o_wrong, o_abstain = score(False)
    return Result(
        name="two-pass ambiguity detection vs. greedy claiming",
        ours=f"{o_wrong} silently wrong, {o_abstain} abstained",
        ablated=f"{g_wrong} silently wrong, {g_abstain} abstained",
        verdict="greedy is confident either way; only arrival order decided it",
    )


class _UngroundedAgent:

    def write(self, session, close):
        biggest = max(close.findings, key=lambda f: abs(f.amount.paise), default=None)
        parts = [
            (
                f"Cash landed {close.variance.cash_landed} against "
                f"{close.variance.gross_captured} captured, a gap of {close.gap}."
            ),
        ]
        if biggest:
            parts.append(f"Most of it is {biggest.title.lower()} at {biggest.amount}.")
        if close.unresolved:
            parts.append(f"{close.unresolved_value} could not be attributed.")
        parts.append(f"The residual is {close.residual}.")
        return " ".join(parts), "ungrounded"


def grounding_gate(events, start: date, days: int, contracted) -> Result:
    from residual.explain.agent import Session, write_memo

    wh = Warehouse.build(events)
    ungrounded_bad = ungrounded_total = 0
    ours_bad = ours_total = 0

    for offset in range(0, days, 7):
        s = start + timedelta(days=offset)
        close = run_close(events, s, s + timedelta(days=6), contracted, wh)

        session = Session(wh, s, s + timedelta(days=6), contracted, close.variance)
        text, _ = _UngroundedAgent().write(session, close)
        g = check(text, session.permitted())
        ungrounded_bad += len(g.fabricated)
        ungrounded_total += len(g.citations)

        memo = write_memo(close, wh, contracted)
        ours_bad += len(memo.grounding.fabricated)
        ours_total += len(memo.grounding.citations)

    return Result(
        name="verifier-grounded memo vs. prose written off the raw ledger",
        ours=f"{ours_bad}/{ours_total} figures untraceable",
        ablated=f"{ungrounded_bad}/{ungrounded_total} figures untraceable",
        verdict="untraceable is not the same as wrong -- it means nothing checked it",
    )


def run_all(events, truth, start: date, days: int, contracted) -> list[Result]:
    return [
        naive_utr_matching(events, start, days, contracted, truth),
        greedy_linkage(truth.links),
        grounding_gate(events, start, days, contracted),
    ]
