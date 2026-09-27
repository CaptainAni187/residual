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


def test_a_file_that_is_not_json_is_refused(client):
    reply = client.post("/api/close", files={"recon": ("x.json", b"nope", "application/json")})
    assert reply.status_code == 400
    assert "not JSON" in reply.json()["detail"]


def test_an_empty_upload_is_refused(client):
    reply = client.post("/api/close", files={"recon": ("x.json", b"[]", "application/json")})
    assert reply.status_code == 400


def test_an_oversized_upload_is_refused(client):
    huge = b'[{"a":1}]' + b" " * (5 * 1024 * 1024)
    reply = client.post("/api/close", files={"recon": ("big.json", huge, "application/json")})
    assert reply.status_code == 413


def test_a_statement_that_does_not_tie_to_its_balance_is_refused(client, recon_blob):
    broken = b"Date,Narration,Withdrawal,Deposit,Balance\n01/03/2026,x,0.00,100.00,999999.00\n"
    reply = client.post(
        "/api/close",
        files={
            "recon": ("recon.json", recon_blob, "application/json"),
            "statement": ("bad.csv", broken, "text/csv"),
        },
    )
    assert reply.status_code == 422
    assert "running balance" in reply.json()["detail"]


def test_an_unknown_token_is_not_found(client):
    reply = client.post("/api/explain", json={"token": "0" * 32, "cause": "normal_fee"})
    assert reply.status_code == 404
    assert "expired" in reply.json()["detail"]


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


def test_investigating_books_that_already_close_is_a_conflict(client, token):
    reply = client.post("/api/investigate", json={"token": token})
    assert reply.status_code == 409
    assert "nothing open" in reply.json()["detail"]


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
