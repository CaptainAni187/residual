
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from residual.explain.close import _ensure_links, run_close
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate
from residual.web.app import app


@pytest.fixture(scope="module")
def world():
    return simulate(BENCHMARK)


def _run_all(fn, threads: int = 12) -> list[str]:
    problems: list[str] = []
    workers = [
        threading.Thread(target=lambda i=i: problems.extend(fn(i) or []))
        for i in range(threads)
    ]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    return problems


def test_a_warehouse_survives_concurrent_readers(world) -> None:
    wh = Warehouse.build(world.log.events())
    _ensure_links(wh)
    expected = wh.sql("SELECT count(*) FROM events")[0][0]

    def read(_i: int) -> list[str]:
        out = []
        for _ in range(15):
            try:
                if wh.sql("SELECT count(*) FROM events")[0][0] != expected:
                    out.append("row count changed under a concurrent read")
                rows = wh.sql(
                    "SELECT account, sum(amount_paise) FROM postings GROUP BY 1"
                )
                if any(len(r) != 2 for r in rows):
                    out.append(f"malformed row shape: {rows[:1]}")
            except Exception as exc:  # noqa: BLE001 -- any failure is the finding
                out.append(f"{type(exc).__name__}: {exc}")
        return out

    assert _run_all(read) == []


def test_each_thread_gets_its_own_cursor(world) -> None:
    wh = Warehouse.build(world.log.events())
    seen: list[object] = []
    guard = threading.Lock()
    hold = threading.Barrier(6)

    def grab(_i: int) -> list[str]:
        cursor = wh.cursor
        with guard:
            seen.append(cursor)
        hold.wait(timeout=10)
        return []

    _run_all(grab, threads=6)
    assert len({id(c) for c in seen}) == 6, "threads shared a cursor"


def test_the_same_thread_reuses_its_cursor(world) -> None:
    wh = Warehouse.build(world.log.events())
    assert wh.cursor is wh.cursor


def test_linkage_runs_once_under_a_race(world) -> None:
    wh = Warehouse.build(world.log.events())
    ready = threading.Barrier(8)

    def link(_i: int) -> list[str]:
        ready.wait()
        _ensure_links(wh)
        return []

    _run_all(link, threads=8)
    assert wh.links_loaded
    linked = wh.sql("SELECT count(*) FROM credit_links")[0][0]
    assert linked > 0


def test_concurrent_closes_agree(world) -> None:
    events = world.log.events()
    wh = Warehouse.build(events)
    contracted = {str(m): r for m, r in BENCHMARK.base_rates}
    from datetime import timedelta

    start = world.start + timedelta(days=56)
    expected = run_close(events, start, start + timedelta(days=6), contracted, wh)

    def close(_i: int) -> list[str]:
        got = run_close(events, start, start + timedelta(days=6), contracted, wh)
        if got.by_cause() != expected.by_cause() or got.residual != expected.residual:
            return ["a concurrent close produced a different decomposition"]
        return []

    assert _run_all(close, threads=8) == []


def test_the_dashboard_survives_concurrent_visitors() -> None:
    client = TestClient(app)
    client.get("/?week=8")

    def visit(i: int) -> list[str]:
        out = []
        for path, params in (
            ("/", {"week": i % 13}),
            ("/ask", {"q": "balances"}),
            ("/evidence/8/normal_fee", {}),
        ):
            response = client.get(path, params=params)
            if response.status_code != 200:
                out.append(f"{path} -> HTTP {response.status_code}")
        return out

    assert _run_all(visit) == []
