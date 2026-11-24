"""The provider router: tried in order, falls through on failure, and the investigator always ends with an answer."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from residual.agents import chat, loop
from residual.web.app import app

KEYS = ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "ANTHROPIC_API_KEY")


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.replace("API_KEY", "MODEL"), raising=False)
    monkeypatch.setattr(loop, "_LIVE", {})
    monkeypatch.setattr(loop, "_LISTED", {})

    def offline(url, **kwargs):
        raise httpx.ConnectError("no network in tests")

    monkeypatch.setattr(httpx, "get", offline)


def _reply(status: int, text: str = "ok") -> httpx.Response:
    request = httpx.Request("POST", "https://example.test/chat/completions")
    return httpx.Response(status, json={"choices": [{"message": {"content": text}}]}, request=request)


def test_providers_are_tried_in_a_fixed_order(monkeypatch):
    for key in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(key, "k")
    assert chat.available() == ["groq", "gemini", "openrouter"]


def test_a_failing_provider_hands_over_to_the_next(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    seen: list[str] = []

    def post(url, **kwargs):
        seen.append(url)
        return _reply(401) if "groq" in url else _reply(200, "from gemini")

    monkeypatch.setattr(httpx, "post", post)
    speaker = chat.Chat()
    assert speaker.say("s", "p") == "from gemini"
    assert speaker.provider == "gemini"
    assert speaker.failures == ["groq: HTTP 401"]
    assert speaker.describe().startswith("gemini/")


def test_when_every_provider_fails_the_reason_names_each_without_leaking_keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "secret-key")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _reply(429))
    with pytest.raises(chat.AllProvidersFailed) as caught:
        chat.Chat().say("s", "p")
    assert str(caught.value) == "groq: HTTP 429; gemini: HTTP 429"
    assert "secret" not in str(caught.value)


def test_a_retired_model_is_an_env_change(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "some-new-model")
    assert chat.model_for("groq") == "some-new-model"


def test_the_investigator_falls_back_to_the_analyst_when_models_fail(monkeypatch):
    from tests.test_samples import MANIFEST, upload

    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _reply(500))
    client = TestClient(app)
    files = MANIFEST["sets"]["missing"]["files"]
    token = client.post(
        "/api/close", files=dict(upload(s, files[s]) for s in ("recon", "statement")), data={"contract": MANIFEST["rates"]},
    ).json()["token"]
    body = client.post("/api/investigate", json={"token": token, "cause": "refunds_issued"}).json()
    assert body["driver"] == "analyst"
    assert body["notes"] and "HTTP 500" in body["notes"][0]
    assert body["steps"]
    assert body["accepted"] is True


def test_the_live_check_reports_each_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _reply(200) if "groq" in url else _reply(404))
    status = chat.check_all()
    assert status["groq"].startswith("ok")
    assert status["gemini"].startswith("HTTP 404")


def test_a_retired_model_is_replaced_by_one_the_provider_still_serves(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    asked: list[str] = []

    def post(url, json, **kw):
        asked.append(json["model"])
        return _reply(404) if json["model"] == loop.PROVIDERS["groq"]["model"] else _reply(200, "fresh")

    listing = {"data": [{"id": "whisper-large-v3"}, {"id": "llama-guard-4"}, {"id": "openai/gpt-oss-120b"}]}
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json=listing, request=httpx.Request("GET", url)))
    assert chat.Chat().say("s", "p") == "fresh"
    assert asked == [loop.PROVIDERS["groq"]["model"], "openai/gpt-oss-120b"]
    assert chat.model_for("groq") == "openai/gpt-oss-120b"


def test_a_pinned_model_is_never_swapped(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_MODEL", "pinned")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _reply(404))
    with pytest.raises(chat.AllProvidersFailed):
        chat.Chat().say("s", "p")
    assert chat.model_for("groq") == "pinned"


@pytest.mark.parametrize(
    ("provider", "listing", "picked"),
    [
        ("gemini", ["models/gemini-2.5-flash", "models/gemini-3-flash", "models/gemini-3-flash-image", "models/embedding-001"], "gemini-3-flash"),
        ("groq", ["whisper-large-v3", "qwen/qwen3-32b", "llama-3.3-70b-versatile"], "llama-3.3-70b-versatile"),
        ("openrouter", ["meta-llama/llama-3.3-70b-instruct", "qwen/qwen3-coder:free"], "qwen/qwen3-coder:free"),
    ],
)
def test_the_chooser_picks_a_chat_model(provider, listing, picked):
    assert loop.choose(provider, [{"id": m} for m in listing]) == picked


def test_openrouter_free_models_must_support_tools():
    listing = [
        {"id": "a/llama-3.3-70b-instruct:free", "supported_parameters": ["max_tokens"]},
        {"id": "b/qwen-2.5:free", "supported_parameters": ["tools", "max_tokens"]},
    ]
    assert loop.choose("openrouter", listing) == "b/qwen-2.5:free"


def _ask(client, token, question):
    return client.post("/api/ask", json={"token": token, "question": question}).json()


def test_every_starter_question_is_answered_when_every_model_is_down(monkeypatch):
    from tests.test_samples import MANIFEST, upload

    monkeypatch.setenv("GROQ_API_KEY", "k")
    calls: list[str] = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: calls.append(url) or _reply(503))
    client = TestClient(app)
    files = MANIFEST["sets"]["missing"]["files"]
    token = client.post(
        "/api/close", files=dict(upload(s, files[s]) for s in ("recon", "statement")), data={"contract": MANIFEST["rates"]},
    ).json()["token"]
    starters = [
        "Which payouts never reached my bank?",
        "How much did I pay in fees, by method?",
        "What were my five biggest payouts?",
        "How many payments failed, and by which method?",
        "How much did I refund this period?",
    ]
    for question in starters:
        calls.clear()
        body = _ask(client, token, question)
        assert body["source"].startswith("catalogue"), (question, body)
        assert len(calls) == 1, "a failed model must not be asked twice"
    assert _ask(client, token, starters[0])["rows"], "the lost payout should be listed"


def test_asking_about_missing_payouts_without_a_statement_says_what_is_needed(monkeypatch):
    from tests.test_samples import MANIFEST, upload

    client = TestClient(app)
    token = client.post("/api/close", files=dict([upload("recon", MANIFEST["sets"]["missing"]["files"]["recon"])])).json()["token"]
    body = _ask(client, token, "Which payouts never reached my bank?")
    assert not body["rows"]
    assert "bank statement" in body["note"]


def test_a_throttled_free_model_hands_over_to_a_sibling(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    asked: list[str] = []

    def post(url, json, **kw):
        asked.append(json["model"])
        return _reply(200, "sibling") if json["model"] == "b/llama-3.3-70b-instruct:free" else _reply(429)

    listing = {"data": [
        {"id": "a/qwen3:free", "supported_parameters": ["tools"]},
        {"id": "b/llama-3.3-70b-instruct:free", "supported_parameters": ["tools"]},
    ]}
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json=listing, request=httpx.Request("GET", url)))
    assert chat.Chat().say("s", "p") == "sibling"
    assert asked == [loop.PROVIDERS["openrouter"]["model"], "b/llama-3.3-70b-instruct:free"]


def test_swapping_stops_after_a_few_tries(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    asked: list[str] = []
    monkeypatch.setattr(httpx, "post", lambda url, json, **kw: asked.append(json["model"]) or _reply(429))
    listing = {"data": [{"id": f"m{i}/llama:free"} for i in range(6)]}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json=listing, request=httpx.Request("GET", url)))
    with pytest.raises(chat.AllProvidersFailed):
        chat.Chat().say("s", "p")
    assert len(asked) == 1 + loop.ALTERNATES


def _token(client, key):
    from tests.test_samples import MANIFEST, upload

    files = MANIFEST["sets"][key]["files"]
    return client.post(
        "/api/close", files=dict(upload(s, files[s]) for s in ("recon", "statement")), data={"contract": MANIFEST["rates"]},
    ).json()["token"]


def test_a_missing_payout_is_searched_for_before_it_is_confirmed():
    client = TestClient(app)
    body = client.post("/api/investigate", json={"token": _token(client, "missing"), "cause": "settlement_never_arrived"}).json()
    assert body["mode"] == "payout_search"
    assert [s["tool"] for s in body["steps"]] == ["search_reference", "search_amount"]
    assert body["accepted"] is True


def test_a_payout_that_landed_under_a_mangled_reference_is_flagged_not_confirmed():
    from tests.test_samples import MANIFEST, SAMPLES, upload

    files = MANIFEST["sets"]["missing"]["files"]
    client = TestClient(app)
    first = client.post(
        "/api/close", files=dict(upload(s, files[s]) for s in ("recon", "statement")), data={"contract": MANIFEST["rates"]},
    ).json()
    utr = next(f for f in first["findings"] if f["cause"] == "settlement_never_arrived")["refs"][0]
    import csv
    import io

    lines = (SAMPLES / files["statement"]).read_text().splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("Date,"))
    last = next(i for i in range(len(lines) - 1, header, -1) if lines[i].count(",") >= 6)
    # Append a credit whose narration keeps only the UTR's tail, as some banks print it.
    row = next(csv.reader([lines[last]]))
    closing = float(row[6].replace(",", "")) + 44008.31
    extra = io.StringIO()
    csv.writer(extra, lineterminator="").writerow(
        ["24/03/26", f"NEFT CR-RAZORPAY-XX{utr[-8:]}", "", "24/03/26", "", "44,008.31", f"{closing:,.2f}"]
    )
    edited = "\n".join([*lines[: last + 1], extra.getvalue(), *lines[last + 1 :]]) + "\n"
    token = client.post(
        "/api/close",
        files={"recon": upload("recon", files["recon"])[1], "statement": ("s.csv", edited.encode(), "text/csv")},
        data={"contract": MANIFEST["rates"]},
    ).json()["token"]
    body = client.post("/api/investigate", json={"token": token, "cause": "settlement_never_arrived"})
    if body.status_code == 409:
        pytest.fail("expected the search to run")
    assert body.json()["accepted"] is False
    assert "may be bank credit" in body.json()["verdict"]


@pytest.mark.parametrize("key", ["clean", "missing", "overcharged"])
def test_the_generic_second_opinion_always_has_something_to_find(key):
    client = TestClient(app)
    body = client.post("/api/investigate", json={"token": _token(client, key)}).json()
    assert body["steps"], body
    assert body["accepted"] is True, body


def test_model_calls_are_capped_per_visitor(monkeypatch):
    from residual.web import api

    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _reply(200, "SELECT 1 AS one"))
    monkeypatch.setattr(api, "_HITS", {})
    monkeypatch.setattr(api, "METER_LIMIT", 3)
    client = TestClient(app)
    token = _token(client, "clean")
    seen = [client.post("/api/ask", json={"token": token, "question": "anything at all?"}, headers={"x-forwarded-for": "9.9.9.9"}).status_code for _ in range(4)]
    assert seen == [200, 200, 200, 429]
    other = client.post("/api/ask", json={"token": token, "question": "anything at all?"}, headers={"x-forwarded-for": "8.8.8.8"})
    assert other.status_code == 200


def test_without_a_model_nothing_is_metered(monkeypatch):
    from residual.web import api

    monkeypatch.setattr(api, "_HITS", {})
    monkeypatch.setattr(api, "METER_LIMIT", 1)
    client = TestClient(app)
    token = _token(client, "clean")
    for _ in range(3):
        assert client.post("/api/ask", json={"token": token, "question": "fees by method"}).status_code == 200


def test_one_answer_never_takes_longer_than_its_budget(monkeypatch):
    import time as _time

    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    calls = []

    def slow(url, **kw):
        calls.append(kw["timeout"])
        _time.sleep(0.2)
        return _reply(503)

    monkeypatch.setattr(httpx, "post", slow)
    speaker = chat.Chat(budget=3.0)
    started = _time.monotonic()
    with pytest.raises(chat.AllProvidersFailed):
        speaker.say("s", "p")
    assert _time.monotonic() - started < 3.5
    assert all(t <= 3.0 for t in calls)


def test_a_model_that_rejects_reasoning_effort_is_asked_again_without_it(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    sent = []

    def post(url, json, **kw):
        sent.append(dict(json))
        return _reply(400) if "reasoning_effort" in json else _reply(200, "fine")

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(loop, "PROVIDERS", {**loop.PROVIDERS, "groq": {**loop.PROVIDERS["groq"], "model": "openai/gpt-oss-120b"}})
    assert chat.Chat().say("s", "p") == "fine"
    assert "reasoning_effort" in sent[0] and "reasoning_effort" not in sent[1]


def test_a_request_that_never_returns_is_cut_off_at_the_deadline(monkeypatch):
    import time as _time

    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: _time.sleep(30))
    started = _time.monotonic()
    with pytest.raises(chat.AllProvidersFailed):
        chat.Chat(budget=4.0).say("s", "p")
    assert _time.monotonic() - started < 6
