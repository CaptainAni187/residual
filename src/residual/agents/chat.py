from __future__ import annotations

import os
import re
from typing import Any, cast

from residual.agents.loop import PROVIDERS

ANTHROPIC_MODEL = "claude-opus-5"


def available() -> list[str]:
    found = ["anthropic"] if os.environ.get("ANTHROPIC_API_KEY") else []
    return found + [name for name, spec in PROVIDERS.items() if os.environ.get(spec["env"])]


def pick(preferred: str = "") -> str:
    ready = available()
    if preferred and preferred in ready:
        return preferred
    return ready[0] if ready else ""


class Chat:

    def __init__(self, provider: str = "", model: str = "", post: Any = None) -> None:
        self.provider = pick(provider)
        self.model = model
        self._post = post

    @property
    def ready(self) -> bool:
        return bool(self.provider) or self._post is not None

    def describe(self) -> str:
        if not self.provider:
            return "stub" if self._post is not None else "offline"
        return f"{self.provider}/{self.model}" if self.model else self.provider

    def say(self, system: str, prompt: str, max_tokens: int = 400) -> str:
        if self._post is not None:
            return str(self._post(system, prompt))
        if not self.provider:
            raise RuntimeError("no model is configured")
        if self.provider == "anthropic":
            return self._anthropic(system, prompt, max_tokens)
        return self._openai(system, prompt, max_tokens)

    def _anthropic(self, system: str, prompt: str, max_tokens: int) -> str:
        import anthropic

        reply = anthropic.Anthropic().messages.create(
            model=self.model or ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in reply.content if b.type == "text").strip()

    def _openai(self, system: str, prompt: str, max_tokens: int) -> str:
        import httpx

        spec = PROVIDERS[self.provider]
        reply = httpx.post(
            f"{spec['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {os.environ[spec['env']]}"},
            json={
                "model": self.model or spec["model"],
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=60,
        )
        reply.raise_for_status()
        body = cast(dict[str, Any], reply.json())
        text = body.get("choices", [{}])[0].get("message", {}).get("content") or ""
        return re.sub(r"\s+\n", "\n", str(text)).strip()
