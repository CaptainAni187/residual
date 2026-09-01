
from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from residual.ledger.money import Money


class Method(StrEnum):
    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"
    WALLET = "wallet"
    EMI = "emi"


class HoldReason(StrEnum):

    RISK_REVIEW = "risk_review"
    KYC_PENDING = "kyc_pending"
    NEGATIVE_BALANCE = "negative_balance"


class EventBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str

    event_id: str
    occurred_at: date
    recorded_at: date
    seq: int = 0

    @property
    def lag_days(self) -> int:
        return (self.recorded_at - self.occurred_at).days


class OrderPlaced(EventBase):
    type: Literal["order_placed"] = "order_placed"
    order_id: str
    gross: Money
    customer_id: str


class PaymentCaptured(EventBase):
    type: Literal["payment_captured"] = "payment_captured"
    payment_id: str
    order_id: str
    gross: Money
    method: Method
    fee: Money
    tax: Money
    tds: Money = Money(0)
    card_network: str | None = None


class PaymentFailed(EventBase):
    type: Literal["payment_failed"] = "payment_failed"
    payment_id: str
    order_id: str
    gross: Money
    method: Method
    error_code: str


class RefundIssued(EventBase):
    type: Literal["refund_issued"] = "refund_issued"
    refund_id: str
    payment_id: str
    amount: Money

    speed: Literal["normal", "instant"] = "normal"


class DisputeOpened(EventBase):
    type: Literal["dispute_opened"] = "dispute_opened"
    dispute_id: str
    payment_id: str
    amount: Money
    reason_code: str


class DisputeResolved(EventBase):
    type: Literal["dispute_resolved"] = "dispute_resolved"
    dispute_id: str
    payment_id: str
    amount: Money
    won: bool


class RiskHoldApplied(EventBase):
    type: Literal["risk_hold_applied"] = "risk_hold_applied"
    hold_id: str
    amount: Money
    reason: HoldReason


class RiskHoldReleased(EventBase):
    type: Literal["risk_hold_released"] = "risk_hold_released"
    hold_id: str
    amount: Money


class SettlementExecuted(EventBase):

    type: Literal["settlement_executed"] = "settlement_executed"
    settlement_id: str
    utr: str
    net: Money
    covers: tuple[str, ...]
    instant: bool = False
    instant_fee: Money = Money(0)


class BankCreditReceived(EventBase):
    type: Literal["bank_credit_received"] = "bank_credit_received"
    bank_txn_id: str
    amount: Money
    narration: str
    value_date: date


class BankChargeApplied(EventBase):
    type: Literal["bank_charge_applied"] = "bank_charge_applied"
    bank_txn_id: str
    amount: Money
    narration: str


class BankDebit(EventBase):

    type: Literal["bank_debit"] = "bank_debit"
    bank_txn_id: str
    amount: Money
    narration: str


class GatewayAdjustment(EventBase):

    type: Literal["gateway_adjustment"] = "gateway_adjustment"
    adjustment_id: str
    amount: Money
    reason: str = ""


class RouteTransfer(EventBase):
    type: Literal["route_transfer"] = "route_transfer"
    transfer_id: str
    payment_id: str
    amount: Money
    to_account: str


class FeeScheduleChanged(EventBase):

    type: Literal["fee_schedule_changed"] = "fee_schedule_changed"
    method: Method
    old_rate: str
    new_rate: str
    effective_from: date


Event = Annotated[
    OrderPlaced
    | PaymentCaptured
    | PaymentFailed
    | RefundIssued
    | DisputeOpened
    | DisputeResolved
    | RiskHoldApplied
    | RiskHoldReleased
    | SettlementExecuted
    | BankCreditReceived
    | BankChargeApplied
    | BankDebit
    | GatewayAdjustment
    | RouteTransfer
    | FeeScheduleChanged,
    Field(discriminator="type"),
]

EVENT_TYPES = {
    "order_placed": OrderPlaced,
    "payment_captured": PaymentCaptured,
    "payment_failed": PaymentFailed,
    "refund_issued": RefundIssued,
    "dispute_opened": DisputeOpened,
    "dispute_resolved": DisputeResolved,
    "risk_hold_applied": RiskHoldApplied,
    "risk_hold_released": RiskHoldReleased,
    "settlement_executed": SettlementExecuted,
    "bank_credit_received": BankCreditReceived,
    "bank_charge_applied": BankChargeApplied,
    "bank_debit": BankDebit,
    "gateway_adjustment": GatewayAdjustment,
    "route_transfer": RouteTransfer,
    "fee_schedule_changed": FeeScheduleChanged,
}
