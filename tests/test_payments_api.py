from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from residual.ingest.razorpay import UnsupportedRow, payments_to_events, refunds_to_events
from residual.ledger.accounts import Account
from residual.ledger.money import Money
from residual.position.engine import fold

FIXTURE = Path(__file__).parent / "fixtures" / "razorpay" / "payments.json"


@pytest.fixture
def rows():
    return deepcopy(json.loads(FIXTURE.read_text())["items"])


def test_captured_payments_become_captures(rows):
    events = payments_to_events(rows, strict=False)
    captures = [e for e in events if e.type == "payment_captured"]
    assert {e.payment_id for e in captures} == {"pay_TXXcaptured01", "pay_TXXcard0002"}
    first = next(e for e in captures if e.payment_id == "pay_TXXcaptured01")
    assert first.gross == Money(129900)
    assert first.fee == Money(2598)
    assert first.tax == Money(468)
    assert first.fee + first.tax == Money(3066)
    assert str(first.method) == "upi"


def test_a_failed_payment_is_recorded_as_failed_not_captured(rows):
    events = payments_to_events(rows, strict=False)
    failed = [e for e in events if e.type == "payment_failed"]
    assert len(failed) == 1
    assert failed[0].payment_id == "pay_TXXfailed003"


def test_a_refund_reported_but_not_supplied_is_refused(rows):
    with pytest.raises(UnsupportedRow, match="no refund record was supplied"):
        payments_to_events(rows)


def test_an_authorised_but_uncaptured_payment_moves_no_money(rows):
    events = payments_to_events(rows, strict=False)
    assert not [e for e in events if getattr(e, "payment_id", "") == "pay_TXXauthed004"]


def test_customer_details_never_reach_the_ledger(rows):
    events = payments_to_events(rows, strict=False)
    blob = json.dumps([e.model_dump(mode="json") for e in events])
    for leak in ["buyer@example.com", "+919999999999", "buyer@okhdfcbank", "do not ship"]:
        assert leak not in blob


def test_the_books_balance_on_real_api_shapes(rows):
    fold(payments_to_events(rows, strict=False)).check(complete=False)


def test_a_foreign_currency_payment_is_refused(rows):
    rows[0]["currency"] = "USD"
    with pytest.raises(UnsupportedRow, match="INR-only"):
        payments_to_events(rows)


def test_an_unmapped_method_is_refused(rows):
    rows[0]["method"] = "cardless_emi"
    with pytest.raises(UnsupportedRow, match="unmapped payment method"):
        payments_to_events(rows)


def test_an_unmapped_status_is_refused(rows):
    rows[0]["status"] = "quantum_superposition"
    with pytest.raises(UnsupportedRow, match="unmapped payment status"):
        payments_to_events(rows)


def test_a_payment_with_no_id_is_refused(rows):
    rows[0]["id"] = ""
    with pytest.raises(UnsupportedRow, match="no id"):
        payments_to_events(rows, strict=False)


def test_lenient_mode_drops_what_it_cannot_map_instead_of_raising(rows):
    rows[0]["currency"] = "USD"
    rows[1]["status"] = "nonsense"
    events = payments_to_events(rows, strict=False)
    assert {getattr(e, "payment_id", "") for e in events} == {"pay_TXXfailed003"}


LIVE = Path(__file__).parent / "fixtures" / "razorpay" / "live_payment.json"
LIVE_REFUNDS = Path(__file__).parent / "fixtures" / "razorpay" / "live_refund.json"


@pytest.fixture
def live():
    return deepcopy(json.loads(LIVE.read_text())["items"])


@pytest.fixture
def live_refunds():
    return deepcopy(json.loads(LIVE_REFUNDS.read_text())["items"])


def test_the_real_capture_count_matches_what_the_api_reported(live, live_refunds):
    events = payments_to_events(live, live_refunds)
    assert sum(1 for e in events if e.type == "payment_captured") == 4
    assert sum(1 for e in events if e.type == "payment_failed") == 1


def test_every_real_capture_splits_the_fee_the_way_razorpay_bills_it(live, live_refunds):
    by_id = {r["id"]: r for r in live}
    for event in payments_to_events(live, live_refunds):
        if event.type != "payment_captured":
            continue
        billed = by_id[event.payment_id]["fee"]
        assert event.fee + event.tax == Money(billed)
        assert event.fee.paise > 0


def test_every_real_capture_lands_on_the_published_rate(live, live_refunds):
    for event in payments_to_events(live, live_refunds):
        if event.type != "payment_captured":
            continue
        assert event.fee.paise / event.gross.paise == pytest.approx(0.02, abs=0.0001)
        assert event.tax.paise / event.fee.paise == pytest.approx(0.18, abs=0.0010)


def test_a_real_failed_payment_moves_no_money(live, live_refunds):
    events = payments_to_events(live, live_refunds)
    failed = next(e for e in events if e.type == "payment_failed")
    assert failed.gross == Money(129900)
    postings = fold([failed])
    assert all(amount.paise == 0 for amount in postings.values())


def test_the_real_payments_land_in_balancing_books(live, live_refunds):
    balances = fold(payments_to_events(live, live_refunds))
    balances.check(complete=False)
    assert balances[Account.REVENUE] == Money(-978400)
    assert balances[Account.FEE_EXPENSE] == Money(19568)
    assert balances[Account.GST_INPUT_CREDIT] == Money(3522)
    assert balances[Account.GATEWAY_RECEIVABLE] == Money(705310)
    assert balances[Account.REFUNDS] == Money(250000)
    net = (
        balances[Account.FEE_EXPENSE]
        + balances[Account.GST_INPUT_CREDIT]
        + balances[Account.GATEWAY_RECEIVABLE]
        + balances[Account.REFUNDS]
    )
    assert net == -balances[Account.REVENUE]


def test_tax_larger_than_the_fee_it_belongs_to_is_refused(live, live_refunds):
    live[0]["tax"] = live[0]["fee"] + 1
    with pytest.raises(UnsupportedRow, match="exceeds the fee"):
        payments_to_events(live, live_refunds)


def test_a_real_refund_keeps_its_own_id_and_its_own_date(live, live_refunds):
    events = payments_to_events(live, live_refunds)
    refund = next(e for e in events if e.type == "refund_issued")
    real = live_refunds[0]

    assert refund.refund_id == real["id"]
    assert refund.refund_id.startswith("rfnd_")
    assert refund.amount == Money(real["amount"])
    assert refund.occurred_at == datetime.fromtimestamp(real["created_at"], tz=UTC).date()


def test_the_refund_is_not_dated_at_the_capture_it_belongs_to(live, live_refunds):
    events = payments_to_events(live, live_refunds)
    refund = next(e for e in events if e.type == "refund_issued")
    capture = next(
        e for e in events
        if e.type == "payment_captured" and e.payment_id == refund.payment_id
    )
    paid = next(r for r in live if r["id"] == refund.payment_id)
    real = live_refunds[0]

    assert real["created_at"] > paid["created_at"]
    assert refund.occurred_at >= capture.occurred_at


def test_a_failed_refund_posts_nothing(live_refunds):
    live_refunds[0]["status"] = "failed"
    assert refunds_to_events(live_refunds) == []


def test_the_real_refund_leaves_the_books_balanced(live, live_refunds):
    balances = fold(payments_to_events(live, live_refunds))
    balances.check(complete=False)
    assert balances[Account.REFUNDS] == Money(250000)
    assert balances[Account.GATEWAY_RECEIVABLE] == Money(705310)
