
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from residual.ingest import bank, razorpay
from residual.ledger import events as ev
from residual.ledger.money import Money
from residual.ledger.store import EventLog, content_digest
from residual.position.engine import fold
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate

FIXTURES = Path(__file__).parent / "fixtures"


def _capture(payment_id: str = "pay_1") -> ev.PaymentCaptured:
    day = date(2026, 3, 1)
    return ev.PaymentCaptured(
        event_id=f"cap-{payment_id}", occurred_at=day, recorded_at=day,
        payment_id=payment_id, order_id="o1", gross=Money.parse("1000"),
        method=ev.Method.UPI, fee=Money.parse("20"), tax=Money.parse("3.60"),
    )


def test_the_same_fact_twice_is_recorded_once() -> None:
    log = EventLog()
    assert log.ingest([_capture()]).summary() == "1 events recorded"
    assert log.ingest([_capture()]).nothing_new
    assert len(log) == 1


def test_distinct_facts_are_not_collapsed() -> None:
    log = EventLog()
    log.ingest([_capture("pay_1"), _capture("pay_2")])
    assert len(log) == 2
    assert content_digest(_capture("pay_1")) != content_digest(_capture("pay_2"))


def test_a_digest_is_independent_of_position() -> None:
    first, second = EventLog(), EventLog()
    first.ingest([_capture("pay_0"), _capture("pay_1")])
    second.ingest([_capture("pay_1")])
    assert first.events()[1].event_id == second.events()[0].event_id
    assert content_digest(first.events()[1]) == content_digest(second.events()[0])


def test_re_importing_a_recon_report_changes_nothing() -> None:
    rows = json.loads((FIXTURES / "recon_march.json").read_text())["items"]
    events = razorpay.to_events(rows, on=date(2026, 3, 10))

    log = EventLog()
    first = log.ingest(events)
    before = fold(log.events())

    second = log.ingest(razorpay.to_events(rows, on=date(2026, 3, 10)))
    assert first.all_new and second.nothing_new
    assert fold(log.events()) == before
    assert len(log) == len(events)


def test_re_importing_a_bank_statement_changes_nothing() -> None:
    statement = bank.load_any(FIXTURES / "statements" / "hdfc.csv")
    events = bank.to_events(statement)

    log = EventLog()
    log.ingest(events)
    balances = fold(log.events())

    log.ingest(bank.to_events(bank.load_any(FIXTURES / "statements" / "hdfc.csv")))
    assert fold(log.events()) == balances


def test_a_partial_re_run_records_only_what_is_missing() -> None:
    events = simulate(BENCHMARK).log.events()[:200]
    log = EventLog()
    log.ingest(events[:120])
    result = log.ingest(events)

    assert len(result.recorded) == 80
    assert len(result.duplicates) == 120
    assert len(log) == 200
    assert "80 events recorded" in result.summary()


def test_the_chain_still_verifies_after_duplicates_are_rejected() -> None:
    events = simulate(BENCHMARK).log.events()[:150]
    log = EventLog()
    for _ in range(3):
        log.ingest(events)
    log.verify_chain()
    assert len(log) == 150


def test_a_correction_is_not_a_duplicate() -> None:
    day = date(2026, 3, 1)
    refund = ev.RefundIssued(
        event_id="ref-1", occurred_at=day, recorded_at=day,
        refund_id="rfnd_1", payment_id="pay_1", amount=Money.parse("100"),
    )
    reversal = refund.model_copy(
        update={"event_id": "ref-1-reversed", "recorded_at": day + timedelta(days=1)}
    )
    log = EventLog()
    log.ingest([_capture(), refund, reversal])
    assert len(log) == 3


def test_a_replayed_log_file_is_idempotent(tmp_path) -> None:
    events = simulate(BENCHMARK).log.events()[:100]
    log = EventLog()
    log.ingest(events)
    path = log.write_jsonl(tmp_path / "events.jsonl")

    reloaded = EventLog.read_jsonl(path)
    assert reloaded.ingest(events).nothing_new, "a reloaded log forgot what it holds"
    assert len(reloaded) == 100


@pytest.mark.parametrize("times", [2, 3, 5])
def test_importing_n_times_gives_the_same_books_as_once(times: int) -> None:
    events = simulate(BENCHMARK).log.events()[:300]
    once = EventLog()
    once.ingest(events)

    many = EventLog()
    for _ in range(times):
        many.ingest(events)

    assert fold(many.events()) == fold(once.events())
    assert len(many) == len(once)
