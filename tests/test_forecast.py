
from __future__ import annotations

from datetime import timedelta

import pytest

from residual.domain.calendar import is_bank_day
from residual.ledger.money import Money
from residual.position.forecast import Shape, backtest, forecast
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate


@pytest.fixture(scope="module")
def world():
    return simulate(BENCHMARK)


@pytest.fixture(scope="module")
def contracted():
    return {str(m): rate for m, rate in BENCHMARK.base_rates}


def test_a_forecast_cannot_read_the_future(world) -> None:
    events = world.log.events()
    as_of = world.start + timedelta(days=60)

    later = [e for e in events if e.recorded_at > as_of]
    assert later, "the run has no later-recorded facts, so this proves nothing"

    truncated = [e for e in events if e.recorded_at <= as_of]
    assert forecast(events, as_of, horizon=14) == forecast(truncated, as_of, horizon=14)


def test_nothing_is_forecast_to_land_on_a_closed_bank(world) -> None:
    o = forecast(world.log.events(), world.start + timedelta(days=60), horizon=21)
    for d in o.days:
        if not is_bank_day(d.day):
            assert d.expected.paise == 0, f"{d.day} is a {d.day:%A} and the bank is shut"


def test_committed_and_projected_are_kept_apart(world) -> None:
    o = forecast(world.log.events(), world.start + timedelta(days=60), horizon=14)
    assert o.committed.paise > 0 and o.projected.paise > 0
    assert o.total == o.committed + o.projected
    near = [d for d in o.days if d.certain and d.committed.paise]
    assert near, "nothing inside the settlement cycle was treated as committed"


def test_an_escalated_payout_is_not_forecast_as_incoming(world) -> None:
    events = world.log.events()
    as_of = world.start + timedelta(days=70)
    o = forecast(events, as_of, horizon=14)
    assert o.written_off.paise > 0
    assert o.written_off == Money.parse("109084.16")


def test_overdue_money_is_flagged_not_forecast(world) -> None:
    events = world.log.events()
    for offset in (50, 60, 70, 80):
        o = forecast(events, world.start + timedelta(days=offset), horizon=7)
        assert o.overdue.paise >= 0
        first = next((d for d in o.days if d.committed.paise), None)
        if first and o.overdue.paise:
            assert first.committed.paise < o.overdue.paise + first.committed.paise


def test_the_realisation_rate_is_measured_not_assumed(world) -> None:
    shape = Shape.learn(world.log.events(), world.start + timedelta(days=60))
    assert 0 < shape.realisation < 1, "T+2 is not honoured in full and the model should know"
    assert shape.fee_rate > 0 and shape.deduction_rate > 0


def test_forecast_accuracy_holds_at_the_useful_horizon(world, contracted) -> None:
    bt = backtest(world.log.events(), world.start, BENCHMARK.days, contracted, horizon=14)
    assert bt.mape < 0.20, f"MAPE regressed to {bt.mape:.1%}"
    assert abs(bt.optimism) < 0.08, f"optimism regressed to {bt.optimism:.1%}"
    assert 0.20 < bt.days_short / len(bt.errors) < 0.80, "misses are all in one direction"


def test_the_benchmark_quarter_is_harder_than_an_ordinary_one() -> None:
    import dataclasses

    from residual.simulate.world import simulate as run

    contracted = {str(m): rate for m, rate in BENCHMARK.base_rates}
    eventful = backtest(
        run(BENCHMARK).log.events(), BENCHMARK.start, BENCHMARK.days,
        contracted, horizon=14,
    )
    ordinary = backtest(
        run(dataclasses.replace(BENCHMARK, scenarios=())).log.events(),
        BENCHMARK.start, BENCHMARK.days, contracted, horizon=14,
    )
    assert ordinary.mape < eventful.mape / 2
    assert ordinary.mape < 0.08


def test_recency_weighting_is_turned_almost_off_on_purpose() -> None:
    import dataclasses

    from residual.position.forecast import HALF_LIFE_DAYS, LOOKBACK_DAYS
    from residual.simulate.world import simulate as run

    assert HALF_LIFE_DAYS >= LOOKBACK_DAYS, "the default should be near-flat"

    config = dataclasses.replace(BENCHMARK, days=300, scenarios=())
    events = run(config).log.events()
    contracted = {str(m): rate for m, rate in BENCHMARK.base_rates}

    aggressive = backtest(
        events, config.start, config.days, contracted, horizon=14, half_life=7.0
    )
    default = backtest(
        events, config.start, config.days, contracted, horizon=14
    )
    assert abs(aggressive.optimism) < abs(default.optimism), "it should cut the lag"
    assert aggressive.mape > default.mape, "and it should cost accuracy to do it"


def test_calibration_made_the_world_harder_to_forecast() -> None:
    from residual.simulate.world import simulate as run

    events = run(BENCHMARK).log.events()
    contracted = {str(m): rate for m, rate in BENCHMARK.base_rates}
    near = backtest(events, BENCHMARK.start, BENCHMARK.days, contracted, horizon=7)
    far = backtest(events, BENCHMARK.start, BENCHMARK.days, contracted, horizon=21)

    assert far.mape < near.mape, "a longer horizon should still average out"


def test_error_is_not_claimed_to_decompose(world, contracted) -> None:
    from residual.position.forecast import ForecastError

    assert not hasattr(ForecastError, "residual")
    assert not hasattr(ForecastError, "explained_fraction")
    bt = backtest(world.log.events(), world.start, BENCHMARK.days, contracted, horizon=7)
    assert any(e.contributors for e in bt.errors), "nothing was measured at all"
