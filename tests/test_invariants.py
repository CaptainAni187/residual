
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from residual.ledger.accounts import NEVER_NEGATIVE, NORMAL_BALANCE, Side
from residual.ledger.money import CurrencyMismatch, Money, allocate
from residual.ledger.posting import Entry, Unbalanced, credit, debit
from residual.ledger.project import project
from residual.ledger.store import ChainBroken, EventLog
from residual.position.engine import decompose, fold
from tests.strategies import DAY0, event_stream

slow = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@given(st.integers(-10**12, 10**12), st.integers(-10**12, 10**12))
def test_money_addition_is_exact(a: int, b: int) -> None:
    assert (Money(a) + Money(b)).paise == a + b


@given(st.decimals(min_value=0, max_value=10**7, places=2))
def test_parse_round_trips_through_rupees(d) -> None:
    assert Money.parse(d).rupees == d.quantize(Money.parse(d).rupees)


def test_money_refuses_floats_and_cross_currency() -> None:
    with pytest.raises(TypeError):
        Money.parse(0.1)
    with pytest.raises(CurrencyMismatch):
        Money(100, "INR") + Money(100, "USD")


@given(event_stream())
@slow
def test_every_entry_balances(stream) -> None:
    for event in stream:
        entry = project(event)
        assert sum(p.amount.paise for p in entry.postings) == 0


def test_unbalanced_entry_cannot_be_constructed() -> None:
    from residual.ledger.accounts import Account

    with pytest.raises(Unbalanced):
        Entry(
            event_id="x", event_type="bogus", occurred_at=DAY0, recorded_at=DAY0,
            postings=(debit(Account.BANK, Money(500)), credit(Account.REVENUE, Money(400))),
        )


@given(event_stream())
@slow
def test_books_always_balance(stream) -> None:
    fold(stream).check()


@given(event_stream())
@slow
def test_reserves_never_go_contra(stream) -> None:
    balances = fold(stream)
    for acct in NEVER_NEGATIVE:
        bal = balances[acct].paise
        assert bal >= 0 if NORMAL_BALANCE[acct] is Side.DEBIT else bal <= 0


@given(event_stream())
@slow
def test_replay_is_deterministic(stream) -> None:
    assert fold(stream) == fold(stream)


@given(event_stream())
@slow
def test_as_of_never_uses_hindsight(stream) -> None:
    log = EventLog()
    log.extend(stream)
    for offset in (0, 15, 45, 90):
        cutoff = DAY0 + timedelta(days=offset)
        for e in log.as_of(occurred_by=cutoff, known_by=cutoff):
            assert e.occurred_at <= cutoff and e.recorded_at <= cutoff


@given(event_stream())
@slow
def test_variance_always_closes_to_zero(stream) -> None:
    v = decompose(stream, DAY0, DAY0 + timedelta(days=365))
    assert v.residual.paise == 0, (
        f"gap {v.gap} but movements explain {v.explained}; "
        f"an event moved money without a posting"
    )


@given(event_stream(), st.integers(0, 80), st.integers(1, 30))
@slow
def test_variance_closes_on_every_sub_window(stream, start_offset: int, length: int) -> None:
    start = DAY0 + timedelta(days=start_offset)
    v = decompose(stream, start, start + timedelta(days=length))
    assert v.residual.paise == 0


@given(event_stream())
@slow
def test_hash_chain_verifies(stream) -> None:
    log = EventLog()
    log.extend(stream)
    log.verify_chain()


@given(event_stream(max_payments=4))
@slow
def test_tampering_breaks_the_chain(stream) -> None:
    import json
    import tempfile

    log = EventLog()
    log.extend(stream)
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "e.jsonl"
    log.write_jsonl(path)

    lines = path.read_text().splitlines()
    row = json.loads(lines[0])
    row["event"]["event_id"] = "tampered"
    lines[0] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n")

    with pytest.raises(ChainBroken):
        EventLog.read_jsonl(path)


@given(st.integers(-10**9, 10**9), st.integers(-1000, 1000))
def test_scaling_by_an_integer_is_exact(paise: int, factor: int) -> None:
    assert (Money(paise) * factor).paise == paise * factor


def test_money_cannot_be_scaled_by_a_rate_without_saying_so() -> None:
    with pytest.raises(TypeError, match="apply_rate"):
        Money(10000) * 1.02  # type: ignore[operator]
    with pytest.raises(TypeError, match="apply_rate"):
        Money(10000) * True


def test_zero_is_falsey_and_anything_else_is_not() -> None:
    assert not Money.zero()
    assert Money(1) and Money(-1)


def test_sign_helpers_agree_with_the_arithmetic() -> None:
    m = Money.parse("-1234.56")
    assert abs(m) == Money.parse("1234.56")
    assert -m == Money.parse("1234.56")
    flipped = -m
    assert -flipped == m


def test_repr_round_trips() -> None:
    m = Money.parse("98765.43")
    assert eval(repr(m)) == m


@given(st.integers(-10**10, 10**10))
def test_formatting_never_loses_a_paise(paise: int) -> None:
    m = Money(paise)
    digits = "".join(ch for ch in str(m) if ch.isdigit())
    assert digits == f"{abs(paise):03d}"
    assert (str(m).startswith("-")) == (paise < 0)


@given(
    st.integers(-10**9, 10**9),
    st.lists(st.integers(0, 10_000), min_size=1, max_size=8),
)
def test_apportionment_never_loses_a_paise(paise: int, weights: list[int]) -> None:
    amount = Money(paise)
    if sum(weights) == 0 and paise != 0:
        with pytest.raises(ValueError, match="sum to zero"):
            allocate(amount, weights)
        return
    parts = allocate(amount, weights)
    assert sum(p.paise for p in parts) == paise
    assert len(parts) == len(weights)


@given(st.integers(1, 10**9), st.lists(st.integers(0, 1000), min_size=1, max_size=6))
def test_shares_follow_the_weights(paise: int, weights: list[int]) -> None:
    if sum(weights) == 0:
        return
    parts = allocate(Money(paise), weights)
    for (w1, p1), (w2, p2) in zip(zip(weights, parts), zip(weights[1:], parts[1:])):
        if w1 > w2:
            assert p1.paise >= p2.paise
        assert (p1.paise == 0) == (w1 == 0 and paise * w1 == 0) or w1 > 0


def test_apportionment_refuses_rather_than_vanishing() -> None:
    with pytest.raises(ValueError, match="sum to zero"):
        allocate(Money.parse("100"), [0, 0])
    with pytest.raises(ValueError, match="negative weights"):
        allocate(Money.parse("100"), [3, -1])
    assert allocate(Money.zero(), [0, 0]) == [Money.zero(), Money.zero()]


def test_thirds_of_a_rupee_still_make_a_rupee() -> None:
    assert allocate(Money.parse("100"), [1, 1, 1]) == [
        Money.parse("33.33"), Money.parse("33.33"), Money.parse("33.34")
    ]
