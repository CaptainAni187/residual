
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from residual.ingest import bank
from residual.ledger.accounts import Account
from residual.ledger.money import Money
from residual.position.engine import fold

STATEMENTS = Path(__file__).parent / "fixtures" / "statements"
BANKS = ["hdfc", "icici", "sbi", "axis"]


@pytest.mark.parametrize(
    "text,paise,sign",
    [
        ("1,234.56", 123456, 0),
        ("12,34,567.89", 123456789, 0),
        ("1234567.89", 123456789, 0),
        ("1234.56 Cr", 123456, 1),
        ("5000.00 Dr", 500000, -1),
        ("(1,234.56)", 123456, -1),
        ("-987.65", 98765, -1),
        ("₹ 1,000.00", 100000, 0),
        ("0.00", 0, 0),
    ],
)
def test_amounts_survive_however_the_bank_wrote_them(text, paise, sign) -> None:
    amount, got_sign = bank.parse_amount(text)
    assert amount is not None and amount.paise == paise
    assert got_sign == sign


@pytest.mark.parametrize("blank", ["", "  ", "-", "--", "NA", "N/A"])
def test_a_blank_cell_is_not_a_zero(blank) -> None:
    assert bank.parse_amount(blank) == (None, 0)
    assert bank.parse_amount("0.00")[0] == Money.zero()


@pytest.mark.parametrize(
    "text",
    ["05/03/2026", "05/03/26", "05-03-2026", "05-Mar-2026", "5 March 2026", "2026-03-05"],
)
def test_dates_survive_however_the_bank_wrote_them(text) -> None:
    assert bank.parse_date(text) == date(2026, 3, 5)


def test_junk_is_not_mistaken_for_a_date() -> None:
    for junk in ["", "Total", "*** End of Statement ***", "32/13/2026"]:
        assert bank.parse_date(junk) is None


@pytest.mark.parametrize("name", BANKS)
def test_every_bank_format_parses_and_proves_itself(name) -> None:
    parsed = bank.load(STATEMENTS / f"{name}.csv")
    assert parsed.rows, f"{name}: nothing parsed"
    assert parsed.verifiable, f"{name}: no balance column to check against"
    assert parsed.reconciles, f"{name}: {parsed.report()}"


@pytest.mark.parametrize("name", BANKS)
def test_the_header_is_found_under_the_preamble(name) -> None:
    parsed = bank.load(STATEMENTS / f"{name}.csv")
    assert parsed.header_line > 1
    assert {"txn_date", "narration"} <= set(parsed.columns)


def test_an_indicator_column_is_not_read_as_a_debit_column() -> None:
    parsed = bank.load(STATEMENTS / "axis.csv")
    assert parsed.columns.get("indicator") is not None
    assert "debit" not in parsed.columns
    credits = [row for row in parsed.rows if row.credit.paise]
    assert len(credits) == 2, "the CR rows were read as debits"


def test_footers_and_blank_lines_are_ignored_not_parsed() -> None:
    parsed = bank.load(STATEMENTS / "hdfc.csv")
    assert any("End of Statement" in note for _, note in parsed.skipped)
    assert all(row.txn_date.year == 2026 for row in parsed.rows)


def test_a_misread_statement_is_reported_not_imported() -> None:
    original = (STATEMENTS / "hdfc.csv").read_text()
    target = '"9,39,365.80"'
    assert target in original, "the fixture changed; pick another balance to corrupt"
    text = original.replace(target, '"9,99,999.99"')
    parsed = bank.parse(text)
    assert not parsed.reconciles
    assert "the parse is wrong, not the bank" in parsed.report()
    assert parsed.balance_rate < 1.0


def test_a_file_that_is_not_a_statement_is_refused() -> None:
    with pytest.raises(bank.UnreadableStatement, match="looks like a statement header"):
        bank.parse("name,quantity\nwidget,4\ngadget,7\n")


def test_merchant_spending_is_not_counted_as_a_bank_charge() -> None:
    events = bank.to_events(bank.load(STATEMENTS / "hdfc.csv"))
    charges = [e for e in events if e.type == "bank_charge_applied"]
    other = [e for e in events if e.type == "bank_debit"]

    assert [e.amount for e in charges] == [Money.parse("826")]
    assert {e.amount for e in other} == {Money.parse("45000"), Money.parse("285000")}


def test_statement_events_keep_the_books_balanced() -> None:
    events = bank.to_events(bank.load(STATEMENTS / "hdfc.csv"))
    balances = fold(events)
    balances.check()
    assert balances[Account.BANK].paise != 0


def test_a_credit_is_never_guessed_onto_a_payout() -> None:
    events = bank.to_events(bank.load(STATEMENTS / "hdfc.csv"))
    credits = [e for e in events if e.type == "bank_credit_received"]
    assert credits
    assert all(e.narration for e in credits)
    assert not hasattr(credits[0], "settlement_id")


PDF = STATEMENTS / "hdfc.pdf"


def test_a_pdf_statement_parses_and_proves_itself() -> None:
    parsed = bank.load_any(PDF)
    assert parsed.rows
    assert parsed.verifiable and parsed.reconciles, parsed.report()


def test_the_balance_check_picks_the_extraction_strategy() -> None:
    parsed = bank.load_any(PDF)
    assert parsed.strategy == "word coordinates"
    assert parsed.reconciles


def test_a_pdf_and_its_csv_agree_row_for_row() -> None:
    from_pdf = bank.load_any(PDF)
    from_csv = bank.load_any(STATEMENTS / "hdfc.csv")

    assert len(from_pdf.rows) == len(from_csv.rows)
    for a, b in zip(from_pdf.rows, from_csv.rows):
        assert a.txn_date == b.txn_date
        assert a.debit == b.debit
        assert a.credit == b.credit
        assert a.balance == b.balance


def test_a_multi_word_heading_stays_one_column() -> None:
    parsed = bank.load_any(PDF)
    assert "balance" in parsed.columns
    assert all(row.balance is not None for row in parsed.rows)


def test_a_pdf_with_no_extractable_text_says_so(tmp_path) -> None:
    blank = tmp_path / "scan.pdf"
    blank.write_bytes(b"%PDF-1.4\n%%EOF\n")
    with pytest.raises(bank.UnreadableStatement):
        bank.load_any(blank)


def test_format_is_detected_from_content_not_the_extension(tmp_path) -> None:
    disguised = tmp_path / "statement.csv"
    disguised.write_bytes(PDF.read_bytes())
    assert bank.load_any(disguised).reconciles


def test_the_same_statement_links_the_same_way_in_both_formats() -> None:
    import json
    from datetime import date

    from residual.ingest import razorpay
    from residual.ledger.warehouse import Warehouse
    from residual.recon.linkage import link_credits

    fixtures = Path(__file__).parent / "fixtures"
    rows = json.loads((fixtures / "recon_march.json").read_text())["items"]
    gateway = razorpay.to_events(rows, on=date(2026, 3, 10))

    resolved = {}
    for fmt in ("csv", "pdf"):
        statement = bank.load_any(STATEMENTS / f"hdfc.{fmt}")
        events = sorted(
            gateway + bank.to_events(statement), key=lambda e: (e.occurred_at, e.event_id)
        )
        links = link_credits(Warehouse.build(events))
        resolved[fmt] = sorted(link.settlement_id for link in links if link.linked)

    assert resolved["csv"] == resolved["pdf"], resolved
    assert len(resolved["csv"]) == 3


def test_a_truncated_reference_is_not_forced_into_a_prefix_match() -> None:
    statement = bank.load_any(STATEMENTS / "hdfc.pdf")
    first = next(row for row in statement.rows if row.credit.paise)
    assert first.narration.endswith("202603"), "the fixture stopped truncating"
    assert "20260302411907" not in first.narration


def _statement(body: str) -> str:
    return "Date,Narration,Withdrawal Amt.,Deposit Amt.,Closing Balance\n" + body


def test_an_overdraft_balance_keeps_its_sign() -> None:
    parsed = bank.parse(_statement(
        '02/03/26,LARGE PAYMENT,"15,000.00",,"-4,000.00"\n'
        '03/03/26,NEFT CR-RAZORPAY,,"5,000.00","1,000.00"\n'
    ))
    assert parsed.rows[0].balance == Money.parse("-4000")
    assert parsed.reconciles


def test_a_running_balance_printed_only_some_rows_still_verifies() -> None:
    parsed = bank.parse(_statement(
        '02/03/26,NEFT CR-A,,"1,000.00",\n'
        '02/03/26,NEFT CR-B,,"2,000.00",\n'
        '02/03/26,CHRG,100.00,,"12,900.00"\n'
        '03/03/26,NEFT CR-C,,"500.00",\n'
        '03/03/26,CHRG,50.00,,"13,350.00"\n'
    ))
    assert len(parsed.rows) == 5
    assert parsed.reconciles


def test_a_wrong_balance_across_a_gap_is_still_caught() -> None:
    parsed = bank.parse(_statement(
        '02/03/26,NEFT CR-A,,"1,000.00",\n'
        '02/03/26,CHRG,100.00,,"12,900.00"\n'
        '03/03/26,NEFT CR-C,,"500.00",\n'
        '03/03/26,CHRG,50.00,,"13,999.99"\n'
    ))
    assert not parsed.reconciles


def test_an_opening_balance_row_anchors_the_check() -> None:
    parsed = bank.parse(_statement(
        '01/03/26,OPENING BALANCE,,,"10,000.00"\n'
        '02/03/26,NEFT CR-RAZORPAY-123,,"1,000.00","11,000.00"\n'
    ))
    assert len(parsed.rows) == 1, "the opening balance is not a transaction"
    assert parsed.reconciles
    assert any("opening balance" in note for _, note in parsed.skipped)


def test_the_report_says_which_thing_went_wrong() -> None:
    no_column = bank.parse(
        "Date,Narration,Withdrawal Amt.,Deposit Amt.\n02/03/26,X,,\"1,000.00\"\n"
    )
    assert "carries no balance column" in no_column.report()

    one_balance = bank.parse(_statement('02/03/26,X,,"1,000.00","11,000.00"\n'))
    assert "only 1 balance figure" in one_balance.report()
    assert "balance" in one_balance.columns


def test_a_narration_containing_a_comma_survives_quoting() -> None:
    parsed = bank.parse(_statement(
        '02/03/26,"NEFT CR-ACME PVT LTD, MUMBAI-123",,"1,000.00","11,000.00"\n'
        '03/03/26,"UPI/PAY, VENDOR/9845",500.00,,"10,500.00"\n'
    ))
    assert parsed.reconciles
    assert "MUMBAI" in parsed.rows[0].narration


def test_a_windows_export_with_a_bom_and_crlf_parses(tmp_path) -> None:
    path = tmp_path / "win.csv"
    path.write_bytes(
        "﻿Date,Narration,Withdrawal Amt.,Deposit Amt.,Closing Balance\r\n"
        '02/03/26,"NEFT CR-RAZORPAY-123",,"1,000.00","11,000.00"\r\n'
        "03/03/26,CHRG,100.00,,\"10,900.00\"\r\n".encode()
    )
    assert bank.load(path).reconciles


def test_a_cp1252_statement_is_decoded(tmp_path) -> None:
    path = tmp_path / "legacy.csv"
    path.write_bytes(
        "Date,Narration,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
        '01/03/26,OPENING,,,"10,000.00"\n'
        '02/03/26,NEFT CR-CAFÉ MÜNCHEN-123,,"1,000.00","11,000.00"\n'.encode("cp1252")
    )
    parsed = bank.load(path)
    assert parsed.reconciles
    assert "CAFÉ" in parsed.rows[0].narration


def test_a_summary_block_after_the_transactions_is_ignored() -> None:
    parsed = bank.parse(_statement(
        '01/03/26,OPENING,,,"10,000.00"\n'
        '02/03/26,NEFT CR-RAZORPAY-123,,"1,000.00","11,000.00"\n'
        ",,,,\n"
        ",STATEMENT SUMMARY,,,\n"
        ',Total Credits,,,"1,000.00"\n'
    ))
    assert len(parsed.rows) == 1
    assert parsed.reconciles


def test_a_pathological_field_is_our_error_not_the_csv_modules() -> None:
    monster = "Date,Narration,Debit,Credit,Balance\n" + "x" * (bank.MAX_FIELD + 10)
    with pytest.raises(bank.UnreadableStatement, match="not a narration"):
        bank.parse(monster)


def test_a_file_past_the_size_ceiling_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bank, "MAX_BYTES", 1024)
    path = tmp_path / "huge.csv"
    path.write_text("Date,Narration,Debit,Credit,Balance\n" + "02/03/26,x,,1.00,1.00\n" * 200)
    with pytest.raises(bank.UnreadableStatement, match="ceiling"):
        bank.load(path)


def test_two_hundred_thousand_rows_parse_completely() -> None:
    rows = "Date,Narration,Debit,Credit,Balance\n" + "".join(
        f"02/03/26,N{i},,1.00,{i + 1}.00\n" for i in range(200_000)
    )
    parsed = bank.parse(rows)
    assert len(parsed.rows) == 200_000
    assert parsed.reconciles, "a long statement must still verify against itself"
