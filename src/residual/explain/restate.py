
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from residual.domain.causes import Cause
from residual.explain.close import Close, run_close
from residual.ledger.events import EventBase
from residual.ledger.money import Money, total


@dataclass(frozen=True, slots=True)
class Movement:
    cause: Cause
    then: Money
    now: Money

    @property
    def delta(self) -> Money:
        return self.now - self.then

    @property
    def appeared(self) -> bool:
        return self.then.paise == 0 and self.now.paise != 0


@dataclass(slots=True)
class Restatement:
    window: tuple[date, date]
    signed_on: date
    then: Close
    now: Close
    movements: list[Movement]

    @property
    def gap_delta(self) -> Money:
        return self.now.gap - self.then.gap

    @property
    def moved(self) -> bool:
        return self.gap_delta.paise != 0 or bool(self.movements)

    @property
    def reclassified(self) -> Money:
        return Money((sum(abs(m.delta.paise) for m in self.movements) + 1) // 2)

    @property
    def late_arrivals(self) -> list[Movement]:
        return [m for m in self.movements if m.appeared]

    @property
    def unexplained_drift(self) -> Money:
        return self.gap_delta - total(m.delta for m in self.movements)


def restate(
    events: list[EventBase],
    start: date,
    end: date,
    contracted: dict[str, str],
    signed_on: date | None = None,
) -> Restatement:
    signed_on = signed_on or end
    then = run_close(events, start, end, contracted, known_by=signed_on)
    now = run_close(events, start, end, contracted)

    a, b = then.by_cause(), now.by_cause()
    movements = [
        Movement(cause, a.get(cause, Money.zero()), b.get(cause, Money.zero()))
        for cause in sorted(set(a) | set(b))
    ]
    movements = [m for m in movements if m.delta.paise]
    movements.sort(key=lambda m: -abs(m.delta.paise))

    return Restatement(
        window=(start, end), signed_on=signed_on, then=then, now=now, movements=movements
    )
