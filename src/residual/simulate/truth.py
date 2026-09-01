
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from residual.domain.causes import Cause
from residual.ledger.money import Money, total


@dataclass(frozen=True, slots=True)
class Attribution:

    event_id: str
    occurred_at: date
    cause: Cause
    amount: Money
    entity_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class TimingFact:

    payment_id: str
    captured_on: date
    net: Money
    t2_naive: date
    t2_actual: date


@dataclass(slots=True)
class GroundTruth:

    attributions: list[Attribution] = field(default_factory=list)
    timing: list[TimingFact] = field(default_factory=list)

    links: dict[str, str] = field(default_factory=dict)

    styles: dict[str, str] = field(default_factory=dict)

    def add(self, a: Attribution) -> None:
        if a.amount.paise:
            self.attributions.append(a)

    def add_timing(self, t: TimingFact) -> None:
        self.timing.append(t)

    def holiday_delay(self, start: date, end: date) -> Money:
        return total(
            t.net
            for t in self.timing
            if start <= t.captured_on <= end and t.t2_naive <= end < t.t2_actual
        )

    def window(self, start: date, end: date) -> list[Attribution]:
        return [a for a in self.attributions if start <= a.occurred_at <= end]

    def by_cause(self, start: date, end: date) -> dict[Cause, Money]:
        out: dict[Cause, Money] = defaultdict(Money.zero)
        for a in self.window(start, end):
            out[a.cause] = out[a.cause] + a.amount
        delayed = self.holiday_delay(start, end)
        if delayed.paise:
            out[Cause.BANK_HOLIDAY_DELAY] = delayed
        return {c: m for c, m in out.items() if m.paise}

    def total(self, start: date, end: date) -> Money:
        return total(a.amount for a in self.window(start, end))
