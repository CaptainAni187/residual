"""The gateway report as the dashboard downloads it (CSV or Excel, rupees, dates as text) gives the same books as JSON."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from residual.ingest.razorpay import split_fee
from residual.ledger.money import Money
from residual.web.app import app

SAMPLES = Path(__file__).resolve().parents[1] / "public" / "samples"
RATES = json.loads((SAMPLES / "manifest.json").read_text())["rates"]
COLUMNS = ["entity_id", "type", "debit", "credit", "amount", "currency", "fee", "tax", "on_hold", "settled",
           "created_at", "settled_at", "settlement_id", "description", "payment_id", "settlement_utr", "order_id", "method"]


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _dashboard_rows(key: str) -> list[list[str]]:
    items = json.loads((SAMPLES / f"razorpay_report_{key}.json").read_text())["items"]

    def cell(row: dict, col: str) -> str:
        value = row.get(col)
        if value is None:
            return ""
        if col in ("debit", "credit", "amount", "fee", "tax"):
            return f"{int(value) / 100:.2f}"
        if col in ("created_at", "settled_at"):
            return datetime.fromtimestamp(int(value), tz=UTC).strftime("%d/%m/%Y %H:%M:%S")
        return str(value)

    return [["Settlement Recon Report"], [], [c.replace("_", " ").title() for c in COLUMNS]] + [
        [cell(r, c) for c in COLUMNS] for r in items
    ]


def _close(client, name: str, blob: bytes, key: str) -> dict:
    statement = (SAMPLES / f"bank_statement_{key}.csv").read_bytes()
    reply = client.post(
        "/api/close",
        files={"recon": (name, blob, "application/octet-stream"), "statement": ("s.csv", statement, "text/csv")},
        data={"contract": RATES},
    )
    assert reply.status_code == 200, reply.text
    return reply.json()


def _findings(body: dict) -> dict[str, int]:
    return {f["cause"]: f["amount_paise"] for f in body["findings"]}


@pytest.mark.parametrize("key", ["clean", "missing", "overcharged"])
def test_a_dashboard_csv_closes_exactly_like_the_json(client, key):
    buf = io.StringIO()
    csv.writer(buf).writerows(_dashboard_rows(key))
    from_csv = _close(client, "report.csv", buf.getvalue().encode(), key)
    from_json = _close(client, "report.json", (SAMPLES / f"razorpay_report_{key}.json").read_bytes(), key)
    assert from_csv["residual_paise"] == 0
    assert _findings(from_csv) == _findings(from_json)


def test_a_dashboard_excel_closes_exactly_like_the_json(client):
    book = openpyxl.Workbook()
    for row in _dashboard_rows("overcharged"):
        book.active.append(row)
    blob = io.BytesIO()
    book.save(blob)
    from_xlsx = _close(client, "report.xlsx", blob.getvalue(), "overcharged")
    from_json = _close(client, "report.json", (SAMPLES / "razorpay_report_overcharged.json").read_bytes(), "overcharged")
    assert _findings(from_xlsx) == _findings(from_json)


def test_a_pdf_uploaded_as_the_report_says_to_download_csv(client):
    reply = client.post("/api/close", files={"recon": ("r.pdf", b"%PDF-1.4 nothing", "application/pdf")})
    assert reply.json()["detail"]["code"] == "pdf_report"


def test_gst_is_counted_once_whichever_way_the_fee_is_reported():
    inclusive = {"amount": 10000, "fee": 236, "tax": 36, "credit": 9764}
    exclusive = {"amount": 10000, "fee": 200, "tax": 36, "credit": 9764}
    assert split_fee(inclusive) == (Money(200), Money(36))
    assert split_fee(exclusive) == (Money(200), Money(36))
    assert split_fee({"amount": 10000, "fee": 236, "tax": 36}) == (Money(200), Money(36)), "Razorpay's convention by default"
