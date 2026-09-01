
from __future__ import annotations

import pytest

from residual.ledger import select
from residual.ledger.events import (
    BankChargeApplied,
    BankCreditReceived,
    BankDebit,
    DisputeOpened,
    DisputeResolved,
    GatewayAdjustment,
    PaymentCaptured,
    RefundIssued,
    RiskHoldApplied,
    RiskHoldReleased,
    RouteTransfer,
    SettlementExecuted,
)
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate


@pytest.fixture(scope="module")
def events():
    return simulate(BENCHMARK).log.events()


@pytest.mark.parametrize(
    "selector,kind",
    [
        (select.captures, PaymentCaptured),
        (select.settlements, SettlementExecuted),
        (select.credits, BankCreditReceived),
        (select.refunds, RefundIssued),
        (select.disputes, DisputeOpened),
        (select.resolutions, DisputeResolved),
        (select.holds, RiskHoldApplied),
        (select.releases, RiskHoldReleased),
        (select.transfers, RouteTransfer),
    ],
)
def test_each_selector_returns_only_its_own_kind(events, selector, kind) -> None:
    picked = list(selector(events))
    assert picked, f"{kind.__name__} never occurs in the benchmark; this proves nothing"
    assert all(isinstance(e, kind) for e in picked)


def test_selectors_agree_with_the_type_tag(events) -> None:
    by_tag = sum(1 for e in events if e.type == "payment_captured")
    assert len(list(select.captures(events))) == by_tag


def test_bank_outflows_covers_both_kinds_of_debit(events) -> None:
    from datetime import date

    from residual.ledger.money import Money

    extra = [
        BankDebit(
            event_id="bd1", occurred_at=date(2026, 1, 1), recorded_at=date(2026, 1, 1),
            bank_txn_id="btx_x", amount=Money.parse("100"), narration="VENDOR",
        )
    ]
    picked = list(select.bank_outflows([*events, *extra]))
    assert any(isinstance(e, BankChargeApplied) for e in picked)
    assert any(isinstance(e, BankDebit) for e in picked)


def test_adjustments_are_selectable(events) -> None:
    from datetime import date

    from residual.ledger.money import Money

    extra = GatewayAdjustment(
        event_id="ga1", occurred_at=date(2026, 1, 1), recorded_at=date(2026, 1, 1),
        adjustment_id="adj_1", amount=Money.parse("500"), reason="clawback",
    )
    assert list(select.adjustments([*events, extra])) == [extra]


def test_selectors_are_lazy(events) -> None:
    from itertools import islice

    assert len(list(islice(select.captures(events), 3))) == 3


def test_an_empty_stream_yields_nothing() -> None:
    assert list(select.captures([])) == []
