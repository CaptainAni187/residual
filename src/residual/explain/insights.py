from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from residual.domain.causes import Cause
from residual.explain.close import Close, Finding

Bucket = Literal["chase", "tax", "timing", "cost", "lost", "own"]

BUCKET_LABEL: dict[str, str] = {
    "chase": "Chase the gateway",
    "tax": "Claim on your taxes",
    "timing": "Will settle on its own",
    "cost": "Cost of doing business",
    "lost": "Lost",
    "own": "Your own payments (rent, suppliers)",
}

_CHASE = {
    Cause.SETTLEMENT_NEVER_ARRIVED, Cause.FEE_RATE_INCREASE, Cause.RISK_HOLD,
    Cause.DISPUTE_RESERVE_HELD, Cause.GATEWAY_ADJUSTMENT,
}
_TAX = {Cause.GST_ON_FEE, Cause.TDS_194O}
_TIMING = {Cause.CAPTURED_NOT_YET_SETTLED, Cause.SETTLEMENT_IN_FLIGHT, Cause.BANK_HOLIDAY_DELAY}
_LOST = {Cause.CHARGEBACK_LOST}


def bucket_of(finding: Finding) -> Bucket:
    cause = Cause(str(finding.cause))
    if cause in _CHASE:
        return "chase" if finding.amount.paise > 0 else "timing"
    if cause in _TAX:
        return "tax"
    if cause in _TIMING:
        return "timing"
    if cause in _LOST:
        return "lost"
    if cause is Cause.OTHER_BANK_OUTFLOW:
        return "own"
    return "cost"


def gst_claim_deadline(on: date) -> date:
    year_ending = on.year + 1 if on.month >= 4 else on.year
    return date(year_ending, 11, 30)


@dataclass(frozen=True, slots=True)
class Action:
    cause: str
    title: str
    why: str
    amount_paise: int
    urgency: Literal["high", "medium", "low"]
    who: str
    deadline: str
    draft: str
    impact: str
    score: int


@dataclass(frozen=True, slots=True)
class Insight:
    totals: dict[str, int]
    recoverable_paise: int
    actions: list[Action] = field(default_factory=list)


_WEIGHT = {"high": 3, "medium": 2, "low": 1}
_IST = ZoneInfo("Asia/Kolkata")


def _per_year(amount_paise: int, start: date, end: date) -> int:
    days = max(1, (end - start).days + 1)
    return round(amount_paise * 365 / days)


def _rupees(paise: int) -> str:
    digits = str((abs(paise) + 50) // 100)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return f"₹{digits}"


METHOD_NAME = {"card": "Card", "upi": "UPI", "netbanking": "Net banking", "wallet": "Wallet", "emi": "EMI"}


def _fee_detail(note: str) -> str:
    lines = []
    for part in note.split("--", 1)[-1].split(", "):
        method, _, rest = part.strip().partition(": ")
        if rest:
            lines.append(f"{METHOD_NAME.get(method, method.title())} payments: {rest.replace('INR ', '₹')}")
    return "; ".join(lines) + "." if lines else "Billed above your contracted rate."


def _action(finding: Finding, start: date, end: date, today: date | None = None) -> Action | None:
    cause = Cause(str(finding.cause))
    amount = finding.amount.paise
    refs = ", ".join(finding.evidence.entity_ids[:3])

    if cause is Cause.SETTLEMENT_NEVER_ARRIVED and amount > 0:
        return Action(
            str(cause), "Get the missing payout traced",
            f"The gateway marked it paid{f' (UTR {refs})' if refs else ''}, but it never reached your bank.",
            amount, "high", "Gateway settlements team", "Now — traces get harder the longer they wait",
            "payout_trace", f"Recover {_rupees(amount)}", amount * _WEIGHT["high"],
        )
    if cause is Cause.FEE_RATE_INCREASE and amount > 0:
        yearly = _per_year(amount, start, end)
        return Action(
            str(cause), "Dispute fees charged above your contract",
            _fee_detail(finding.evidence.note),
            amount, "high", "Your gateway account manager", "Before the next settlement",
            "fee_dispute", f"Recover {_rupees(amount)} now, and stop about {_rupees(yearly)} a year",
            (yearly + amount) * _WEIGHT["high"],
        )
    if cause is Cause.GATEWAY_ADJUSTMENT and amount > 0:
        return Action(
            str(cause), "Ask what the gateway's adjustment was for",
            f"The gateway took this out of a payout{f' ({refs})' if refs else ''} without a matching fee or refund.",
            amount, "medium", "Gateway support team", "This week",
            "escalate", f"Recover up to {_rupees(amount)}", amount * _WEIGHT["medium"],
        )
    if cause is Cause.RISK_HOLD and amount > 0:
        return Action(
            str(cause), "Ask when the held funds will be released",
            "The gateway froze this money under a risk review.",
            amount, "medium", "Gateway risk team", "This week",
            "escalate", f"Release {_rupees(amount)}", amount * _WEIGHT["medium"],
        )
    if cause is Cause.DISPUTE_RESERVE_HELD and amount > 0:
        return Action(
            str(cause), "Answer the open disputes with evidence",
            "This is held against customer disputes. Winning them releases it.",
            amount, "medium", "Gateway disputes dashboard", "Before each dispute's response deadline",
            "escalate", f"Win back up to {_rupees(amount)}", amount * _WEIGHT["medium"],
        )
    if cause is Cause.GST_ON_FEE and amount > 0:
        deadline = gst_claim_deadline(start)
        left = (deadline - (today or datetime.now(tz=_IST).date())).days
        urgency: Literal["high", "medium", "low"]
        if left < 0:
            urgency, when = "low", f"The claim window closed on {deadline:%d %b %Y} (Section 16(4)) — check with your CA"
        elif left <= 30:
            urgency, when = "high", f"By {deadline:%d %b %Y} — {left} day{'s' if left != 1 else ''} left (Section 16(4))"
        else:
            urgency, when = "low", f"By {deadline:%d %b %Y} (Section 16(4))"
        return Action(
            str(cause), "Claim GST paid on gateway fees",
            "GST on the gateway's fee is input tax credit, once it shows in your GSTR-2B. "
            "Check that with GST Credit Checker before you claim.",
            amount, urgency, "Your CA, in your GST return", when,
            "", f"Claim {_rupees(amount)} as input credit", amount * _WEIGHT[urgency],
        )
    if cause is Cause.TDS_194O and amount > 0:
        return Action(
            str(cause), "Claim TDS the gateway deducted",
            "Deducted under section 194-O. It counts against your income tax once it appears in Form 26AS.",
            amount, "low", "Your CA, in your income tax return", "In this financial year's return",
            "", f"Offset {_rupees(amount)} of tax", amount * _WEIGHT["low"],
        )
    if cause is Cause.CHARGEBACK_LOST and amount > 0:
        return Action(
            str(cause), "Look at why chargebacks are being lost",
            "This money is gone. The fix is preventing the next one.",
            amount, "low", "Your team", "Ongoing",
            "", "Prevent future losses", amount // 10,
        )
    return None


def analyse(close: Close, start: date, end: date, today: date | None = None) -> Insight:
    totals: dict[str, int] = dict.fromkeys(BUCKET_LABEL, 0)
    for finding in close.findings:
        totals[bucket_of(finding)] += finding.amount.paise
    actions = [a for f in close.findings if (a := _action(f, start, end, today)) is not None]
    actions.sort(key=lambda a: -a.score)
    recoverable = sum(
        f.amount.paise for f in close.findings
        if bucket_of(f) in ("chase", "tax") and f.amount.paise > 0
    )
    return Insight(totals=totals, recoverable_paise=recoverable, actions=actions)


@dataclass(frozen=True, slots=True)
class Week:
    start: date
    end: date
    gross_paise: int
    gap_paise: int
    totals: dict[str, int]
    fee_rate: float
    contract_rate: float
    overcharge_paise: int
    flagged: int


def weeks_between(first: date, last: date) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    cursor = first
    while cursor <= last:
        out.append((cursor, min(cursor + timedelta(days=6), last)))
        cursor += timedelta(days=7)
    return out


def week_of(close: Close, start: date, end: date) -> Week:
    insight = analyse(close, start, end)
    gross = close.variance.gross_captured.paise
    fees = sum(
        f.amount.paise for f in close.findings
        if str(f.cause) in (Cause.NORMAL_FEE, Cause.FEE_RATE_INCREASE)
    )
    over = sum(f.amount.paise for f in close.findings if str(f.cause) == Cause.FEE_RATE_INCREASE)
    contract = fees - over
    return Week(
        start=start, end=end, gross_paise=gross, gap_paise=close.gap.paise,
        totals=insight.totals, fee_rate=(fees / gross) if gross else 0.0,
        contract_rate=(contract / gross) if gross else 0.0,
        overcharge_paise=over, flagged=sum(1 for f in close.findings if f.alarming),
    )
