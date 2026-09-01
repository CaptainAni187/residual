
from __future__ import annotations

from collections.abc import Iterable, Iterator

from residual.ledger.events import (
    BankChargeApplied,
    BankCreditReceived,
    BankDebit,
    DisputeOpened,
    DisputeResolved,
    EventBase,
    GatewayAdjustment,
    PaymentCaptured,
    RefundIssued,
    RiskHoldApplied,
    RiskHoldReleased,
    RouteTransfer,
    SettlementExecuted,
)


def captures(events: Iterable[EventBase]) -> Iterator[PaymentCaptured]:
    for event in events:
        if isinstance(event, PaymentCaptured):
            yield event


def settlements(events: Iterable[EventBase]) -> Iterator[SettlementExecuted]:
    for event in events:
        if isinstance(event, SettlementExecuted):
            yield event


def credits(events: Iterable[EventBase]) -> Iterator[BankCreditReceived]:
    for event in events:
        if isinstance(event, BankCreditReceived):
            yield event


def refunds(events: Iterable[EventBase]) -> Iterator[RefundIssued]:
    for event in events:
        if isinstance(event, RefundIssued):
            yield event


def disputes(events: Iterable[EventBase]) -> Iterator[DisputeOpened]:
    for event in events:
        if isinstance(event, DisputeOpened):
            yield event


def resolutions(events: Iterable[EventBase]) -> Iterator[DisputeResolved]:
    for event in events:
        if isinstance(event, DisputeResolved):
            yield event


def holds(events: Iterable[EventBase]) -> Iterator[RiskHoldApplied]:
    for event in events:
        if isinstance(event, RiskHoldApplied):
            yield event


def releases(events: Iterable[EventBase]) -> Iterator[RiskHoldReleased]:
    for event in events:
        if isinstance(event, RiskHoldReleased):
            yield event


def transfers(events: Iterable[EventBase]) -> Iterator[RouteTransfer]:
    for event in events:
        if isinstance(event, RouteTransfer):
            yield event


def adjustments(events: Iterable[EventBase]) -> Iterator[GatewayAdjustment]:
    for event in events:
        if isinstance(event, GatewayAdjustment):
            yield event


def bank_outflows(
    events: Iterable[EventBase],
) -> Iterator[BankChargeApplied | BankDebit]:
    for event in events:
        if isinstance(event, BankChargeApplied | BankDebit):
            yield event
