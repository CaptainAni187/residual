
from __future__ import annotations

import dataclasses

import pytest

from residual.ledger import select
from residual.ledger.money import total
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate

SEEDS = (1, 7, 42, 20260822, 99991)


@pytest.fixture(scope="module")
def year():
    events = []
    for seed in SEEDS:
        config = dataclasses.replace(BENCHMARK, seed=seed, days=365, scenarios=())
        events.extend(simulate(config).log.events())
    return events


def test_the_gateway_fee_matches_the_published_rate_card(year) -> None:
    captures = list(select.captures(year))
    gross = total(c.gross for c in captures)
    fee = total(c.fee for c in captures)
    assert 1.99 <= fee.paise / gross.paise * 100 <= 2.01

    assert all(c.fee.paise > 0 for c in captures if c.gross.paise > 100), (
        "some method is being charged nothing"
    )


def test_gst_is_eighteen_percent_of_the_fee(year) -> None:
    captures = list(select.captures(year))
    fee = total(c.fee for c in captures)
    tax = total(c.tax for c in captures)
    assert abs(tax.paise / fee.paise * 100 - 18.0) < 0.05


def test_the_chargeback_rate_matches_the_published_average(year) -> None:
    cards = [c for c in select.captures(year) if str(c.method) in ("card", "emi")]
    disputes = list(select.disputes(year))
    rate = len(disputes) / len(cards)

    assert 0.004 <= rate <= 0.009, f"{rate:.2%} of card captures"
    assert rate < 0.009, "past Visa's monitoring threshold"


@pytest.mark.parametrize("seed", SEEDS)
def test_disputes_only_reach_card_rails(seed: int) -> None:
    config = dataclasses.replace(BENCHMARK, seed=seed, days=365, scenarios=())
    events = simulate(config).log.events()
    by_payment = {c.payment_id: c for c in select.captures(events)}

    disputes = list(select.disputes(events))
    assert disputes, "no dispute occurred; this proves nothing"
    for dispute in disputes:
        capture = by_payment[dispute.payment_id]
        assert str(capture.method) in ("card", "emi"), str(capture.method)


def test_tds_is_the_current_statutory_rate() -> None:
    assert BENCHMARK.tds_rate == "0.10"
    assert 0 < BENCHMARK.tds_share < 1, "not every seller is within scope"


def test_the_settlement_cycle_is_t_plus_two_working_days() -> None:
    from datetime import date

    from residual.domain.calendar import add_bank_days

    assert BENCHMARK.settlement_lag_days == 2
    assert add_bank_days(date(2026, 1, 23), 2) == date(2026, 1, 28)


def test_upi_dominates_the_mix_without_pretending_to_be_retail(year) -> None:
    captures = list(select.captures(year))
    upi = sum(1 for c in captures if str(c.method) == "upi") / len(captures)
    assert 0.65 < upi < 0.80, f"{upi:.0%}"


def test_the_refund_rate_sits_in_the_published_band(year) -> None:
    captures = list(select.captures(year))
    refunds = list(select.refunds(year))
    assert 0.03 <= len(refunds) / len(captures) <= 0.10
