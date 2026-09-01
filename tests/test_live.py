
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from residual.ingest import razorpay
from residual.position.engine import fold


def _yesterday():
    return (datetime.now(tz=UTC) - timedelta(days=1)).date()


live = pytest.mark.skipif(
    not os.environ.get("RAZORPAY_KEY_ID") or not os.environ.get("RAZORPAY_KEY_SECRET"),
    reason="no test-mode credentials; set them in .env to run against the real API",
)


@live
def test_the_real_endpoint_answers() -> None:
    rows = razorpay.fetch(_yesterday())
    assert isinstance(rows, list)


@live
def test_every_field_we_rely_on_is_still_there() -> None:
    rows = razorpay.fetch(_yesterday())
    if not rows:
        pytest.skip("no settled transactions on that day")
    required = {"entity_id", "type", "amount", "credit", "debit", "settlement_id"}
    assert required <= set(rows[0])


@live
def test_real_data_keeps_the_books_balanced() -> None:
    on = _yesterday()
    rows = razorpay.fetch(on)
    if not rows:
        pytest.skip("no settled transactions on that day")
    fold(razorpay.to_events(rows, on=on)).check(complete=False)


def test_the_offline_path_needs_no_credentials(monkeypatch) -> None:
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from residual.explain.agent import OfflineAgent, pick_agent
    from residual.simulate.presets import BENCHMARK
    from residual.simulate.world import simulate

    assert isinstance(pick_agent(), OfflineAgent)
    assert simulate(BENCHMARK).log.head


@live
def test_credentials_are_confirmed_before_settlements_are_asked_about() -> None:
    reachable = razorpay.probe()
    assert reachable["authenticated"]
    assert reachable["key_id"].startswith("rzp_test_")


def test_the_probe_refuses_a_live_key(monkeypatch) -> None:
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_abcdefghij")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "not-a-real-secret")
    with pytest.raises(razorpay.NotConfigured, match="non-test key"):
        razorpay.probe()


def test_the_probe_refuses_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    with pytest.raises(razorpay.NotConfigured, match="test-mode keys only"):
        razorpay.probe()
