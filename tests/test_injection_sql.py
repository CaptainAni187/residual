
from __future__ import annotations

from datetime import timedelta

import pytest

from residual.explain.hypotheses import (
    FeeRateChange,
    GatewayFees,
    UnknownMethod,
    _priced_at,
)
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate


@pytest.fixture(scope="module")
def setup():
    world = simulate(BENCHMARK)
    wh = Warehouse.build(world.log.events())
    start = world.start + timedelta(days=56)
    return wh, start, start + timedelta(days=6)


PAYLOADS = [
    "x' THEN 0 WHEN method='card",
    "card' THEN 999999 WHEN method='x",
    "card'; DROP TABLE events; --",
    "card' OR '1'='1",
    "card') END), 0) FROM events WHERE 1=1 UNION SELECT 999 --",
    "card'--",
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_a_method_name_cannot_inject_sql(payload: str) -> None:
    with pytest.raises(UnknownMethod):
        _priced_at({payload: "2.00"})


def test_values_travel_as_parameters_not_text() -> None:
    sql, params = _priced_at({"card": "2.00", "upi": "0.00"})
    assert sql.count("?") == len(params) == 4
    assert "'card'" not in sql and "2.00" not in sql


def test_a_rate_that_is_not_a_number_never_reaches_the_query() -> None:
    from decimal import InvalidOperation

    with pytest.raises((InvalidOperation, ValueError)):
        _priced_at({"card": "2.0) AS BIGINT) END), 0) FROM events --"})


@pytest.mark.parametrize("payload", PAYLOADS)
def test_the_verifiers_refuse_a_hostile_contract(setup, payload: str) -> None:
    wh, start, end = setup
    for verifier in (GatewayFees, FeeRateChange):
        with pytest.raises(UnknownMethod):
            verifier({payload: "2.00"}).verify(wh, start, end)


def test_the_tables_survive_every_payload(setup) -> None:
    wh, start, end = setup
    before = wh.sql("SELECT count(*) FROM events")[0][0]
    for payload in PAYLOADS:
        with pytest.raises(UnknownMethod):
            GatewayFees({payload: "2.00"}).verify(wh, start, end)
    assert wh.sql("SELECT count(*) FROM events")[0][0] == before


def test_a_legitimate_contract_still_works(setup) -> None:
    wh, start, end = setup
    contracted = {str(m): rate for m, rate in BENCHMARK.base_rates}
    evidence = GatewayFees(contracted).verify(wh, start, end)
    assert evidence.supported and evidence.amount.paise > 0
    assert wh.sql(evidence.sql)


def test_the_pasteable_copy_escapes_quotes() -> None:
    from residual.ledger.warehouse import Warehouse as W

    wh = W.build([])
    rendered = wh.rendered("SELECT ? AS x", ["O'Brien"])
    assert "'O''Brien'" in rendered
    assert wh.sql(rendered)[0][0] == "O'Brien"
