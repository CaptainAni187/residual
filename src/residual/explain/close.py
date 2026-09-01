
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from residual.domain.causes import ALARMING, PERMANENT, Cause
from residual.explain import tax
from residual.explain.hypotheses import (
    REGISTRY,
    Evidence,
    FeeRateChange,
    GatewayFees,
    Hypothesis,
    SettlementNeverArrived,
)
from residual.explain.tax import Risk
from residual.ingest.gst import Return as GstReturn
from residual.ledger.accounts import Account
from residual.ledger.events import EventBase
from residual.ledger.money import Money, total
from residual.ledger.warehouse import Warehouse
from residual.position.engine import Variance, decompose


@dataclass(frozen=True, slots=True)
class Finding:
    cause: Cause
    title: str
    amount: Money
    evidence: Evidence
    alarming: bool = False

    @property
    def permanent(self) -> bool:
        return self.cause in PERMANENT

    def __str__(self) -> str:
        return f"{self.cause}: {self.amount}"


@dataclass(frozen=True, slots=True)
class Coverage:

    account: Account
    claimed: Money
    actual: Money

    @property
    def ok(self) -> bool:
        return self.claimed.paise == self.actual.paise

    @property
    def drift(self) -> Money:
        return self.claimed - self.actual


@dataclass(frozen=True, slots=True)
class Unresolved:

    kind: str
    detail: str
    amount: Money
    entity_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class Close:
    window: tuple[date, date]
    as_of: date | None
    variance: Variance
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0
    coverage: list[Coverage] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)

    risks: list[Risk] = field(default_factory=list)

    @property
    def at_risk(self) -> Money:
        return total(r.amount for r in self.risks)

    @property
    def unresolved_value(self) -> Money:
        return total(u.amount for u in self.unresolved)

    @property
    def fully_covered(self) -> bool:
        return all(c.ok for c in self.coverage)

    @property
    def coverage_gaps(self) -> list[Coverage]:
        return [c for c in self.coverage if not c.ok]

    @property
    def gap(self) -> Money:
        return self.variance.gap

    @property
    def explained(self) -> Money:
        return total(f.amount for f in self.findings)

    @property
    def residual(self) -> Money:
        return self.gap - self.explained

    @property
    def closes(self) -> bool:
        return self.residual.paise == 0

    @property
    def explained_fraction(self) -> float:
        if self.gap.paise == 0:
            return 1.0
        return 1 - abs(self.residual.paise) / abs(self.gap.paise)

    @property
    def alarms(self) -> list[Finding]:
        return [f for f in self.findings if f.alarming]

    @property
    def permanent_loss(self) -> Money:
        return total(f.amount for f in self.findings if f.permanent)

    def by_cause(self) -> dict[Cause, Money]:
        return {f.cause: f.amount for f in self.findings}


def default_hypotheses(contracted: dict[str, str]) -> list[Hypothesis]:
    parameterised: dict[Cause, Hypothesis] = {
        Cause.NORMAL_FEE: GatewayFees(contracted),
        Cause.FEE_RATE_INCREASE: FeeRateChange(contracted),
        Cause.SETTLEMENT_NEVER_ARRIVED: SettlementNeverArrived(),
    }
    return [
        parameterised.get(cause) or cls()
        for cause, cls in REGISTRY.items()
    ]


def refine(results: dict[Cause, tuple[Hypothesis, Evidence]]) -> dict[Cause, Money]:
    amounts = {cause: evidence.amount for cause, (_, evidence) in results.items()}
    for cause, (h, _) in results.items():
        if not (h.refines and amounts[cause].paise):
            continue
        if h.refines not in amounts:
            continue
        amounts[h.refines] = amounts[h.refines] - amounts[cause]
    return amounts


def run_close(
    events: list[EventBase],
    start: date,
    end: date,
    contracted: dict[str, str],
    warehouse: Warehouse | None = None,
    hypotheses: list[Hypothesis] | None = None,
    known_by: date | None = None,
    gstr2b: GstReturn | None = None,
    form_26as: Money | None = None,
) -> Close:
    if known_by is not None:
        events = [e for e in events if e.recorded_at <= known_by]
        wh = Warehouse.build(events)
    else:
        wh = warehouse or Warehouse.build(events)
    _ensure_links(wh)
    hyps = hypotheses if hypotheses is not None else default_hypotheses(contracted)

    results: dict[Cause, tuple[Hypothesis, Evidence]] = {}
    for h in hyps:
        results[h.cause] = (h, h.verify(wh, start, end))

    amounts = refine(results)

    findings = [
        Finding(
            cause=cause,
            title=h.title,
            amount=amounts[cause],
            evidence=evidence,
            alarming=h.alarming or cause in ALARMING,
        )
        for cause, (h, evidence) in results.items()
        if evidence.supported and amounts[cause].paise
    ]
    findings.sort(key=lambda f: -abs(f.amount.paise))

    variance = decompose(events, start, end)
    moved = {m.account: m.amount for m in variance.movements}
    touched = {a for h in hyps for a in h.accounts} | set(moved)
    coverage = [
        Coverage(
            account=account,
            claimed=total(
                amounts[cause] for cause, (h, _) in results.items() if account in h.accounts
            ),
            actual=moved.get(account, Money.zero()),
        )
        for account in sorted(touched)
    ]

    return Close(
        window=(start, end),
        as_of=known_by,
        variance=variance,
        findings=findings,
        checked=len(hyps),
        coverage=coverage,
        unresolved=_abstentions(wh, start, end),
        risks=(
            tax.assess(list(events), start, end, gstr2b=gstr2b, form_26as=form_26as)
            if gstr2b is not None or form_26as is not None
            else []
        ),
    )


def _ensure_links(wh: Warehouse) -> None:
    from residual.recon.linkage import link_credits, load_links

    if not wh.links_loaded:
        with wh.lock:
            if not wh.links_loaded:
                load_links(wh, link_credits(wh))


def _abstentions(wh: Warehouse, start: date, end: date) -> list[Unresolved]:
    rows = wh.sql(
        "SELECT cl.bank_txn_id, cl.reason, e.amount_paise FROM credit_links cl "
        "JOIN events e ON e.entity_id = cl.bank_txn_id "
        "WHERE cl.settlement_id IS NULL AND e.occurred_at BETWEEN ? AND ? "
        "ORDER BY e.amount_paise DESC, cl.bank_txn_id",
        [start, end],
    )
    return [
        Unresolved(
            kind="credit not attributed to a payout",
            detail=reason,
            amount=Money(int(amt)),
            entity_ids=(btx,),
        )
        for btx, reason, amt in rows
    ]
