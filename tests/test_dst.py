
from __future__ import annotations

import pytest

from residual.dst import faults
from residual.dst.faults import CONVERGING, Fault, Schedule, plan
from residual.dst.simulator import run_one, shrink, sweep
from residual.ledger import project as projection
from residual.ledger import store
from residual.ledger.money import Money


def test_a_schedule_is_a_pure_function_of_its_seed() -> None:
    assert plan(8214, batches=20) == plan(8214, batches=20)
    assert plan(8214, batches=20) != plan(8215, batches=20)


def test_truncation_is_not_expected_to_converge() -> None:
    assert Fault.TRUNCATE not in CONVERGING
    assert not Schedule(1, (faults.Injection(Fault.TRUNCATE, 0, "keep 50%"),)).converges
    assert Schedule(1, (faults.Injection(Fault.DUPLICATE_BATCH, 0),)).converges


def test_faults_actually_disturb_the_delivery_plan() -> None:
    batches = [[f"b{i}e{j}" for j in range(4)] for i in range(10)]
    schedule = Schedule(
        1,
        (
            faults.Injection(Fault.DUPLICATE_BATCH, 0),
            faults.Injection(Fault.SPLIT, 2),
            faults.Injection(Fault.DELAY, 4, "+2 batches"),
        ),
    )
    delivered = faults.apply(batches, schedule)
    assert len(delivered) > len(batches)
    assert sum(len(e) for _, e in delivered) > sum(len(b) for b in batches)


def test_a_run_is_reproducible() -> None:
    first, second = run_one(seed=7), run_one(seed=7)
    assert first.schedule == second.schedule
    assert (first.recorded, first.rejected_as_duplicate) == (
        second.recorded, second.rejected_as_duplicate
    )


def test_the_faults_are_actually_being_suffered() -> None:
    result = sweep(seeds=40, stop_early=False)
    assert result.duplicates_rejected > 100, "no duplicate ever reached the log"
    assert result.deliveries > 40, "no batch was ever split or duplicated"


def test_the_ledger_survives_hundreds_of_hostile_lives() -> None:
    result = sweep(seeds=150, stop_early=False)
    assert result.ok, [str(f.schedule) for f in result.failures[:3]]


def test_it_catches_a_ledger_that_forgets_idempotency(monkeypatch) -> None:

    def accepts_anything(self, event):
        prev = self._records[-1].hash if self._records else store.GENESIS
        seq = len(self._records) + 1
        event = event.model_copy(update={"seq": seq})
        record = store.Record(
            seq=seq, event=event, prev_hash=prev,
            hash=store._hash(prev, event), digest=store.content_digest(event),
        )
        self._records.append(record)
        self._seen[record.digest] = record
        return record

    monkeypatch.setattr(store.EventLog, "append", accepts_anything)
    run = run_one(seed=7)
    assert not run.ok
    assert any(v.invariant == "idempotency" for v in run.violations)


def test_it_catches_balances_that_drift_from_the_log(monkeypatch) -> None:
    from residual.dst import simulator
    from residual.ledger.accounts import Account

    real_fold = simulator.fold

    def drifting(events):
        balances = real_fold(events)
        if len(list(events)) > 30:
            balances[Account.BANK] = balances[Account.BANK] + Money(1)
        return balances

    monkeypatch.setattr(simulator, "fold", drifting)
    run = run_one(seed=7)
    assert not run.ok
    assert any(v.invariant == "determinism" for v in run.violations)


def test_it_catches_a_broken_projection(monkeypatch) -> None:
    from residual.ledger.posting import Posting, Unbalanced

    real = projection._DISPATCH["payment_captured"]

    def unbalanced(event):
        postings = list(real(event))
        first = postings[0]
        postings[0] = Posting(first.account, first.amount + Money(1), first.ref, first.memo)
        return tuple(postings)

    monkeypatch.setitem(projection._DISPATCH, "payment_captured", unbalanced)
    with pytest.raises(Unbalanced):
        run_one(seed=7)


def test_shrinking_reduces_a_failure_to_its_cause(monkeypatch) -> None:

    def accepts_anything(self, event):
        prev = self._records[-1].hash if self._records else store.GENESIS
        seq = len(self._records) + 1
        event = event.model_copy(update={"seq": seq})
        record = store.Record(
            seq=seq, event=event, prev_hash=prev,
            hash=store._hash(prev, event), digest=store.content_digest(event),
        )
        self._records.append(record)
        self._seen[record.digest] = record
        return record

    monkeypatch.setattr(store.EventLog, "append", accepts_anything)
    run = run_one(seed=7)
    assert len(run.schedule.injections) > 3

    minimal = shrink(run)
    assert len(minimal.injections) < len(run.schedule.injections)
    assert not run_one(seed=7, schedule=minimal).ok, "the shrunk schedule stopped failing"
