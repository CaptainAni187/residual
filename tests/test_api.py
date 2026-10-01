from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from residual.web.app import app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def recon_blob():
    return json.dumps(json.loads((FIXTURES / "recon_sample.json").read_text())).encode()


@pytest.fixture
def token(client, recon_blob):
    reply = client.post(
        "/api/close",
        files={"recon": ("recon.json", recon_blob, "application/json")},
        data={"contract": "card=2.90,upi=2.36"},
    )
    assert reply.status_code == 200, reply.text
    return reply.json()["token"]


def test_health_says_nothing_is_stored(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["stores_uploads"] is False


def test_an_uploaded_recon_closes_to_zero(client, recon_blob):
    body = client.post(
        "/api/close",
        files={"recon": ("recon.json", recon_blob, "application/json")},
        data={"contract": "card=2.90,upi=2.36"},
    ).json()
    assert body["residual_paise"] == 0
    assert body["closes"] is True
    assert body["covered"] is True
    assert body["findings"]
    assert sum(f["amount_paise"] for f in body["findings"]) == body["gap_paise"]


def test_every_finding_carries_the_query_behind_it(client, token):
    body = client.post(
        "/api/close",
        files={"recon": ("r.json", (FIXTURES / "recon_sample.json").read_bytes(), "application/json")},
        data={"contract": "card=2.90"},
    ).json()
    for finding in body["findings"]:
        assert finding["sql"].lower().startswith("select")


def _err(reply):
    return reply.json()["detail"]


def test_a_file_that_is_not_json_is_refused(client):
    reply = client.post("/api/close", files={"recon": ("x.json", b"nope", "application/json")})
    assert reply.status_code == 400
    assert _err(reply)["code"] == "not_json"
    assert _err(reply)["field"] == "recon"
    assert _err(reply)["fix"]


def test_an_empty_upload_is_refused(client):
    reply = client.post("/api/close", files={"recon": ("x.json", b"[]", "application/json")})
    assert reply.status_code == 400


def test_an_oversized_upload_is_refused(client):
    huge = b'[{"a":1}]' + b" " * (5 * 1024 * 1024)
    reply = client.post("/api/close", files={"recon": ("big.json", huge, "application/json")})
    assert reply.status_code == 413


def test_a_statement_that_does_not_tie_to_its_balance_is_refused(client, recon_blob):
    broken = (
        b"Date,Narration,Withdrawal,Deposit,Balance\n"
        b"01/03/2026,opening,0.00,100.00,1000.00\n"
        b"02/03/2026,credit,0.00,100.00,5555.00\n"
        b"03/03/2026,credit,0.00,100.00,5655.00\n"
    )
    reply = client.post(
        "/api/close",
        files={
            "recon": ("recon.json", recon_blob, "application/json"),
            "statement": ("bad.csv", broken, "text/csv"),
        },
    )
    assert reply.status_code == 422
    assert _err(reply)["code"] == "balance_mismatch"
    assert _err(reply)["field"] == "statement"


def test_an_unknown_token_is_not_found(client):
    reply = client.post("/api/explain", json={"token": "0" * 32, "cause": "normal_fee"})
    assert reply.status_code == 404
    assert _err(reply)["code"] == "session_expired"


def test_explaining_a_cause_that_is_not_there_is_not_found(client, token):
    reply = client.post("/api/explain", json={"token": token, "cause": "unicorns"})
    assert reply.status_code == 404


def test_an_explanation_without_a_key_is_written_from_the_verifier(client, token):
    body = client.post(
        "/api/explain", json={"token": token, "cause": "refunds_issued"}
    ).json()
    assert body["source"] == "offline"
    assert "INR" in body["text"]


def test_the_providers_endpoint_reports_what_is_configured(client):
    body = client.get("/api/providers").json()
    assert isinstance(body["configured"], list)
    assert body["using"]


def test_a_question_is_answered_from_the_catalogue(client, token):
    body = client.post(
        "/api/ask", json={"token": token, "question": "which settlements never arrived?"}
    ).json()
    assert body["source"].startswith("catalogue")
    assert body["sql"].lower().startswith("select") or body["sql"] == ""


def test_an_empty_question_is_rejected_before_it_runs(client, token):
    assert client.post("/api/ask", json={"token": token, "question": "x"}).status_code == 422


def test_on_balanced_books_the_agent_gives_a_second_opinion(client):
    token = client.post("/api/demo?seed=4242&week=8").json()["token"]
    body = client.post("/api/investigate", json={"token": token}).json()
    assert body["mode"] == "second_opinion"
    assert body["cause"]
    assert body["steps"]
    assert body["accepted"] is True


def test_uploads_are_capped_and_expire(client, recon_blob):
    from residual.web import api

    assert api.MAX_SESSIONS <= 64
    assert api.SESSION_TTL <= 60 * 60
    for _ in range(3):
        client.post(
            "/api/close", files={"recon": ("r.json", recon_blob, "application/json")}
        )
    assert len(api._SESSIONS) <= api.MAX_SESSIONS


def test_the_generated_merchant_differs_between_runs(client):
    sources = {client.post("/api/demo").json()["source"] for _ in range(3)}
    assert len(sources) > 1


def test_every_generated_merchant_still_closes_to_zero(client):
    for _ in range(3):
        body = client.post("/api/demo").json()
        assert body["residual_paise"] == 0, body["source"]
        assert body["covered"] is True


def test_a_pinned_seed_reproduces_the_same_merchant(client):
    first = client.post("/api/demo?seed=4242&week=3").json()
    again = client.post("/api/demo?seed=4242&week=3").json()
    assert first["source"] == again["source"]
    assert first["gap_paise"] == again["gap_paise"]


def test_a_bank_statement_is_read_and_ties_to_its_own_balance(client):
    blob = (FIXTURES / "statements" / "hdfc.csv").read_bytes()
    body = client.post(
        "/api/statement", files={"statement": ("hdfc.csv", blob, "text/csv")}
    ).json()
    assert body["rows"]
    assert body["ties_to_balance"] is True
    assert body["balances_checked"] > 0
    assert body["rows_disagreeing"] == 0
    assert body["credits_paise"] > 0


def test_a_statement_with_no_transactions_is_refused(client):
    reply = client.post(
        "/api/statement", files={"statement": ("empty.csv", b"Date,Narration\n", "text/csv")}
    )
    assert reply.status_code == 422


def test_gst_credit_reports_what_cannot_be_claimed(client):
    body = client.post(
        "/api/gst",
        files={
            "recon": ("r.json", (FIXTURES / "recon_march.json").read_bytes(), "application/json"),
            "gstr2b": ("g.json", (FIXTURES / "gst" / "gstr2b_march.json").read_bytes(), "application/json"),
        },
    ).json()
    assert body["paid_paise"] > 0
    assert body["invoices"] > 0
    assert body["at_risk_paise"] >= 0
    for risk in body["risks"]:
        assert risk["action"]


def test_an_unreadable_gst_return_is_refused(client):
    reply = client.post(
        "/api/gst",
        files={
            "recon": ("r.json", (FIXTURES / "recon_march.json").read_bytes(), "application/json"),
            "gstr2b": ("g.json", b"{}", "application/json"),
        },
    )
    assert reply.status_code == 422


def test_rates_that_are_not_rates_are_refused_on_the_right_field(client, recon_blob):
    for bad, needle in [("card=abc", "not a number"), ("bitcoin=2", "not a payment method"),
                        ("card=45", "outside"), ("card", "not a rate")]:
        reply = client.post(
            "/api/close",
            files={"recon": ("r.json", recon_blob, "application/json")},
            data={"contract": bad},
        )
        assert reply.status_code == 422, bad
        assert _err(reply)["code"] == "bad_rates"
        assert _err(reply)["field"] == "contract"
        assert needle in _err(reply)["message"], (bad, _err(reply)["message"])


def test_a_csv_uploaded_as_the_report_gets_a_specific_hint(client):
    reply = client.post(
        "/api/close", files={"recon": ("report.csv", b"id,amount,fee\n1,2,3\n", "text/csv")}
    )
    assert _err(reply)["code"] == "not_json"
    assert "CSV" in _err(reply)["fix"]


def test_an_empty_file_is_refused_before_it_is_parsed(client):
    reply = client.post("/api/close", files={"recon": ("r.json", b"", "application/json")})
    assert reply.status_code == 400
    assert _err(reply)["code"] == "empty_file"


def _locked_pdf(password: str) -> bytes:
    import io

    from reportlab.lib import pdfencrypt
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    page = canvas.Canvas(buf, encrypt=pdfencrypt.StandardEncryption(password, canPrint=1))
    page.drawString(72, 720, "statement")
    page.save()
    return buf.getvalue()


def test_a_locked_statement_asks_for_its_password(client):
    reply = client.post(
        "/api/statement",
        files={"statement": ("locked.pdf", _locked_pdf("s3cret"), "application/pdf")},
    )
    assert reply.status_code == 422
    assert _err(reply)["code"] == "needs_password"
    assert _err(reply)["field"] == "statement"


def test_a_wrong_password_is_told_apart_from_a_missing_one(client):
    reply = client.post(
        "/api/statement",
        files={"statement": ("locked.pdf", _locked_pdf("s3cret"), "application/pdf")},
        data={"password": "guess"},
    )
    assert _err(reply)["code"] == "bad_password"


def test_a_pdf_statement_is_read_and_checked(client):
    blob = (FIXTURES / "statements" / "hdfc.pdf").read_bytes()
    body = client.post("/api/statement", files={"statement": ("hdfc.pdf", blob, "application/pdf")}).json()
    assert body["rows"]
    assert body["ties_to_balance"] is True
    assert body["assumptions"]


def test_the_agent_drafts_a_payout_trace_with_the_real_utr(client):
    token = client.post("/api/demo?seed=4242&week=8").json()["token"]
    reply = client.post("/api/draft", json={"token": token, "kind": "escalate"})
    assert reply.status_code == 200
    body = reply.json()
    assert body["subject"] and body["body"] and body["to"]
    assert body["items"] >= 1
    assert body["source"] == "template"


def test_asking_for_a_draft_there_is_nothing_for_says_so(client):
    token = client.post("/api/demo?seed=4242&week=0").json()["token"]
    reply = client.post("/api/draft", json={"token": token, "kind": "payout_trace"})
    assert reply.status_code in (200, 409)
    if reply.status_code == 409:
        assert _err(reply)["code"] == "nothing_to_draft"


def test_an_unknown_draft_kind_is_refused(client, token):
    reply = client.post("/api/draft", json={"token": token, "kind": "love_letter"})
    assert reply.status_code == 422
    assert _err(reply)["code"] == "unknown_draft"


def test_a_backend_failure_is_a_message_not_a_traceback(client, monkeypatch):
    from residual.web import api

    def explode(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(api.Session, "close", explode)
    local = TestClient(app, raise_server_exceptions=False)
    reply = local.post("/api/demo?seed=4242&week=8")
    assert reply.status_code == 500
    assert _err(reply)["code"] == "server_error"
    assert "disk on fire" not in reply.text
    assert "Traceback" not in reply.text


LOCKED = FIXTURES / "statements" / "hdfc_locked.pdf"
LOCKED_PASSWORD = "HDFC1234"


def test_the_right_password_opens_a_real_locked_statement(client):
    body = client.post(
        "/api/statement",
        files={"statement": ("hdfc_locked.pdf", LOCKED.read_bytes(), "application/pdf")},
        data={"password": LOCKED_PASSWORD},
    ).json()
    assert len(body["rows"]) == 6
    assert body["ties_to_balance"] is True


def test_a_locked_statement_can_be_reconciled_with_its_password(client, recon_blob):
    reply = client.post(
        "/api/close",
        files={
            "recon": ("recon.json", recon_blob, "application/json"),
            "statement": ("hdfc_locked.pdf", LOCKED.read_bytes(), "application/pdf"),
        },
        data={"statement_password": LOCKED_PASSWORD},
    )
    assert reply.status_code == 200, reply.text
    assert "hdfc_locked.pdf" in reply.json()["source"]


def test_the_password_is_not_echoed_back_anywhere(client):
    reply = client.post(
        "/api/statement",
        files={"statement": ("hdfc_locked.pdf", LOCKED.read_bytes(), "application/pdf")},
        data={"password": LOCKED_PASSWORD},
    )
    assert LOCKED_PASSWORD not in reply.text


def test_inputs_say_what_the_data_covers_not_just_the_week_closed(client):
    inputs = client.post("/api/demo?seed=1002582&week=0").json()["inputs"]
    assert inputs["period"] == "2026-01-05 to 2026-01-11"
    assert inputs["covers"].startswith("2026-01-05")
    assert inputs["covers"] != inputs["period"]


def test_the_quarter_finds_the_fee_hike_in_the_right_week(client):
    token = client.post("/api/demo?seed=20260822&week=8").json()["token"]
    q = client.post("/api/quarter", json={"token": token}).json()
    assert q["residual_paise"] == 0
    assert len(q["weeks"]) == 13
    assert q["hike_started"] == "2026-02-02"
    assert sum(q["insight"]["totals"].values()) == q["gap_paise"]
    assert q["insight"]["actions"][0]["cause"] == "settlement_never_arrived"
    assert q["causes"] and q["headline"]


def test_every_result_carries_its_action_plan(client):
    body = client.post("/api/demo?seed=20260822&week=8").json()
    assert body["insight"]["recoverable_paise"] > 0
    assert [a["cause"] for a in body["insight"]["actions"]][:2] == ["settlement_never_arrived", "fee_rate_increase"]
