
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from residual.ingest import razorpay
from residual.ledger.accounts import Account
from residual.ledger.money import Money
from residual.position.engine import InvariantViolation, fold

FIXTURE = Path(__file__).parent / "fixtures" / "recon_sample.json"
ON = date(2022, 6, 11)


@pytest.fixture(scope="module")
def rows():
    return json.loads(FIXTURE.read_text())["items"]


@pytest.fixture(scope="module")
def events(rows):
    return razorpay.to_events(rows, on=ON)


def test_every_row_type_is_mapped(events) -> None:
    kinds = {e.type for e in events}
    assert kinds == {
        "payment_captured", "refund_issued", "route_transfer",
        "gateway_adjustment", "settlement_executed",
    }


def test_real_field_names_produce_balanced_books(events) -> None:
    fold(events).check(complete=False)


def test_a_fully_settled_report_leaves_no_receivable(events) -> None:
    assert fold(events)[Account.GATEWAY_RECEIVABLE].paise == 0


def test_an_adjustment_never_touches_the_bank(events) -> None:
    balances = fold(events)
    assert balances[Account.GATEWAY_ADJUSTMENTS] == Money.parse("826")
    assert balances[Account.BANK].paise == 0, "an adjustment is netted, not debited"


def test_the_settlement_is_rebuilt_from_its_parts(events) -> None:
    settlement = next(e for e in events if e.type == "settlement_executed")
    assert settlement.net == Money.parse("2076.34")
    assert settlement.utr == "1568176960vxp0rj"
    assert len(settlement.covers) == 5


def test_money_survives_the_round_trip(events) -> None:
    captured = [e for e in events if e.type == "payment_captured"]
    assert sum(e.gross.paise for e in captured) == 600000
    assert sum(e.fee.paise for e in captured) == 14700


def test_it_refuses_to_run_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    with pytest.raises(razorpay.NotConfigured, match="test-mode keys only"):
        razorpay.fetch(ON)


def test_it_refuses_a_live_key(monkeypatch) -> None:
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_abcdefghij")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "not-a-real-secret")
    with pytest.raises(razorpay.NotConfigured, match="non-test key"):
        razorpay.fetch(ON)


def test_no_secret_reaches_the_url() -> None:
    import ast

    tree = ast.parse(Path(razorpay.__file__).read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "httpx"
    ]
    assert calls, "the fetch call disappeared; this test is now vacuous"

    for call in calls:
        keywords = {k.arg: k.value for k in call.keywords}
        assert "auth" in keywords, "credentials must travel as basic auth"

        params = keywords.get("params")
        assert params is not None
        names = {n.id for n in ast.walk(params) if isinstance(n, ast.Name)}
        assert not (names & {"key_id", "key_secret"}), (
            f"a credential reached the query string: {names}"
        )


BASE = {
    "entity_id": "pay_1", "type": "payment", "debit": 0, "credit": 97100,
    "amount": 100000, "currency": "INR", "fee": 2900, "tax": 0, "settled": True,
    "created_at": 1772496000, "settled_at": 1772496000, "settlement_id": "setl_1",
    "settlement_utr": "u1", "order_id": "o1", "method": "card",
}


def test_a_foreign_currency_row_is_refused_not_converted_at_par() -> None:
    with pytest.raises(razorpay.UnsupportedRow, match="INR-only"):
        razorpay.to_events([{**BASE, "currency": "USD"}], on=ON)


def test_an_unmapped_row_type_is_refused() -> None:
    with pytest.raises(razorpay.UnsupportedRow, match="unmapped row type"):
        razorpay.to_events([{**BASE, "type": "commission"}], on=ON)


def test_non_strict_skips_what_strict_refuses() -> None:
    events = razorpay.to_events(
        [{**BASE, "currency": "USD"}, BASE], on=ON, strict=False
    )
    captured = [e for e in events if e.type == "payment_captured"]
    assert len(captured) == 1


@pytest.mark.parametrize(
    "row",
    [
        {"on_hold": True, "settled": False, "settlement_id": None},
        {"amount": None, "fee": None, "tax": None, "credit": None},
        {"settlement_id": None},
        {"created_at": None},
        {"method": "paylater"},
        {"credit": 0, "debit": 0},
    ],
)
def test_awkward_but_legitimate_rows_still_balance(row: dict) -> None:
    fold(razorpay.to_events([{**BASE, **row}], on=ON)).check(complete=False)


def test_one_day_is_a_fragment_not_a_broken_ledger() -> None:
    refund_only = {
        **BASE, "entity_id": "rfnd_1", "type": "refund",
        "debit": 50000, "credit": 0, "amount": 50000, "payment_id": "pay_earlier",
    }
    events = razorpay.to_events([refund_only], on=ON)

    fold(events).check(complete=False)
    with pytest.raises(InvariantViolation):
        fold(events).check()
