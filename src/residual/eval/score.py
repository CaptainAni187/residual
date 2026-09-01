
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from residual.domain.causes import Cause
from residual.ledger.money import Money
from residual.simulate.truth import GroundTruth

STRUCTURAL: frozenset[Cause] = frozenset(
    {Cause.CAPTURED_NOT_YET_SETTLED, Cause.SETTLEMENT_IN_FLIGHT}
)

KNOWN_BLIND_SPOTS: frozenset[Cause] = frozenset({Cause.BANK_HOLIDAY_DELAY})


@dataclass(frozen=True, slots=True)
class CauseScore:
    cause: Cause
    reported: Money
    actual: Money

    @property
    def error(self) -> Money:
        return self.reported - self.actual

    @property
    def exact(self) -> bool:
        return self.error.paise == 0


@dataclass(slots=True)
class WindowScore:
    window: tuple[date, date]
    gap: Money
    residual: Money
    matched: list[CauseScore] = field(default_factory=list)
    hallucinated: list[CauseScore] = field(default_factory=list)
    missed: list[CauseScore] = field(default_factory=list)
    structural: list[CauseScore] = field(default_factory=list)

    @property
    def closes(self) -> bool:
        return self.residual.paise == 0

    @property
    def reported_count(self) -> int:
        return len(self.matched) + len(self.hallucinated)


@dataclass(slots=True)
class Report:
    windows: list[WindowScore] = field(default_factory=list)


    @property
    def close_rate(self) -> float:
        return _ratio(sum(w.closes for w in self.windows), len(self.windows))

    @property
    def hallucinated_cause_rate(self) -> float:
        bad = sum(len(w.hallucinated) for w in self.windows)
        return _ratio(bad, sum(w.reported_count for w in self.windows))

    @property
    def cause_precision(self) -> float:
        tp = sum(len(w.matched) for w in self.windows)
        return _ratio(tp, sum(w.reported_count for w in self.windows))

    @property
    def cause_recall(self) -> float:
        tp = sum(len(w.matched) for w in self.windows)
        return _ratio(tp, tp + sum(len(w.missed) for w in self.windows))

    @property
    def amount_exact_rate(self) -> float:
        scores = [c for w in self.windows for c in w.matched]
        return _ratio(sum(c.exact for c in scores), len(scores))

    @property
    def rupee_error(self) -> Money:
        return Money(sum(abs(c.error.paise) for w in self.windows for c in w.matched))

    @property
    def blind_spots(self) -> dict[Cause, int]:
        out: dict[Cause, int] = {}
        for w in self.windows:
            for c in w.missed:
                out[c.cause] = out.get(c.cause, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _ratio(n: int, d: int) -> float:
    return n / d if d else 1.0


def score_close(close, truth: GroundTruth) -> WindowScore:
    start, end = close.window
    reported = close.by_cause()
    actual = truth.by_cause(start, end)

    ws = WindowScore(window=close.window, gap=close.gap, residual=close.residual)
    for cause, amount in reported.items():
        if cause in STRUCTURAL:
            ws.structural.append(CauseScore(cause, amount, Money.zero()))
        elif cause in actual:
            ws.matched.append(CauseScore(cause, amount, actual[cause]))
        else:
            ws.hallucinated.append(CauseScore(cause, amount, Money.zero()))
    for cause, amount in actual.items():
        if cause not in reported and amount.paise:
            ws.missed.append(CauseScore(cause, Money.zero(), amount))
    return ws


def score_run(
    events, truth: GroundTruth, start: date, days: int, contracted: dict[str, str],
    window_days: int = 7,
) -> Report:
    from residual.explain.close import run_close
    from residual.ledger.warehouse import Warehouse

    wh = Warehouse.build(events)
    report = Report()
    for offset in range(0, days, window_days):
        s = start + timedelta(days=offset)
        e = s + timedelta(days=window_days - 1)
        report.windows.append(score_close(run_close(events, s, e, contracted, wh), truth))
    return report
