
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from residual.explain.close import run_close
from residual.explain.tax import RAZORPAY_GSTIN, assess, gst_input_credit, tds_deposited
from residual.ingest import gst, razorpay
from residual.ledger.money import Money

FIXTURES = Path(__file__).parent / "fixtures"
MARCH = (date(2026, 3, 1), date(2026, 3, 31))


@pytest.fixture(scope="module")
def events():
    rows = json.loads((FIXTURES / "recon_march.json").read_text())["items"]
    return razorpay.to_events(rows, on=date(2026, 3, 10))


@pytest.fixture(scope="module")
def gstr2b():
    return gst.load(FIXTURES / "gst" / "gstr2b_march.json")


def test_the_portal_json_shape_parses(gstr2b) -> None:
    assert gstr2b.period == "032026"
    assert len(gstr2b.invoices) == 3
    assert gstr2b.credit_from(RAZORPAY_GSTIN).paise > 0


def test_line_items_are_summed_to_the_invoice(gstr2b) -> None:
    invoice = gstr2b.from_supplier(RAZORPAY_GSTIN)[0]
    assert invoice.total_tax == invoice.igst + invoice.cgst + invoice.sgst + invoice.cess


def test_a_malformed_gstin_is_caught_before_filing(gstr2b) -> None:
    bad = gstr2b.malformed_gstins
    assert len(bad) == 1
    assert bad[0].supplier_gstin == "29XXXXX9999X9X9"


def test_the_flat_csv_export_parses_too() -> None:
    csv_text = (
        "GSTIN of supplier,Trade/Legal name,Invoice number,Invoice date,"
        "Taxable Value (Rs),Integrated Tax(Rs),Central Tax(Rs),State/UT Tax(Rs)\n"
        f"{RAZORPAY_GSTIN},RAZORPAY SOFTWARE PRIVATE LIMITED,RZP/1,15-03-2026,"
        "8186.00,0.00,736.74,736.74\n"
    )
    parsed = gst.parse_csv(csv_text)
    assert len(parsed.invoices) == 1
    assert parsed.credit_from(RAZORPAY_GSTIN) == Money.parse("1473.48")


def test_a_file_that_is_not_a_return_is_refused() -> None:
    with pytest.raises(gst.UnreadableReturn):
        gst.parse_csv("product,price\nwidget,100\n")


def test_a_late_supplier_filing_shows_up_as_credit_at_risk(events, gstr2b) -> None:
    risk = gst_input_credit(events, gstr2b, *MARCH)
    assert risk is not None and risk.material
    assert risk.amount == Money.parse("276.53")
    assert "16(2)(aa)" in risk.detail
    assert risk.action


def test_no_risk_is_reported_when_the_supplier_filed_everything(events) -> None:
    paid = sum(
        e.tax.paise for e in events if e.type == "payment_captured"
    )
    complete = gst.parse_csv(
        "GSTIN of supplier,Invoice number,Invoice date,Taxable Value (Rs),"
        "Integrated Tax(Rs),Central Tax(Rs),State/UT Tax(Rs)\n"
        f"{RAZORPAY_GSTIN},RZP/FULL,31-03-2026,100000.00,{paid / 100:.2f},0.00,0.00\n"
    )
    risk = gst_input_credit(events, complete, *MARCH)
    assert risk is not None and not risk.material
    assert risk.action == "nothing to chase"


@pytest.fixture(scope="module")
def withholding():
    from residual.ledger import select
    from residual.simulate.presets import BENCHMARK
    from residual.simulate.world import simulate

    world = simulate(BENCHMARK)
    events = world.log.events()
    withheld = sum(e.tds.paise for e in select.captures(events))
    assert withheld > 0, "the simulator stopped withholding; these tests are vacuous"
    return events, world.start, world.start + timedelta(days=BENCHMARK.days), Money(withheld)


def test_tds_is_reported_as_unverified_rather_than_silently_trusted(withholding) -> None:
    events, start, end, withheld = withholding
    risk = tds_deposited(events, start, end)
    assert risk is not None
    assert "not yet matched" in risk.title
    assert risk.amount == withheld
    assert "Form 26AS" in risk.action


def test_tds_fully_reflected_in_26as_is_not_a_risk(withholding) -> None:
    events, start, end, withheld = withholding
    risk = tds_deposited(events, start, end, per_form_26as=withheld)
    assert risk is not None and not risk.material
    assert risk.action == "nothing to chase"


def test_tds_short_of_26as_is_flagged(withholding) -> None:
    events, start, end, withheld = withholding
    short = Money(withheld.paise // 2)
    risk = tds_deposited(events, start, end, per_form_26as=short)
    assert risk is not None and risk.material
    assert risk.amount == withheld - short
    assert "not deposited" in risk.title


def test_risk_never_enters_the_variance_identity(events, gstr2b) -> None:
    plain = run_close(events, *MARCH, {"card": "2.00"})
    with_tax = run_close(events, *MARCH, {"card": "2.00"}, gstr2b=gstr2b)

    assert with_tax.risks and with_tax.at_risk.paise > 0
    assert with_tax.residual == plain.residual == Money.zero()
    assert with_tax.gap == plain.gap
    assert with_tax.explained == plain.explained
    assert with_tax.by_cause() == plain.by_cause()


def test_the_pack_carries_risks_outside_its_totals(events, gstr2b) -> None:
    from residual.explain import pack as packing
    from residual.ledger.store import EventLog

    log = EventLog()
    log.extend(events)
    close = run_close(events, *MARCH, {"card": "2.00"}, gstr2b=gstr2b)
    body = packing.build(close, log).body

    assert body["risks"]
    assert "risk" not in json.dumps(body["totals"])
    assert body["totals"]["residual"]["paise"] == 0


def test_assess_reports_nothing_when_given_nothing(events) -> None:
    assert assess(events, *MARCH) == [] or all(
        r.kind == "tds_194o" for r in assess(events, *MARCH)
    )


@pytest.mark.parametrize(
    "header",
    [
        "State/UT Tax(Rs)",
        "State/UT Tax(₹)",
        "State/UT Territory Tax",
        "SGST",
    ],
)
def test_every_spelling_of_the_state_tax_column_is_recognised(header: str) -> None:
    csv_text = (
        f"GSTIN of supplier,Invoice number,Invoice date,Taxable Value (Rs),"
        f"Central Tax(Rs),{header}\n"
        f"{RAZORPAY_GSTIN},RZP/1,15-03-2026,8186.00,736.74,736.74\n"
    )
    assert gst.parse_csv(csv_text).credit_from(RAZORPAY_GSTIN) == Money.parse("1473.48")
