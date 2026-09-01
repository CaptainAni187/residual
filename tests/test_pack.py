
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from residual.explain import pack as packing
from residual.explain.agent import OfflineAgent, write_memo
from residual.explain.close import run_close
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate


@pytest.fixture(scope="module")
def sealed(tmp_path_factory):
    r = simulate(BENCHMARK)
    events = r.log.events()
    wh = Warehouse.build(events)
    contracted = {str(m): rate for m, rate in BENCHMARK.base_rates}
    start = r.start + timedelta(days=56)
    close = run_close(events, start, start + timedelta(days=6), contracted, wh)
    memo = write_memo(close, wh, contracted, agent=OfflineAgent())
    return r, events, contracted, packing.build(close, r.log, memo)


def test_the_same_books_produce_the_same_pack(sealed) -> None:
    _r, _events, contracted, pack = sealed
    again = simulate(BENCHMARK)
    wh = Warehouse.build(again.log.events())
    start = again.start + timedelta(days=56)
    close = run_close(again.log.events(), start, start + timedelta(days=6), contracted, wh)
    assert packing.build(close, again.log).digest == pack.digest


def test_it_round_trips_through_a_file(sealed, tmp_path) -> None:
    r, events, contracted, pack = sealed
    path = pack.write(tmp_path / "close.json")
    packing.verify(packing.Pack.read(path), r.log, events, contracted)


def test_altering_the_books_invalidates_the_pack(sealed) -> None:
    _r, events, contracted, pack = sealed
    tampered = simulate(BENCHMARK)
    tampered.log.append(events[0].model_copy(update={"event_id": "sneaky"}))
    with pytest.raises(packing.PackMismatch, match="log head"):
        packing.verify(pack, tampered.log, tampered.log.events(), contracted)


def test_editing_the_pack_invalidates_it(sealed) -> None:
    r, events, contracted, pack = sealed
    edited = packing.Pack(json.loads(json.dumps(pack.body)))
    edited.body["totals"]["residual"]["display"] = "INR 0.00 (definitely)"
    with pytest.raises(packing.PackMismatch, match="digest"):
        packing.verify(edited, r.log, events, contracted)


def test_the_memo_is_carried_but_not_hashed(sealed) -> None:
    *_, pack = sealed
    assert pack.body["memo"]["excluded_from_digest"] is True

    without = packing.Pack(json.loads(json.dumps(pack.body)))
    without.body["memo"]["text"] = "something else entirely"
    assert packing._digest(without.body) == pack.digest


def test_the_pack_carries_its_own_partition_proof(sealed) -> None:
    _, _, _, pack = sealed
    proof = pack.body["partition_proof"]
    assert proof and all(row["ok"] for row in proof)
    for row in proof:
        assert row["claimed"]["paise"] == row["actual"]["paise"]


def test_every_finding_ships_its_evidence(sealed) -> None:
    _, _, _, pack = sealed
    assert pack.body["findings"]
    for f in pack.body["findings"]:
        assert f["evidence_sql"].strip().upper().startswith(("SELECT", "WITH"))
        assert "?" not in f["evidence_sql"]
