
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from residual.web.app import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_the_close_renders(client) -> None:
    r = client.get("/?week=8")
    assert r.status_code == 200
    for expected in ("gross captured", "cash landed", "gap", "residual"):
        assert expected in r.text


def test_planted_failures_are_visible_on_the_page(client) -> None:
    r = client.get("/?week=8").text
    assert "settlement_never_arrived" in r
    assert "fee_rate_increase" in r


def test_every_cause_drills_down_to_a_query_that_runs(client) -> None:
    from datetime import timedelta

    from residual.explain.close import run_close
    from residual.web.app import _state

    s = _state()
    start = s["world"].start + timedelta(days=56)
    close = run_close(s["events"], start, start + timedelta(days=6), s["contracted"], s["wh"])

    assert close.findings
    for f in close.findings:
        r = client.get(f"/evidence/8/{f.cause}")
        assert r.status_code == 200
        assert "query failed" not in r.text, f"{f.cause}: evidence did not run"
        assert "SELECT" in r.text or "WITH" in r.text


def test_an_unknown_cause_does_not_blow_up(client) -> None:
    r = client.get("/evidence/8/not_a_real_cause")
    assert r.status_code == 200
    assert "no such finding" in r.text


def test_every_week_of_the_run_renders(client) -> None:
    for week in range(13):
        assert client.get(f"/?week={week}").status_code == 200


def test_the_page_is_self_contained(client) -> None:
    text = client.get("/?week=8").text
    assert "http://" not in text.replace("http://www.w3.org", "")
    assert "cdn." not in text
    assert "<style>" in text and "<script>" in text


def test_every_panel_is_on_the_page(client) -> None:
    text = client.get("/?week=8").text
    for panel in (
        "Ask the ledger", "Cash outlook", "Restated since signing",
        "Exceptions", "Proof", "Memo",
    ):
        assert panel in text, panel


def test_the_question_box_answers_and_shows_its_query(client) -> None:
    r = client.get("/ask", params={"q": "which settlements never arrived?"})
    assert r.status_code == 200
    assert "SELECT" in r.text
    assert "settlement_executed" in r.text


def test_the_question_box_refuses_what_is_not_a_question(client) -> None:
    r = client.get("/ask", params={"q": "DROP TABLE events"})
    assert r.status_code == 200
    assert "no catalogue entry matches" in r.text
    assert client.get("/?week=8").status_code == 200, "the table survived"


def test_an_empty_question_does_not_error(client) -> None:
    assert client.get("/ask", params={"q": ""}).status_code == 200


def test_the_outlook_keeps_committed_and_projected_apart(client) -> None:
    text = client.get("/?week=8").text
    assert "committed" in text and "projected" in text
    assert "only one of them can be spent against" in text


def test_the_outlook_marks_days_the_bank_is_shut(client) -> None:
    assert "bank closed" in client.get("/?week=8").text


def test_the_restatement_panel_states_one_of_its_two_outcomes(client) -> None:
    text = client.get("/?week=8").text
    restated = "as signed" in text and "as known now" in text
    unchanged = "nothing arrived late" in text.lower()
    assert restated or unchanged, "the panel said neither"


def test_some_week_of_the_run_is_restated(client) -> None:
    assert any(
        "as known now" in client.get(f"/?week={week}").text for week in range(13)
    ), "no week in the run was restated"


def test_page_content_is_escaped(client) -> None:
    from residual.web.app import _state

    _state()
    r = client.get("/evidence/8/settlement_never_arrived")
    assert "<script" not in r.text.lower()


def test_hostile_text_from_a_narration_cannot_reach_the_page_unescaped(client) -> None:
    r = client.get("/ask", params={"q": "<img src=x onerror=alert(1)> balances"})
    assert "<img src=x" not in r.text
    assert r.status_code == 200

    page = client.get("/?week=8").text
    assert page.count("<script>") == 1
    assert "onerror=" not in page


def test_the_outlook_shows_a_floor_or_says_why_not(client) -> None:
    text = client.get("/?week=8").text
    certified = "at least" in text
    explained = "too little history" in text and "certify-forecast" in text
    assert certified or explained
    assert "adaptive conformal bound" in text
