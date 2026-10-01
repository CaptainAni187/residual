"""The downloadable samples on the site are real inputs, processed live — so each must still tell its story."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from residual.web.app import app

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "public" / "samples"
MANIFEST = json.loads((SAMPLES / "manifest.json").read_text())
TYPES = {".json": "application/json", ".csv": "text/csv", ".pdf": "application/pdf"}


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def upload(slot: str, name: str) -> tuple[str, tuple[str, bytes, str]]:
    path = SAMPLES / name
    return slot, (name, path.read_bytes(), TYPES[path.suffix])


def close(client, key: str) -> dict:
    files = MANIFEST["sets"][key]["files"]
    reply = client.post(
        "/api/close",
        files=dict(upload(slot, files[slot]) for slot in ("recon", "statement")),
        data={"contract": MANIFEST["rates"]},
    )
    assert reply.status_code == 200, reply.text
    return reply.json()


def amount(body: dict, cause: str) -> int:
    return sum(f["amount_paise"] for f in body["findings"] if f["cause"] == cause)


def test_every_tool_offers_at_least_three_samples_and_every_file_exists():
    for tool, keys in MANIFEST["tools"].items():
        assert len(keys) >= 3, tool
        for key in keys:
            for name in MANIFEST["sets"][key]["files"].values():
                assert (SAMPLES / name).is_file(), name


@pytest.mark.parametrize("key", ["clean", "missing", "overcharged"])
def test_every_report_sample_closes_to_zero_against_its_statement(client, key):
    body = close(client, key)
    assert body["residual_paise"] == 0
    assert len(body["inputs"]["files"]) == 2


def test_the_clean_sample_has_nothing_to_chase(client):
    body = close(client, "clean")
    assert amount(body, "settlement_never_arrived") == 0
    assert amount(body, "fee_rate_increase") == 0


def test_the_missing_sample_finds_the_lost_payout(client):
    assert amount(close(client, "missing"), "settlement_never_arrived") > 0


def test_the_overcharged_sample_finds_the_fee_hike(client):
    assert amount(close(client, "overcharged"), "fee_rate_increase") > 0


def _money(text: str) -> int:
    return round(float(text.replace(",", "")) * 100) if text else 0


def _drop_first_payout(text: str) -> bytes:
    """Delete the first gateway credit and re-run the closing balances, as a merchant editing the file would."""
    head, _, body = text.partition("Date,Narration")
    rows = list(csv.reader(io.StringIO("Date,Narration" + body)))
    header, rows = rows[0], rows[1:]
    gone = next(i for i, row in enumerate(rows) if "RAZORPAY" in row[1])
    lost = _money(rows[gone][5])
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    for i, row in enumerate(rows):
        if i == gone:
            continue
        if len(row) == 7 and i > gone:
            row[6] = f"{(_money(row[6]) - lost) / 100:,.2f}"
        writer.writerow(row)
    return (head + out.getvalue()).encode()


def test_editing_a_sample_changes_the_answer(client):
    """Results are computed from the files, not looked up: delete a payout from the statement and it is found missing."""
    files = MANIFEST["sets"]["clean"]["files"]
    edited = _drop_first_payout((SAMPLES / files["statement"]).read_text())
    reply = client.post(
        "/api/close",
        files={"recon": upload("recon", files["recon"])[1], "statement": ("edited.csv", edited, "text/csv")},
        data={"contract": MANIFEST["rates"]},
    )
    assert reply.status_code == 200, reply.text
    assert amount(reply.json(), "settlement_never_arrived") > 0


def test_editing_the_rates_changes_the_answer(client):
    files = MANIFEST["sets"]["clean"]["files"]
    reply = client.post(
        "/api/close",
        files=dict(upload(slot, files[slot]) for slot in ("recon", "statement")),
        data={"contract": MANIFEST["rates"].replace("card=2.00", "card=1.80")},
    )
    assert amount(reply.json(), "fee_rate_increase") > 0


def gst(client, key: str) -> dict:
    files = MANIFEST["sets"][key]["files"]
    reply = client.post("/api/gst", files=dict(upload(slot, files[slot]) for slot in ("recon", "gstr2b")))
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_the_gst_samples_tell_their_stories(client):
    assert gst(client, "gst_all")["at_risk_paise"] == 0
    assert gst(client, "gst_week")["at_risk_paise"] > 0
    assert any(r["kind"] == "malformed_gstin" for r in gst(client, "gst_bad")["risks"])


@pytest.mark.parametrize("key", MANIFEST["tools"]["statement"])
def test_every_statement_sample_reads_and_ties(client, key):
    sample = MANIFEST["sets"][key]
    reply = client.post(
        "/api/statement",
        files=dict([upload("statement", sample["files"]["statement"])]),
        data={"password": sample.get("password", "")},
    )
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["rows"]
    assert body["ties_to_balance"] is True


def test_the_locked_sample_needs_its_password(client):
    reply = client.post("/api/statement", files=dict([upload("statement", MANIFEST["sets"]["st_locked"]["files"]["statement"])]))
    assert reply.json()["detail"]["code"] == "needs_password"


def test_the_published_samples_match_the_generator(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("make_samples", ROOT / "scripts" / "make_samples.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "make_samples", module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "OUT", tmp_path / "samples")
    module.main()
    made = sorted(p.name for p in (tmp_path / "samples").iterdir())
    assert made == sorted(p.name for p in SAMPLES.iterdir())
    for name in made:
        assert (tmp_path / "samples" / name).read_bytes() == (SAMPLES / name).read_bytes(), name
