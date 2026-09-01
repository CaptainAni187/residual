
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import ClassVar

from residual.domain.causes import Cause
from residual.ledger.accounts import Account
from residual.ledger.events import Method
from residual.ledger.money import Money
from residual.ledger.warehouse import Warehouse


@dataclass(frozen=True, slots=True)
class Evidence:
    supported: bool
    amount: Money
    sql: str
    entity_ids: tuple[str, ...] = ()
    note: str = ""

    @property
    def material(self) -> bool:
        return self.supported and self.amount.paise != 0


REGISTRY: dict[Cause, type[Hypothesis]] = {}


class Hypothesis:

    cause: ClassVar[Cause]
    title: ClassVar[str]
    alarming: ClassVar[bool] = False

    accounts: ClassVar[frozenset[Account]] = frozenset()

    refines: ClassVar[Cause | None] = None

    def __init_subclass__(cls, **kw: object) -> None:
        super().__init_subclass__(**kw)
        if getattr(cls, "cause", None) is not None:
            REGISTRY[cls.cause] = cls

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        raise NotImplementedError


    @staticmethod
    def _sum_postings(
        wh: Warehouse, account: str, start: date, end: date, event_type: str | None = None
    ) -> tuple[Money, str]:
        clause = " AND event_type = ?" if event_type else ""
        q = (
            "SELECT COALESCE(SUM(amount_paise), 0) FROM postings "
            "WHERE account = ? AND occurred_at BETWEEN ? AND ?" + clause
        )
        params: list[object] = [account, start, end]
        if event_type:
            params.append(event_type)
        return wh.scalar_money(q, params), wh.rendered(q, params)

    @staticmethod
    def _refs(wh: Warehouse, account: str, start: date, end: date, limit: int = 25) -> tuple[str, ...]:
        rows = wh.sql(
            "SELECT DISTINCT ref FROM postings WHERE account = ? "
            "AND occurred_at BETWEEN ? AND ? AND ref <> '' ORDER BY ref LIMIT ?",
            [account, start, end, limit],
        )
        return tuple(r[0] for r in rows)


class UnknownMethod(Exception):
    pass


def _priced_at(contracted: dict[str, str]) -> tuple[str, list[object]]:
    known = {str(m) for m in Method}
    unknown = sorted(set(contracted) - known)
    if unknown:
        raise UnknownMethod(
            f"contract names instrument(s) this ledger does not record: {unknown}; "
            f"known instruments are {sorted(known)}"
        )

    clauses: list[str] = []
    params: list[object] = []
    for method, rate in contracted.items():
        Decimal(rate)
        clauses.append(
            "WHEN method = ? THEN CAST(ROUND(CAST(amount_paise AS DECIMAL(38,6))"
            " * CAST(? AS DECIMAL(12,6)) / 100, 0) AS BIGINT)"
        )
        params += [method, rate]
    return " ".join(clauses), params


def _no_contract() -> Evidence:
    return Evidence(
        supported=False,
        amount=Money.zero(),
        sql="-- no contracted fee schedule supplied; nothing to compare against",
        note="pass the merchant's rates to price fees against their contract",
    )


class GatewayFees(Hypothesis):
    cause = Cause.NORMAL_FEE
    title = "Gateway fees at the contracted rate"
    accounts = frozenset({Account.FEE_EXPENSE})

    def __init__(self, contracted: dict[str, str]) -> None:
        self.contracted = contracted

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        if not self.contracted:
            return _no_contract()
        cases, rate_params = _priced_at(self.contracted)
        q = (
            f"SELECT COALESCE(SUM(CASE {cases} ELSE 0 END), 0) FROM events "
            "WHERE type = 'payment_captured' AND occurred_at BETWEEN ? AND ?"
        )
        params = [*rate_params, start, end]
        amount = wh.scalar_money(q, params)
        return Evidence(
            supported=amount.paise > 0,
            amount=amount,
            sql=wh.rendered(q, params),
            note="priced at the contracted schedule, not at what was billed",
        )


class FeeRateChange(Hypothesis):
    cause = Cause.FEE_RATE_INCREASE
    title = "Fees billed above the contracted rate"
    accounts = frozenset({Account.FEE_EXPENSE})
    alarming = True

    def __init__(self, contracted: dict[str, str]) -> None:
        self.contracted = contracted

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        if not self.contracted:
            return _no_contract()
        cases, rate_params = _priced_at(self.contracted)
        q = (
            f"SELECT COALESCE(SUM(fee_paise - (CASE {cases} ELSE 0 END)), 0) FROM events "
            "WHERE type = 'payment_captured' AND occurred_at BETWEEN ? AND ?"
        )
        params = [*rate_params, start, end]
        amount = wh.scalar_money(q, params)
        if amount.paise == 0:
            return Evidence(False, Money.zero(), wh.rendered(q, params),
                            note="every capture was billed at the contracted rate")

        detail = wh.sql(
            "SELECT method, COUNT(*), "
            f"SUM(fee_paise - (CASE {cases} ELSE 0 END))/100.0 FROM events "
            "WHERE type = 'payment_captured' AND occurred_at BETWEEN ? AND ? "
            f"GROUP BY 1 HAVING SUM(fee_paise - (CASE {cases} ELSE 0 END)) <> 0 "
            "ORDER BY 1",
            [*rate_params, start, end, *rate_params],
        )
        worst = ", ".join(f"{m}: +INR {d:,.2f} over {n} captures" for m, n, d in detail)
        return Evidence(
            supported=True, amount=amount, sql=wh.rendered(q, params),
            entity_ids=tuple(m for m, _, _ in detail),
            note=f"billed above contract -- {worst}",
        )


class GstOnFees(Hypothesis):
    cause = Cause.GST_ON_FEE
    title = "GST charged on gateway fees"
    accounts = frozenset({Account.GST_INPUT_CREDIT})

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        amount, sql = self._sum_postings(wh, "gst_input_credit", start, end)
        return Evidence(amount.paise != 0, amount, sql,
                        note="recoverable as input credit, but it is cash today")


class Tds194O(Hypothesis):
    cause = Cause.TDS_194O
    title = "TDS withheld under section 194-O"
    accounts = frozenset({Account.TDS_RECEIVABLE})

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        amount, sql = self._sum_postings(wh, "tds_receivable", start, end)
        return Evidence(amount.paise != 0, amount, sql, note="creditable against tax liability")


class InstantSettlementFees(Hypothesis):
    cause = Cause.INSTANT_SETTLEMENT_FEE
    title = "Instant settlement fees"
    accounts = frozenset({Account.FEE_EXPENSE})

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        q = (
            "SELECT COALESCE(SUM(fee_paise), 0) FROM events "
            "WHERE type = 'settlement_executed' AND occurred_at BETWEEN ? AND ?"
        )
        amount = wh.scalar_money(q, [start, end])
        return Evidence(amount.paise != 0, amount, wh.rendered(q, [start, end]),
                        note="the cost of not waiting for T+2")


class BankCharges(Hypothesis):
    cause = Cause.BANK_CHARGES
    title = "Charges debited by the bank"
    accounts = frozenset({Account.BANK_CHARGES})

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        amount, sql = self._sum_postings(wh, "bank_charges", start, end)
        return Evidence(amount.paise != 0, amount, sql)


class RefundsIssued(Hypothesis):
    cause = Cause.REFUNDS_ISSUED
    title = "Refunds issued to customers"
    accounts = frozenset({Account.REFUNDS})

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        amount, sql = self._sum_postings(wh, "refunds", start, end)
        return Evidence(amount.paise != 0, amount, sql,
                        entity_ids=self._refs(wh, "refunds", start, end))


class DisputeReserveHeld(Hypothesis):
    cause = Cause.DISPUTE_RESERVE_HELD
    title = "Withheld against open disputes"
    accounts = frozenset({Account.DISPUTE_RESERVE})

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        amount, sql = self._sum_postings(wh, "dispute_reserve", start, end)
        return Evidence(
            amount.paise != 0, amount, sql,
            entity_ids=self._refs(wh, "dispute_reserve", start, end),
            note="held pending resolution; recoverable if the dispute is won",
        )


class ChargebackLost(Hypothesis):
    cause = Cause.CHARGEBACK_LOST
    title = "Disputes lost outright"
    accounts = frozenset({Account.CHARGEBACK_LOSS})
    alarming = True

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        amount, sql = self._sum_postings(wh, "chargeback_loss", start, end)
        return Evidence(amount.paise != 0, amount, sql,
                        entity_ids=self._refs(wh, "chargeback_loss", start, end),
                        note="gone; not recoverable")


class GatewayAdjustments(Hypothesis):
    cause = Cause.GATEWAY_ADJUSTMENT
    title = "Clawed back by the gateway outside the fee schedule"
    accounts = frozenset({Account.GATEWAY_ADJUSTMENTS})
    alarming = True

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        amount, sql = self._sum_postings(wh, "gateway_adjustments", start, end)
        return Evidence(
            amount.paise != 0, amount, sql,
            entity_ids=self._refs(wh, "gateway_adjustments", start, end),
            note="netted off a payout rather than billed -- worth querying",
        )


class OtherBankOutflow(Hypothesis):
    cause = Cause.OTHER_BANK_OUTFLOW
    title = "Paid out of the bank for something else"
    accounts = frozenset({Account.OTHER_OUTFLOW})

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        amount, sql = self._sum_postings(wh, "other_outflow", start, end)
        return Evidence(
            amount.paise != 0, amount, sql,
            entity_ids=self._refs(wh, "other_outflow", start, end),
            note="the merchant's own spending -- recorded so the balance ties, "
                 "not counted against the gateway",
        )


class RouteSplit(Hypothesis):
    cause = Cause.ROUTE_SPLIT
    title = "Split to linked accounts via Route"
    accounts = frozenset({Account.REVENUE})

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        q = (
            "SELECT COALESCE(SUM(amount_paise), 0) FROM events "
            "WHERE type = 'route_transfer' AND occurred_at BETWEEN ? AND ?"
        )
        amount = wh.scalar_money(q, [start, end])
        return Evidence(amount.paise != 0, amount, wh.rendered(q, [start, end]),
                        note="never the merchant's money to receive")


class RiskHoldPlaced(Hypothesis):
    cause = Cause.RISK_HOLD
    title = "Frozen by a risk hold"
    accounts = frozenset({Account.ON_HOLD})
    alarming = True

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        amount, sql = self._sum_postings(wh, "on_hold", start, end)
        if amount.paise == 0:
            return Evidence(False, amount, sql, note="no net change in held funds")
        direction = "placed" if amount.paise > 0 else "released"
        return Evidence(True, amount, sql,
                        entity_ids=self._refs(wh, "on_hold", start, end),
                        note=f"funds {direction} during the window")


class CapturedNotYetSettled(Hypothesis):
    cause = Cause.CAPTURED_NOT_YET_SETTLED
    title = "Captured in the window, settles after it"
    accounts = frozenset({Account.GATEWAY_RECEIVABLE})

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        amount, sql = self._sum_postings(wh, "gateway_receivable", start, end)
        return Evidence(amount.paise != 0, amount, sql,
                        note="ordinary T+2 timing, not a loss")


class SettlementInFlight(Hypothesis):
    cause = Cause.SETTLEMENT_IN_FLIGHT
    title = "Gateway has sent it, bank has not confirmed"
    accounts = frozenset({Account.SETTLEMENT_IN_TRANSIT})

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        amount, sql = self._sum_postings(wh, "settlement_in_transit", start, end)
        return Evidence(amount.paise != 0, amount, sql)


class BankHolidayDelay(Hypothesis):
    cause = Cause.BANK_HOLIDAY_DELAY
    title = "Held back because the bank was shut"
    accounts = frozenset({Account.GATEWAY_RECEIVABLE})
    refines = Cause.CAPTURED_NOT_YET_SETTLED

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        q = (
            "WITH delayed AS ("
            "  SELECT p.ref AS payment_id, p.amount_paise"
            "  FROM postings p JOIN calendar c ON c.d = p.occurred_at"
            "  WHERE p.account = 'gateway_receivable'"
            "    AND p.event_type = 'payment_captured'"
            "    AND p.occurred_at BETWEEN ? AND ?"
            "    AND c.t2_naive <= ? AND c.t2_actual > ?"
            ") SELECT COALESCE((SELECT SUM(amount_paise) FROM delayed), 0)"
            "       - COALESCE((SELECT SUM(amount_paise) FROM events"
            "                   WHERE type = 'route_transfer'"
            "                     AND counterparty IN (SELECT payment_id FROM delayed)), 0)"
        )
        params = [start, end, end, end]
        amount = wh.scalar_money(q, params)
        if amount.paise == 0:
            return Evidence(False, amount, wh.rendered(q, params),
                            note="no payout in this window was pushed out by the calendar")

        days = wh.sql(
            "SELECT DISTINCT c.t2_naive FROM calendar c "
            "WHERE c.d BETWEEN ? AND ? AND c.t2_actual > ? AND c.t2_naive <= ? "
            "ORDER BY 1",
            [start, end, end, end],
        )
        closed = ", ".join(str(d[0]) for d in days)
        return Evidence(
            True, amount, wh.rendered(q, params),
            entity_ids=tuple(str(d[0]) for d in days),
            note=f"T+2 fell on a non-banking day ({closed}); late, not lost",
        )


class SettlementNeverArrived(Hypothesis):
    cause = Cause.SETTLEMENT_NEVER_ARRIVED
    title = "Executed by the gateway, never credited by the bank"
    accounts = frozenset({Account.SETTLEMENT_IN_TRANSIT})
    alarming = True
    refines = Cause.SETTLEMENT_IN_FLIGHT

    def __init__(self, stale_after_days: int = 3) -> None:
        self.stale_after_days = stale_after_days

    def verify(self, wh: Warehouse, start: date, end: date) -> Evidence:
        cutoff = end - timedelta(days=self.stale_after_days)
        q = (
            "SELECT COALESCE(SUM(s.amount_paise), 0), COALESCE(STRING_AGG(s.utr, ',' ORDER BY s.utr), '') "
            "FROM events s "
            "LEFT JOIN credit_links cl ON cl.settlement_id = s.entity_id "
            "WHERE s.type = 'settlement_executed' "
            "  AND s.occurred_at BETWEEN ? AND ? "
            "  AND s.occurred_at <= ? "
            "  AND cl.settlement_id IS NULL "
            "  AND NOT EXISTS ("
            "     SELECT 1 FROM credit_links a "
            "     JOIN events b ON b.entity_id = a.bank_txn_id "
            "     WHERE a.settlement_id IS NULL "
            "       AND b.amount_paise = s.amount_paise "
            "       AND b.occurred_at BETWEEN s.occurred_at - INTERVAL 6 DAY "
            "                             AND s.occurred_at + INTERVAL 6 DAY)"
        )
        params = [start, end, cutoff]
        row = wh.sql(q, params)[0]
        amount = Money(int(row[0]))
        if amount.paise == 0:
            return Evidence(False, amount, wh.rendered(q, params),
                            note="every settlement in the window was accounted for")
        return Evidence(
            True, amount, wh.rendered(q, params),
            entity_ids=tuple(u for u in row[1].split(",") if u),
            note=f"no credit links to these UTRs more than {self.stale_after_days} days "
                 f"after execution, and no unplaced credit could be them -- escalate",
        )
