from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from residual.explain.close import run_close
from residual.explain.insights import analyse, bucket_of, gst_claim_deadline, weeks_between
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate


@pytest.fixture(scope="module")
def world():
    r = simulate(BENCHMARK)
    events = r.log.events()
    return r, events, Warehouse.build(events), {str(m): x for m, x in BENCHMARK.base_rates}


def _close(world, start, end):
    _, events, wh, rates = world
    return run_close(events, start, end, rates, wh)


def test_the_buckets_add_up_to_the_whole_gap_every_week(world):
    r = world[0]
    for s, e in weeks_between(r.start, r.start + timedelta(days=BENCHMARK.days - 1)):
        close = _close(world, s, e)
        assert sum(analyse(close, s, e).totals.values()) == close.gap.paise


def test_a_missing_payout_is_the_first_thing_to_do(world):
    s = world[0].start + timedelta(days=56)
    found = analyse(_close(world, s, s + timedelta(days=6)), s, s + timedelta(days=6))
    assert found.actions[0].cause == "settlement_never_arrived"
    assert found.actions[0].urgency == "high"
    assert found.actions[0].draft == "payout_trace"


def test_released_money_is_timing_not_something_to_chase(world):
    s = world[0].start + timedelta(days=56)
    close = _close(world, s, s + timedelta(days=6))
    hold = next(f for f in close.findings if str(f.cause) == "risk_hold")
    assert hold.amount.paise < 0
    assert bucket_of(hold) == "timing"


def test_recoverable_is_only_chase_and_tax(world):
    s = world[0].start + timedelta(days=56)
    close = _close(world, s, s + timedelta(days=6))
    found = analyse(close, s, s + timedelta(days=6))
    expected = sum(f.amount.paise for f in close.findings if bucket_of(f) in ("chase", "tax") and f.amount.paise > 0)
    assert found.recoverable_paise == expected


@pytest.mark.parametrize(
    ("on", "deadline"),
    [(date(2026, 1, 5), date(2026, 11, 30)), (date(2026, 3, 31), date(2026, 11, 30)),
     (date(2026, 4, 1), date(2027, 11, 30)), (date(2025, 12, 31), date(2026, 11, 30))],
)
def test_gst_credit_deadline_follows_the_financial_year(on, deadline):
    assert gst_claim_deadline(on) == deadline


def test_the_landing_page_number_is_still_true():
    from residual.eval.sizing import OUT, measure

    assert json.loads(Path(OUT).read_text()) == measure()
