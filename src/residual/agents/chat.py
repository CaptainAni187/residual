from __future__ import annotations

import os
import re
import time
from typing import Any

from residual.agents.loop import PROVIDERS, current_model, post_chat

ANTHROPIC_MODEL = "claude-opus-5"

# Tried in this order; the first that answers wins. Fast and free first.
ORDER = ("groq", "gemini", "openrouter", "grok", "anthropic")


def available() -> list[str]:
    ready = {name for name, spec in PROVIDERS.items() if os.environ.get(spec["env"])}
    if os.environ.get("ANTHROPIC_API_KEY"):
        ready.add("anthropic")
    return [name for name in ORDER if name in ready]


def model_for(provider: str) -> str:
    """A provider's model, overridable with e.g. GROQ_MODEL so a retired model is an env change, not a deploy."""
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL", "").strip() or ANTHROPIC_MODEL
    return current_model(provider)


def pick(preferred: str = "") -> str:
    ready = available()
    if preferred and preferred in ready:
        return preferred
    return ready[0] if ready else ""


def why(exc: Exception) -> str:
    """What went wrong, without echoing a key or a response body."""
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None):
        return f"HTTP {response.status_code}"
    return type(exc).__name__


def clean(text: str) -> str:
    """Drop any visible reasoning a model prints before its answer."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"^.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return re.sub(r"\s+\n", "\n", text).strip()


class AllProvidersFailed(RuntimeError):
    pass


class Chat:
    """Speaks through whichever configured provider answers, falling back down ORDER on any failure."""

    def __init__(self, provider: str = "", model: str = "", post: Any = None, budget: float = 20.0) -> None:
        ready = available()
        if provider and provider in ready:
            ready = [provider, *[p for p in ready if p != provider]]
        self.providers = ready
        self.provider = ready[0] if ready else ""
        self.model = model
        self._post = post
        self.failures: list[str] = []
        self.budget = budget
        self._deadline = 0.0

    @property
    def ready(self) -> bool:
        return bool(self.providers) or self._post is not None

    def describe(self) -> str:
        if not self.provider:
            return "stub" if self._post is not None else "offline"
        return f"{self.provider}/{self.model or model_for(self.provider)}"

    def say(self, system: str, prompt: str, max_tokens: int = 400) -> str:
        if self._post is not None:
            return str(self._post(system, prompt))
        if not self.providers:
            raise RuntimeError("no model is configured")
        self.failures = []
        # One answer gets this long across every provider and model, so a page never hangs.
        self._deadline = time.monotonic() + self.budget
        for provider in self.providers:
            if self._deadline - time.monotonic() < 3:
                self.failures.append(f"{provider}: skipped, out of time")
                continue
            try:
                if provider == "anthropic":
                    text = self._anthropic(provider, system, prompt, max_tokens)
                else:
                    text = self._openai(provider, system, prompt, max_tokens)
            except Exception as exc:  # noqa: BLE001 - any provider failure moves to the next one
                self.failures.append(f"{provider}: {why(exc)}")
                continue
            if not text:
                self.failures.append(f"{provider}: empty reply")
                continue
            self.provider = provider
            return text
        raise AllProvidersFailed("; ".join(self.failures))

    def _anthropic(self, provider: str, system: str, prompt: str, max_tokens: int) -> str:
        import anthropic

        reply = anthropic.Anthropic().messages.create(
            model=self.model or model_for(provider),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in reply.content if b.type == "text").strip()

    def _openai(self, provider: str, system: str, prompt: str, max_tokens: int) -> str:
        spec = PROVIDERS[provider]
        body, _ = post_chat(
            provider,
            os.environ[spec["env"]],
            {
                "model": self.model,
                # Reasoning models spend part of this budget thinking before they answer;
                # too small a budget and the visible answer comes back empty.
                "max_tokens": max(max_tokens * 4, 2048),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=15,
            deadline=self._deadline,
        )
        text = body.get("choices", [{}])[0].get("message", {}).get("content") or ""
        return clean(str(text))


def check_all() -> dict[str, str]:
    """Ping each configured provider with a one-word prompt and report whether it answered."""
    out: dict[str, str] = {}
    for provider in available():
        chat = Chat()
        chat.providers = [provider]
        try:
            chat.say("Reply with the single word OK.", "Are you there?", max_tokens=16)
            out[provider] = f"ok · {model_for(provider)}"
        except AllProvidersFailed as exc:
            out[provider] = f"{str(exc).split(': ', 1)[-1]} · {model_for(provider)}"
    return out
