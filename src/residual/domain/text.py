
from __future__ import annotations


def normalise(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum())
