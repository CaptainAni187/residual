
from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from residual.ledger.money import Money
from residual.position.interval import (
    Adaptive,
    Coverage,
    SplitConformal,
    certify,
    conformal_quantile,
)
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate

DAY = date(2026, 3, 1)


def test_the_quantile_is_the_conformal_one_not_a_percentile() -> None:
    scores = [i / 100 for i in range(10)]
    assert conformal_quantile(scores, 0.10) == 0.09


def test_too_few_points_falls_back_to_the_worst_seen() -> None:
    assert conformal_quantile([0.1, 0.2, 0.3, 0.4, 0.5], 0.10) == 0.5
    assert conformal_quantile([], 0.10) == 0.0


def test_a_floor_sits_below_the_forecast() -> None:
    model = SplitConformal()
    for actual in (900, 950, 1100, 980, 870, 1020, 960, 940, 1010, 930):
        model.observe(Money(100000), Money(actual * 100))
    floor = model.floor_for(DAY, Money.parse("800000"))
    assert floor.floor.paise < floor.forecast.paise
    assert floor.certified
    assert "at least" in str(floor)


def test_it_declines_to_certify_without_enough_history() -> None:
    model = SplitConformal()
    for _ in range(4):
        model.observe(Money(100000), Money(95000))
    floor = model.floor_for(DAY, Money.parse("800000"))
    assert not floor.certified
    assert "too little history" in str(floor)


def test_a_floor_never_goes_negative() -> None:
    model = SplitConformal()
    for _ in range(12):
        model.observe(Money(100000), Money(1))
    assert model.floor_for(DAY, Money.parse("1000")).floor.paise >= 0


def test_the_level_tightens_after_a_breach() -> None:
    model = Adaptive(alpha=0.10, gamma=0.05)
    before = model.current
    predicted = model.floor_for(DAY, Money.parse("1000"))
    model.observe(predicted, Money.zero())
    assert model.current < before


def test_the_level_relaxes_when_the_floor_holds() -> None:
    model = Adaptive(alpha=0.10, gamma=0.05)
    for _ in range(12):
        model.observe(model.floor_for(DAY, Money.parse("1000")), Money.parse("1000"))
    assert model.current > model.alpha
    assert model.realised_miscoverage == 0.0


def test_the_level_stays_inside_its_bounds() -> None:
    model = Adaptive(alpha=0.10, gamma=0.5)
    for i in range(60):
        model.observe(
            model.floor_for(DAY, Money.parse("1000")),
            Money.zero() if i % 2 else Money.parse("2000"),
        )
        assert 0 < model.current < 1


@pytest.fixture(scope="module")
def rolled():
    world = simulate(dataclasses.replace(BENCHMARK, days=500, scenarios=()))
    events, start = world.log.events(), world.start
    return {
        alpha: certify(events, start, 500, alpha=alpha, step=7)
        for alpha in (0.10, 0.02)
    }


@pytest.fixture(scope="module")
def measured(rolled):
    return rolled[0.10]


def test_the_floors_are_measured_on_enough_windows(measured) -> None:
    for coverage in measured.values():
        assert coverage.independent > 20, f"{coverage.independent} proves little"


def test_adaptive_reaches_its_target(measured) -> None:
    adaptive = measured["adaptive (ACI)"]
    assert adaptive.empirical >= 0.88, f"{adaptive.empirical:.1%}"


def test_the_floor_is_tight_enough_to_be_worth_having(measured) -> None:
    for coverage in measured.values():
        assert coverage.tightness > 0.80, coverage.summary()
        assert coverage.tightness <= 1.0


def test_a_tighter_target_costs_headroom(rolled) -> None:
    assert (
        rolled[0.02]["adaptive (ACI)"].tightness
        < rolled[0.10]["adaptive (ACI)"].tightness
    ), "asking for more confidence should lower the floor"


def test_the_window_count_does_not_overstate_the_evidence(measured) -> None:
    for coverage in measured.values():
        assert coverage.independent < coverage.n
        assert "non-overlapping" in coverage.summary()


def test_coverage_summary_reports_both_numbers() -> None:
    coverage = Coverage("x", 0.9, held=90, breached=10)
    coverage.floor_total = Money(900)
    coverage.actual_total = Money(1000)
    assert "90.0% coverage" in coverage.summary()
    assert "90% of what arrived" in coverage.summary()


def _steady(model: Adaptive, n: int, forecast: str, actual: str) -> None:
    for i in range(n):
        day = DAY + timedelta(days=i)
        model.observe(model.floor_for(day, Money.parse(forecast)), Money.parse(actual))


def test_a_calibrated_model_reports_itself_healthy() -> None:
    model = Adaptive(alpha=0.10, gamma=0.05)
    _steady(model, 40, "100000", "98000")
    assert not model.in_distress
    assert model.discount < 0.10
    assert "behaving as calibrated" in model.diagnosis()


def test_a_regime_change_is_noticed() -> None:
    model = Adaptive(alpha=0.10, gamma=0.05)
    _steady(model, 40, "100000", "98000")
    assert not model.in_distress

    _steady(model, 20, "100000", "50000")
    assert model.in_distress
    assert model.discount > 0.25
    assert "stopped saying anything useful" in model.diagnosis()


def test_a_burst_of_breaches_is_noticed_before_it_adapts() -> None:
    model = Adaptive(alpha=0.10, gamma=0.005)
    _steady(model, 40, "100000", "98000")
    for i in range(12):
        day = DAY + timedelta(days=40 + i)
        model.observe(model.floor_for(day, Money.parse("100000")), Money.parse("10000"))
    assert model.in_distress


def test_it_does_not_judge_before_it_has_evidence() -> None:
    model = Adaptive(alpha=0.10)
    _steady(model, 5, "100000", "10")
    assert not model.in_distress, "five windows is not a verdict"
    assert "not enough to judge" in model.diagnosis()


def test_the_benchmark_merchant_is_never_in_distress() -> None:
    from residual.ledger import select
    from residual.ledger.money import total
    from residual.position.forecast import forecast as project

    world = simulate(dataclasses.replace(BENCHMARK, days=500, scenarios=()))
    events = world.log.events()
    model = Adaptive(alpha=0.10)

    for offset in range(60, 480, 14):
        as_of = world.start + timedelta(days=offset)
        end = as_of + timedelta(days=14)
        point = project(events, as_of, horizon=14).through(end)
        landed = total(
            e.amount for e in select.credits(events) if as_of < e.occurred_at <= end
        )
        if point.paise:
            model.observe(model.floor_for(end, point), landed)

    assert not model.in_distress, model.diagnosis()


def test_calibration_never_sees_the_window_it_predicts() -> None:
    from residual.position.interval import calibrate

    world = simulate(dataclasses.replace(BENCHMARK, days=400, scenarios=()))
    as_of = world.start + timedelta(days=300)
    model = calibrate(world.log.events(), as_of, horizon=14, lookback=140)

    assert model.observations > 0
    assert model.observations <= 140 // 14


def test_calibration_produces_a_certifiable_floor() -> None:
    from residual.position.interval import calibrate

    world = simulate(dataclasses.replace(BENCHMARK, days=400, scenarios=()))
    as_of = world.start + timedelta(days=300)
    model = calibrate(world.log.events(), as_of, horizon=14, lookback=200)
    floor = model.floor_for(as_of + timedelta(days=14), Money.parse("1000000"))
    assert floor.certified
    assert floor.floor.paise < floor.forecast.paise


def test_calibration_on_a_short_history_declines_rather_than_guesses() -> None:
    from residual.position.interval import calibrate

    world = simulate(BENCHMARK)
    model = calibrate(world.log.events(), world.start + timedelta(days=40), horizon=14)
    floor = model.floor_for(world.start + timedelta(days=54), Money.parse("100000"))
    assert not floor.certified


def test_pooling_sums_the_evidence_across_merchants() -> None:
    from residual.position.interval import certify_across

    worlds = [
        (w.log.events(), w.start, 300)
        for w in (
            simulate(dataclasses.replace(BENCHMARK, seed=seed, days=300, scenarios=()))
            for seed in (1, 7)
        )
    ]
    pooled = certify_across(worlds, horizon=14, alpha=0.10)
    assert set(pooled) == {"split conformal", "adaptive (ACI)"}
    for row in pooled.values():
        assert row.n > 100
        assert 0 < row.tightness <= 1.0
        assert row.independent < row.n
