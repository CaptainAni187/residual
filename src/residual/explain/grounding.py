
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from residual.ledger.money import Money

_MONEY = re.compile(
    r"(-?)\s*(?:INR|Rs\.?|₹)\s*(-?[\d,]+(?:\.\d{1,2})?)"
    r"|(?<![\w.])(-?\d[\d,]{2,}(?:\.\d{2})?)(?![\w.])",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Citation:
    text: str
    amount: Money
    grounded: bool
    nearest: Money | None = None


@dataclass(slots=True)
class Grounding:
    citations: list[Citation] = field(default_factory=list)
    permitted: list[Money] = field(default_factory=list)

    @property
    def fabricated(self) -> list[Citation]:
        return [c for c in self.citations if not c.grounded]

    @property
    def ok(self) -> bool:
        return not self.fabricated

    @property
    def rate(self) -> float:
        if not self.citations:
            return 1.0
        return sum(c.grounded for c in self.citations) / len(self.citations)

    def reason(self) -> str:
        if self.ok:
            return f"all {len(self.citations)} figures trace to a verified amount"
        bad = ", ".join(c.text for c in self.fabricated)
        return f"{len(self.fabricated)} figure(s) with no verified source: {bad}"


def extract_amounts(text: str) -> list[tuple[str, Money]]:
    out: list[tuple[str, Money]] = []
    for match in _MONEY.finditer(text):
        lead, tagged, bare = match.groups()
        raw = tagged if tagged is not None else bare
        if raw is None:
            continue
        try:
            value = Decimal(raw.replace(",", ""))
        except (InvalidOperation, ValueError):
            continue
        if lead == "-":
            value = -abs(value)
        out.append((match.group(0).strip(), Money.parse(value)))
    return out


def check(text: str, permitted: list[Money], *, tolerance_paise: int = 0) -> Grounding:
    allowed = {abs(m.paise) for m in permitted}
    citations: list[Citation] = []
    for raw, amount in extract_amounts(text):
        target = abs(amount.paise)
        hit = any(abs(target - a) <= tolerance_paise for a in allowed)
        nearest = (
            Money(min(allowed, key=lambda a: abs(a - target))) if allowed and not hit else None
        )
        citations.append(Citation(raw, amount, hit, nearest))
    return Grounding(citations=citations, permitted=list(permitted))
