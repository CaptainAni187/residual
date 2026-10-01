"""The provider router: tried in order, falls through on failure, and the investigator always ends with an answer."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from residual.agents import chat
from residual.web.app import app

KEYS = ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "ANTHROPIC_API_KEY")


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)


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
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _reply(500))
    client = TestClient(app)
    token = client.post("/api/demo").json()["token"]
    body = client.post("/api/investigate", json={"token": token}).json()
    assert body["driver"] == "analyst"
    assert body["notes"] and "HTTP 500" in body["notes"][0]
    assert body["steps"]


def test_the_live_check_reports_each_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _reply(200) if "groq" in url else _reply(404))
    status = chat.check_all()
    assert status["groq"].startswith("ok")
    assert status["gemini"].startswith("HTTP 404")
