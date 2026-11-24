
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


_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATES = re.compile(
    rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b"
    rf"|\b\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH},?\s+\d{{4}}\b"
    rf"|\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b"
    rf"|\b{_MONTH}\s+\d{{4}}\b"
    rf"|\b(?:FY|AY)\s?\d{{2,4}}(?:-\d{{2,4}})?\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"-?\d+(?:\.\d+)?\s*(?:%|per\s?cent\b)", re.IGNORECASE)
_SCALED = re.compile(r"(?:INR|Rs\.?|₹)?\s*(\d+(?:\.\d+)?)\s*(lakh|lac|crore|cr)\b", re.IGNORECASE)
# A long run of bare digits is a reference (UTR, account, invoice), never an amount.
_IDENT = re.compile(r"(?<![\d,.])\d{9,}(?![\d,.])")
_SCALE = {"lakh": 100_000, "lac": 100_000, "crore": 10_000_000, "cr": 10_000_000}


def _looks_like_money(raw: str) -> bool:
    """Bare numbers count only when written like an amount, so '123 rows' is not an invented rupee figure."""
    if re.match(r"\s*-?\s*(?:INR|Rs|₹)", raw, re.IGNORECASE):
        return True
    digits = raw.replace("-", "").strip()
    return "," in digits or "." in digits or len(digits) >= 5


def check(
    text: str,
    permitted: list[Money],
    *,
    tolerance_paise: int = 0,
    identifiers: list[str] | tuple[str, ...] = (),
) -> Grounding:
    """Every amount in `text` must trace to a permitted figure, and every reference to a known identifier.

    Dates and percentages are not amounts. A figure rounded to whole rupees, or written in lakh or
    crore, counts when it is a fair rounding of a permitted figure.
    """
    allowed = {abs(m.paise) for m in permitted}
    citations: list[Citation] = []

    def near(target: int) -> Money | None:
        return Money(min(allowed, key=lambda a: abs(a - target))) if allowed else None

    for ident in sorted({i for i in identifiers if i}, key=len, reverse=True):
        text = text.replace(ident, " ")
    for match in _IDENT.finditer(text):
        citations.append(Citation(match.group(0), Money.zero(), False))
    text = _IDENT.sub(" ", text)
    text = _DATES.sub(" ", text)
    text = _PERCENT.sub(" ", text)

    for match in _SCALED.finditer(text):
        number, unit = match.group(1), match.group(2).lower()
        decimals = len(number.split(".")[1]) if "." in number else 0
        target = int(Decimal(number) * _SCALE[unit] * 100)
        slack = _SCALE[unit] * 100 // (10**decimals) // 2
        hit = any(abs(target - a) <= slack for a in allowed)
        citations.append(Citation(match.group(0).strip(), Money(target), hit, None if hit else near(target)))
    text = _SCALED.sub(" ", text)

    for raw, amount in extract_amounts(text):
        if not _looks_like_money(raw):
            continue
        target = abs(amount.paise)
        hit = any(abs(target - a) <= tolerance_paise for a in allowed)
        if not hit and target % 100 == 0 and "." not in raw:
            hit = any(target in (a // 100 * 100, (a + 50) // 100 * 100) for a in allowed)
        citations.append(Citation(raw, amount, hit, None if hit else near(target)))
    return Grounding(citations=citations, permitted=list(permitted))
