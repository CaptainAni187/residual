
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from residual.ledger import events as ev
from residual.ledger.accounts import Account as A
from residual.ledger.posting import Entry, Posting, credit, debit


def project(event: ev.EventBase) -> Entry:
    fn = _DISPATCH.get(event.type)
    postings: tuple[Posting, ...] = fn(event) if fn else ()
    return Entry(
        event_id=event.event_id,
        event_type=event.type,
        occurred_at=event.occurred_at,
        recorded_at=event.recorded_at,
        postings=postings,
    )


def _captured(e: ev.PaymentCaptured) -> tuple[Posting, ...]:
    net = e.gross - e.fee - e.tax - e.tds
    return (
        debit(A.GATEWAY_RECEIVABLE, net, e.payment_id, "net receivable"),
        debit(A.FEE_EXPENSE, e.fee, e.payment_id, f"{e.method} fee"),
        debit(A.GST_INPUT_CREDIT, e.tax, e.payment_id, "GST on fee"),
        debit(A.TDS_RECEIVABLE, e.tds, e.payment_id, "sec 194-O"),
        credit(A.REVENUE, e.gross, e.payment_id, e.order_id),
    )


def _refunded(e: ev.RefundIssued) -> tuple[Posting, ...]:
    return (
        debit(A.REFUNDS, e.amount, e.refund_id, f"refund of {e.payment_id}"),
        credit(A.GATEWAY_RECEIVABLE, e.amount, e.refund_id, e.payment_id),
    )


def _dispute_opened(e: ev.DisputeOpened) -> tuple[Posting, ...]:
    return (
        debit(A.DISPUTE_RESERVE, e.amount, e.dispute_id, f"reserve, {e.reason_code}"),
        credit(A.GATEWAY_RECEIVABLE, e.amount, e.dispute_id, e.payment_id),
    )


def _dispute_resolved(e: ev.DisputeResolved) -> tuple[Posting, ...]:
    if e.won:
        return (
            debit(A.GATEWAY_RECEIVABLE, e.amount, e.dispute_id, "reserve released"),
            credit(A.DISPUTE_RESERVE, e.amount, e.dispute_id, e.payment_id),
        )
    return (
        debit(A.CHARGEBACK_LOSS, e.amount, e.dispute_id, "dispute lost"),
        credit(A.DISPUTE_RESERVE, e.amount, e.dispute_id, e.payment_id),
    )


def _hold_applied(e: ev.RiskHoldApplied) -> tuple[Posting, ...]:
    return (
        debit(A.ON_HOLD, e.amount, e.hold_id, e.reason),
        credit(A.GATEWAY_RECEIVABLE, e.amount, e.hold_id, "held"),
    )


def _hold_released(e: ev.RiskHoldReleased) -> tuple[Posting, ...]:
    return (
        debit(A.GATEWAY_RECEIVABLE, e.amount, e.hold_id, "hold released"),
        credit(A.ON_HOLD, e.amount, e.hold_id, "released"),
    )


def _settled(e: ev.SettlementExecuted) -> tuple[Posting, ...]:
    return (
        debit(A.SETTLEMENT_IN_TRANSIT, e.net, e.utr, e.settlement_id),
        debit(A.FEE_EXPENSE, e.instant_fee, e.settlement_id, "instant settlement fee"),
        credit(A.GATEWAY_RECEIVABLE, e.net + e.instant_fee, e.settlement_id, e.utr),
    )


def _bank_credit(e: ev.BankCreditReceived) -> tuple[Posting, ...]:
    return (
        debit(A.BANK, e.amount, e.bank_txn_id, e.narration),
        credit(A.SETTLEMENT_IN_TRANSIT, e.amount, e.bank_txn_id, "credit confirmed"),
    )


def _bank_charge(e: ev.BankChargeApplied) -> tuple[Posting, ...]:
    return (
        debit(A.BANK_CHARGES, e.amount, e.bank_txn_id, e.narration),
        credit(A.BANK, e.amount, e.bank_txn_id, "debited by bank"),
    )


def _bank_debit(e: ev.BankDebit) -> tuple[Posting, ...]:
    return (
        debit(A.OTHER_OUTFLOW, e.amount, e.bank_txn_id, e.narration),
        credit(A.BANK, e.amount, e.bank_txn_id, "paid out of the bank"),
    )


def _adjustment(e: ev.GatewayAdjustment) -> tuple[Posting, ...]:
    return (
        debit(A.GATEWAY_ADJUSTMENTS, e.amount, e.adjustment_id, e.reason),
        credit(A.GATEWAY_RECEIVABLE, e.amount, e.adjustment_id, "netted off the payout"),
    )


def _route(e: ev.RouteTransfer) -> tuple[Posting, ...]:
    return (
        debit(A.REVENUE, e.amount, e.transfer_id, f"split to {e.to_account}"),
        credit(A.GATEWAY_RECEIVABLE, e.amount, e.transfer_id, e.payment_id),
    )


_DISPATCH: dict[str, Callable[[Any], tuple[Posting, ...]]] = {
    "payment_captured": _captured,
    "refund_issued": _refunded,
    "dispute_opened": _dispute_opened,
    "dispute_resolved": _dispute_resolved,
    "risk_hold_applied": _hold_applied,
    "risk_hold_released": _hold_released,
    "settlement_executed": _settled,
    "bank_credit_received": _bank_credit,
    "bank_charge_applied": _bank_charge,
    "gateway_adjustment": _adjustment,
    "bank_debit": _bank_debit,
    "route_transfer": _route,
}
