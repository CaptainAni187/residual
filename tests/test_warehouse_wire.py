from __future__ import annotations

from datetime import date

import duckdb
import pytest

from residual.ledger.warehouse import _COVER_COLS, _insert


@pytest.fixture
def con():
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE settlement_covers ("
        "settlement_id VARCHAR, utr VARCHAR, payment_id VARCHAR, settled_on DATE)"
    )
    return connection


def _round_trip(con, rows):
    _insert(con, "settlement_covers", rows, _COVER_COLS)
    return con.execute(
        "SELECT settlement_id, utr, payment_id, settled_on FROM settlement_covers"
    ).fetchall()


def test_an_empty_string_does_not_become_null(con):
    rows = [("setl_1", "", None, date(2026, 3, 2))]
    got = _round_trip(con, rows)[0]
    assert got[1] == ""
    assert got[1] is not None
    assert got[2] is None


def test_the_difference_survives_a_sql_comparison(con):
    _insert(
        con,
        "settlement_covers",
        [("a", "", "p", date(2026, 3, 2)), ("b", None, "p", date(2026, 3, 2))],
        _COVER_COLS,
    )
    blank = con.execute("SELECT count(*) FROM settlement_covers WHERE utr = ''").fetchone()
    missing = con.execute("SELECT count(*) FROM settlement_covers WHERE utr IS NULL").fetchone()
    assert blank is not None and blank[0] == 1
    assert missing is not None and missing[0] == 1


@pytest.mark.parametrize(
    "text",
    [
        'NEFT, HDFC "quoted" reference',
        "line\nbreak inside a narration",
        "trailing space   ",
        "unicode ₹ ü 中文",
        "comma,separated,looking",
        "tab\tseparated\tlooking",
        "NULL",
        "\\",
    ],
)
def test_awkward_text_survives_the_wire(con, text):
    assert _round_trip(con, [("setl_1", text, "pay_1", date(2026, 3, 2))])[0][1] == text


def test_dates_arrive_as_dates_not_strings(con):
    got = _round_trip(con, [("setl_1", "u", "p", date(2026, 3, 8))])[0]
    assert got[3] == date(2026, 3, 8)


def test_a_missing_date_stays_missing(con):
    assert _round_trip(con, [("setl_1", "u", "p", None)])[0][3] is None


def test_no_temporary_file_is_left_behind(con, tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _insert(con, "settlement_covers", [("s", "u", "p", date(2026, 3, 2))], _COVER_COLS)
    assert list(tmp_path.iterdir()) == []


def test_a_row_of_the_wrong_width_is_refused(con):
    with pytest.raises(ValueError):
        _insert(con, "settlement_covers", [("only", "three", "values")], _COVER_COLS)
