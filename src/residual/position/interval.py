
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from residual.ledger.money import Money

DEFAULT_ALPHA = 0.10

DEFAULT_GAMMA = 0.02


def _over_forecast(forecast: Money, actual: Money) -> float:
    if forecast.paise <= 0:
        return 0.0
    return (forecast.paise - actual.paise) / forecast.paise


def conformal_quantile(scores: list[float], alpha: float) -> float:
    if not scores:
        return 0.0
    ordered = sorted(scores)
    n = len(ordered)
    rank = math.ceil((n + 1) * (1 - alpha))
    if rank > n:
        return ordered[-1]
    return ordered[rank - 1]


@dataclass(frozen=True, slots=True)
class Floor:

    day: date
    forecast: Money
    floor: Money
    alpha: float
    calibrated_on: int

    @property
    def confidence(self) -> float:
        return 1 - self.alpha

    @property
    def certified(self) -> bool:
        return self.calibrated_on >= math.ceil(1 / self.alpha) - 1

    @property
    def headroom(self) -> Money:
        return self.forecast - self.floor

    def __str__(self) -> str:
        if not self.certified:
            return f"{self.forecast} expected; too little history to certify a floor"
        return (
            f"at least {self.floor} by {self.day} "
            f"({self.confidence:.0%} confidence, {self.forecast} expected)"
        )


@dataclass(slots=True)
class SplitConformal:

    alpha: float = DEFAULT_ALPHA
    scores: list[float] = field(default_factory=list)

    def observe(self, forecast: Money, actual: Money) -> None:
        self.scores.append(_over_forecast(forecast, actual))

    def floor_for(self, day: date, forecast: Money) -> Floor:
        q = conformal_quantile(self.scores, self.alpha)
        floor = Money(max(0, int(forecast.paise * (1 - q))))
        return Floor(day, forecast, floor, self.alpha, len(self.scores))


@dataclass(slots=True)
class Adaptive:

    alpha: float = DEFAULT_ALPHA
    gamma: float = DEFAULT_GAMMA
    scores: list[float] = field(default_factory=list)
    current: float = DEFAULT_ALPHA
    breaches: int = 0
    observations: int = 0
    recent: list[bool] = field(default_factory=list)

    max_discount: float = 0.25
    window: int = 12

    @property
    def discount(self) -> float:
        return conformal_quantile(self.scores, min(max(self.current, 1e-4), 0.999))

    @property
    def in_distress(self) -> bool:
        if self.observations < self.window:
            return False
        recent = self.recent[-self.window:]
        return (
            self.discount > self.max_discount
            or sum(recent) / len(recent) > self.alpha * 2
        )

    def diagnosis(self) -> str:
        if self.observations < self.window:
            return f"{self.observations} windows observed; not enough to judge the model"
        recent = self.recent[-self.window:]
        breaches = sum(recent)
        if not self.in_distress:
            return (
                f"floor held in {len(recent) - breaches} of the last {len(recent)} "
                f"windows, discounting {self.discount:.0%}; the model is behaving as "
                f"calibrated"
            )
        if self.discount > self.max_discount:
            return (
                f"the floor now has to sit {self.discount:.0%} below the forecast to "
                f"hold its confidence. The model has not started lying -- it has "
                f"stopped saying anything useful. Something about this merchant "
                f"changed; recalibrate before planning against it"
            )
        return (
            f"the floor was breached in {breaches} of the last {len(recent)} windows "
            f"against a {self.alpha:.0%} budget. The errors no longer look like the "
            f"ones this was calibrated on -- treat the forecast as unreliable"
        )

    def floor_for(self, day: date, forecast: Money) -> Floor:
        q = conformal_quantile(self.scores, min(max(self.current, 1e-4), 0.999))
        floor = Money(max(0, int(forecast.paise * (1 - q))))
        return Floor(day, forecast, floor, self.alpha, len(self.scores))

    def observe(self, predicted: Floor, actual: Money) -> bool:
        breached = actual.paise < predicted.floor.paise
        self.observations += 1
        self.breaches += breached
        self.recent.append(bool(breached))
        self.current = min(max(self.current + self.gamma * (self.alpha - breached), 1e-4), 0.999)
        self.scores.append(_over_forecast(predicted.forecast, actual))
        return breached

    @property
    def realised_miscoverage(self) -> float:
        return self.breaches / self.observations if self.observations else 0.0


@dataclass(slots=True)
class Coverage:

    method: str
    target: float
    held: int = 0
    breached: int = 0
    forecast_total: Money = field(default_factory=Money.zero)
    floor_total: Money = field(default_factory=Money.zero)
    actual_total: Money = field(default_factory=Money.zero)

    horizon: int = 14
    step: int = 1

    @property
    def n(self) -> int:
        return self.held + self.breached

    @property
    def independent(self) -> int:
        overlap = max(1, self.horizon // max(self.step, 1))
        return max(1, self.n // overlap)

    @property
    def empirical(self) -> float:
        return self.held / self.n if self.n else 0.0

    @property
    def tightness(self) -> float:
        if not self.actual_total.paise:
            return 0.0
        return self.floor_total.paise / self.actual_total.paise

    @property
    def meets_target(self) -> bool:
        return self.empirical >= self.target

    def summary(self) -> str:
        return (
            f"{self.method}: {self.empirical:.1%} coverage against a {self.target:.0%} "
            f"target over {self.n} windows ({self.independent} non-overlapping), "
            f"floor at {self.tightness:.0%} of what arrived"
        )


def certify(
    events: list,
    start: date,
    days: int,
    horizon: int = 14,
    alpha: float = DEFAULT_ALPHA,
    warmup: int = 60,
    gamma: float = DEFAULT_GAMMA,
    step: int = 1,
) -> dict[str, Coverage]:
    from datetime import timedelta

    from residual.ledger import select
    from residual.ledger.money import total
    from residual.position.forecast import forecast

    split = SplitConformal(alpha=alpha)
    adaptive = Adaptive(alpha=alpha, gamma=gamma)
    out = {
        name: Coverage(name, 1 - alpha, horizon=horizon, step=step)
        for name in ("split conformal", "adaptive (ACI)")
    }

    for offset in range(warmup, days - horizon, step):
        as_of = start + timedelta(days=offset)
        end = as_of + timedelta(days=horizon)
        point = forecast(events, as_of, horizon=horizon).through(end)
        actual = total(
            e.amount for e in select.credits(events) if as_of < e.occurred_at <= end
        )
        if not point.paise:
            continue

        for name, model in (("split conformal", split), ("adaptive (ACI)", adaptive)):
            predicted = model.floor_for(end, point)
            record = out[name]
            if predicted.certified:
                held = actual.paise >= predicted.floor.paise
                record.held += held
                record.breached += not held
                record.forecast_total = record.forecast_total + point
                record.floor_total = record.floor_total + predicted.floor
                record.actual_total = record.actual_total + actual
            if isinstance(model, Adaptive):
                model.observe(predicted, actual)
            else:
                model.observe(point, actual)

    return out


def calibrate(
    events: list,
    as_of: date,
    horizon: int = 14,
    lookback: int = 140,
    alpha: float = DEFAULT_ALPHA,
) -> Adaptive:
    from datetime import timedelta

    from residual.ledger import select
    from residual.ledger.money import total
    from residual.position.forecast import forecast

    model = Adaptive(alpha=alpha)
    for offset in range(-lookback, 0, horizon):
        seen = as_of + timedelta(days=offset)
        window_end = seen + timedelta(days=horizon)
        if window_end > as_of:
            break
        point = forecast(events, seen, horizon=horizon).through(window_end)
        if not point.paise:
            continue
        landed = total(
            e.amount for e in select.credits(events) if seen < e.occurred_at <= window_end
        )
        model.observe(model.floor_for(window_end, point), landed)
    return model


@dataclass(slots=True)
class Pooled:

    method: str
    target: float
    held: int = 0
    breached: int = 0
    floor_paise: int = 0
    actual_paise: int = 0
    independent: int = 0

    @property
    def n(self) -> int:
        return self.held + self.breached

    @property
    def empirical(self) -> float:
        return self.held / self.n if self.n else 0.0

    @property
    def tightness(self) -> float:
        return self.floor_paise / self.actual_paise if self.actual_paise else 0.0

    @property
    def meets_target(self) -> bool:
        return self.empirical >= self.target

    def absorb(self, coverage: Coverage) -> None:
        self.held += coverage.held
        self.breached += coverage.breached
        self.floor_paise += coverage.floor_total.paise
        self.actual_paise += coverage.actual_total.paise
        self.independent += coverage.independent


def certify_across(
    worlds: list[tuple[list, date, int]],
    horizon: int = 14,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, Pooled]:
    pooled: dict[str, Pooled] = {}
    for events, start, days in worlds:
        for name, coverage in certify(
            events, start, days, horizon=horizon, alpha=alpha
        ).items():
            pooled.setdefault(name, Pooled(name, 1 - alpha)).absorb(coverage)
    return pooled
