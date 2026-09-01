
from __future__ import annotations

from datetime import timedelta

import pytest

from residual.domain.causes import Cause
from residual.eval.score import score_run
from residual.explain.close import run_close
from residual.explain.hypotheses import REGISTRY
from residual.ledger.money import Money
from residual.ledger.warehouse import Warehouse
from residual.position.engine import fold
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate


@pytest.fixture(scope="module")
def world():
    r = simulate(BENCHMARK)
    r.log.verify_chain()
    return r


@pytest.fixture(scope="module")
def contracted():
    return {str(m): rate for m, rate in BENCHMARK.base_rates}


def test_every_planted_scenario_actually_happened(world) -> None:
    world.require_all_scenarios_fired()


def test_books_balance_over_the_whole_run(world) -> None:
    fold(world.log.events()).check()


def test_every_close_residual_is_zero(world, contracted) -> None:
    events = world.log.events()
    wh = Warehouse.build(events)
    for offset in range(0, BENCHMARK.days, 7):
        s = world.start + timedelta(days=offset)
        c = run_close(events, s, s + timedelta(days=6), contracted, wh)
        assert c.closes, f"{s}: residual {c.residual}"


def test_no_cause_is_ever_hallucinated(world, contracted) -> None:
    rep = score_run(world.log.events(), world.truth, world.start, BENCHMARK.days, contracted)
    assert rep.hallucinated_cause_rate == 0.0, [
        (str(w.window[0]), str(c.cause), str(c.reported))
        for w in rep.windows for c in w.hallucinated
    ]


def test_amounts_are_exact_to_the_paise(world, contracted) -> None:
    rep = score_run(world.log.events(), world.truth, world.start, BENCHMARK.days, contracted)
    assert rep.rupee_error.paise == 0
    assert rep.amount_exact_rate == 1.0


def test_the_silent_payout_failure_is_caught(world, contracted) -> None:
    events = world.log.events()
    wh = Warehouse.build(events)
    found = [
        f
        for offset in range(0, BENCHMARK.days, 7)
        for f in run_close(
            events,
            world.start + timedelta(days=offset),
            world.start + timedelta(days=offset + 6),
            contracted,
            wh,
        ).findings
        if f.cause is Cause.SETTLEMENT_NEVER_ARRIVED
    ]
    assert found, "the lost payout was not detected"
    assert all(f.alarming for f in found)
    assert all(f.evidence.entity_ids for f in found), "no UTR cited as evidence"


def test_the_unannounced_fee_hike_is_caught(world, contracted) -> None:
    events = world.log.events()
    wh = Warehouse.build(events)
    hits = [
        f
        for offset in range(0, BENCHMARK.days, 7)
        for f in run_close(
            events,
            world.start + timedelta(days=offset),
            world.start + timedelta(days=offset + 6),
            contracted,
            wh,
        ).findings
        if f.cause is Cause.FEE_RATE_INCREASE
    ]
    assert hits, "fees billed above contract went unnoticed"
    assert all(f.amount.paise > 0 for f in hits)


def test_every_finding_carries_runnable_sql(world, contracted) -> None:
    events = world.log.events()
    wh = Warehouse.build(events)
    s = world.start + timedelta(days=56)
    c = run_close(events, s, s + timedelta(days=6), contracted, wh)
    for f in c.findings:
        sql = f.evidence.sql.strip().upper()
        assert sql.startswith(("SELECT", "WITH")), sql[:60]
        assert "?" not in f.evidence.sql, "parameters were not inlined"
        wh.sql(f.evidence.sql)


def test_nothing_that_happened_goes_unreported(world, contracted) -> None:
    rep = score_run(world.log.events(), world.truth, world.start, BENCHMARK.days, contracted)
    assert rep.blind_spots == {}, {str(k): v for k, v in rep.blind_spots.items()}
    assert rep.cause_recall == 1.0


def test_verifiers_partition_every_account(world, contracted) -> None:
    events = world.log.events()
    wh = Warehouse.build(events)
    for offset in range(0, BENCHMARK.days, 7):
        s = world.start + timedelta(days=offset)
        c = run_close(events, s, s + timedelta(days=6), contracted, wh)
        assert c.fully_covered, [(str(g.account), str(g.drift)) for g in c.coverage_gaps]


def test_registry_covers_every_non_structural_cause() -> None:
    from residual.eval.score import KNOWN_BLIND_SPOTS

    missing = set(Cause) - set(REGISTRY) - KNOWN_BLIND_SPOTS
    assert not missing, f"causes with no verifier and no declared blind spot: {missing}"


def test_a_close_cannot_see_facts_that_had_not_arrived(world, contracted) -> None:
    from residual.explain.restate import restate

    events = world.log.events()
    start = world.start + timedelta(days=14)
    end = start + timedelta(days=6)
    rs = restate(events, start, end, contracted)

    assert rs.then.as_of == end and rs.now.as_of is None
    assert rs.then.closes and rs.now.closes, "a restated close must still tie out"
    assert rs.unexplained_drift.paise == 0, (
        f"the two runs differ by {rs.unexplained_drift} that no cause accounts for"
    )


def test_late_disputes_actually_restate_the_books(world, contracted) -> None:
    from residual.explain.restate import restate

    events = world.log.events()
    moved = [
        rs
        for offset in range(0, BENCHMARK.days, 7)
        if (
            rs := restate(
                events,
                world.start + timedelta(days=offset),
                world.start + timedelta(days=offset + 6),
                contracted,
            )
        ).moved
    ]
    assert len(moved) >= 5, f"only {len(moved)} closes were restated"
    assert any(
        m.cause is Cause.DISPUTE_RESERVE_HELD for rs in moved for m in rs.movements
    ), "no dispute arrived late, so the two clocks are doing nothing"


def test_refinement_survives_a_partial_set_of_hypotheses(world, contracted) -> None:
    from residual.explain.close import refine
    from residual.explain.hypotheses import REGISTRY, SettlementNeverArrived
    from residual.ledger.warehouse import Warehouse as W

    events = world.log.events()
    wh = W.build(events)
    start = world.start + timedelta(days=56)
    end = start + timedelta(days=6)
    run_close(events, start, end, contracted, wh)

    child = SettlementNeverArrived()
    evidence = child.verify(wh, start, end)
    amounts = refine({child.cause: (child, evidence)})
    assert amounts[child.cause] == evidence.amount
    assert child.refines not in amounts

    parent_cls = REGISTRY[child.refines]
    parent = parent_cls()
    only_parent = refine({parent.cause: (parent, parent.verify(wh, start, end))})
    assert only_parent[parent.cause] == parent.verify(wh, start, end).amount


def test_every_subsystem_survives_an_empty_ledger(contracted) -> None:
    from datetime import date

    from residual.explain import pack as packing
    from residual.explain.agent import OfflineAgent, write_memo
    from residual.explain.qa import ask
    from residual.explain.restate import restate
    from residual.ledger.store import EventLog
    from residual.ledger.warehouse import Warehouse as W
    from residual.position.engine import decompose, position_at
    from residual.position.forecast import Shape, forecast

    day = date(2026, 5, 1)
    empty: list = []
    wh = W.build(empty)

    close = run_close(empty, day, day, contracted, wh)
    assert close.closes and close.fully_covered and not close.findings

    assert position_at(empty, day).bank == Money.zero()
    assert decompose(empty, day, day).closes
    assert forecast(empty, day, horizon=7).total == Money.zero()
    assert Shape.learn(empty, day).realisation == 1
    assert not restate(empty, day, day, contracted).moved
    assert packing.build(close, EventLog()).digest
    assert ask(wh, "balance by account").ok
    assert write_memo(close, wh, contracted, agent=OfflineAgent()).trustworthy


def test_a_verifier_can_run_on_a_warehouse_the_caller_built(world, contracted) -> None:
    from residual.explain.hypotheses import SettlementNeverArrived
    from residual.ledger.warehouse import Warehouse as W

    events = world.log.events()
    fresh = W.build(events)
    assert not fresh.links_loaded

    from residual.explain.close import _ensure_links

    _ensure_links(fresh)
    assert fresh.links_loaded
    start = world.start + timedelta(days=56)
    assert SettlementNeverArrived().verify(fresh, start, start + timedelta(days=6))


def test_a_partial_settlement_is_late_money_not_missing_money(contracted) -> None:
    from residual.ledger.warehouse import Warehouse as W
    from residual.simulate.world import PartialSettlement
    from residual.simulate.world import simulate as run

    world = run(BENCHMARK)
    world.require_all_scenarios_fired()
    partial = next(
        s for s in BENCHMARK.scenarios if isinstance(s, PartialSettlement)
    )

    events = world.log.events()
    wh = W.build(events)
    start = world.start + timedelta(days=(partial.day // 7) * 7)
    close = run_close(events, start, start + timedelta(days=6), contracted, wh)

    assert close.closes and close.fully_covered
    escalated = {str(f.cause) for f in close.alarms}
    assert "settlement_never_arrived" not in escalated, (
        f"a partial settlement was escalated as missing: {escalated}"
    )


def test_the_held_back_portion_settles_later(contracted) -> None:
    from residual.ledger import select
    from residual.simulate.world import simulate as run

    events = run(BENCHMARK).log.events()
    covered = {pid for s in select.settlements(events) for pid in s.covers}
    captured = {c.payment_id for c in select.captures(events)}

    early = {
        c.payment_id for c in select.captures(events)
        if (BENCHMARK.start + timedelta(days=BENCHMARK.days - 1) - c.occurred_at).days > 20
    }
    assert early - covered == set(), f"{len(early - covered)} early captures never settled"
    assert covered <= captured
