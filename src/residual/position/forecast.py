
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from residual.domain.calendar import add_bank_days, is_bank_day
from residual.domain.causes import Cause
from residual.ledger import select
from residual.ledger.events import (
    DisputeOpened,
    EventBase,
    PaymentCaptured,
    RefundIssued,
)
from residual.ledger.money import Money, allocate, total

LOOKBACK_DAYS = 45

HALF_LIFE_DAYS = 45.0


def _amount(event: EventBase) -> Money:
    value = getattr(event, "amount", None)
    return value if isinstance(value, Money) else Money.zero()


@dataclass(frozen=True, slots=True)
class DayForecast:
    day: date
    committed: Money
    projected: Money
    basis: dict[str, Money] = field(default_factory=dict)

    @property
    def expected(self) -> Money:
        return self.committed + self.projected

    @property
    def certain(self) -> bool:
        return self.projected.paise == 0


@dataclass(slots=True)
class Outlook:
    as_of: date
    days: list[DayForecast]

    overdue: Money = field(default_factory=Money.zero)

    written_off: Money = field(default_factory=Money.zero)

    @property
    def total(self) -> Money:
        return total(d.expected for d in self.days)

    @property
    def committed(self) -> Money:
        return total(d.committed for d in self.days)

    @property
    def projected(self) -> Money:
        return total(d.projected for d in self.days)

    def through(self, day: date) -> Money:
        return total(d.expected for d in self.days if d.day <= day)


@dataclass(frozen=True, slots=True)
class Attribution:
    cause: Cause
    amount: Money
    note: str = ""


@dataclass(slots=True)
class ForecastError:

    day: date
    forecast: Money
    actual: Money
    contributors: list[Attribution] = field(default_factory=list)

    @property
    def error(self) -> Money:
        return self.actual - self.forecast

    @property
    def directional(self) -> Money:
        return total(a.amount for a in self.contributors)

    @property
    def largest(self) -> Attribution | None:
        return max(self.contributors, key=lambda a: abs(a.amount.paise), default=None)


@dataclass(frozen=True, slots=True)
class Shape:

    daily_gross: dict[int, Money]
    fee_rate: Decimal
    deduction_rate: Decimal
    realisation: Decimal

    @classmethod
    def learn(
        cls,
        events: list[EventBase],
        as_of: date,
        lookback: int = LOOKBACK_DAYS,
        half_life: float = HALF_LIFE_DAYS,
    ) -> Shape:
        window = [
            e
            for e in events
            if as_of - timedelta(days=lookback) <= e.occurred_at <= as_of
            and e.recorded_at <= as_of
        ]
        by_weekday: dict[int, list[tuple[date, Money]]] = defaultdict(list)
        gross = fees = deductions = 0

        daily: dict[date, int] = defaultdict(int)
        for e in window:
            if isinstance(e, PaymentCaptured):
                daily[e.occurred_at] += e.gross.paise
                gross += e.gross.paise
                fees += e.fee.paise + e.tax.paise + e.tds.paise
            elif isinstance(e, RefundIssued | DisputeOpened):
                deductions += e.amount.paise

        for day, paise in daily.items():
            by_weekday[day.weekday()].append((day, Money(paise)))

        typical = {}
        for weekday, dated in by_weekday.items():
            weights = [0.5 ** ((as_of - day).days / half_life) for day, _ in dated]
            weighted = sum(w * m.paise for w, (_, m) in zip(weights, dated))
            typical[weekday] = Money(int(weighted / sum(weights) + 0.5))
        landed = sum(e.amount.paise for e in select.credits(window))
        implied = gross - fees - deductions
        return cls(
            daily_gross=typical,
            fee_rate=Decimal(fees) / Decimal(gross) if gross else Decimal(0),
            deduction_rate=Decimal(deductions) / Decimal(gross) if gross else Decimal(0),
            realisation=(
                min(Decimal(landed) / Decimal(implied), Decimal(1))
                if implied > 0
                else Decimal(1)
            ),
        )

    def gross_for(self, day: date) -> Money:
        return self.daily_gross.get(day.weekday(), Money.zero())


def forecast(
    events: list[EventBase],
    as_of: date,
    horizon: int = 14,
    lag: int = 2,
    stale_after: int = 3,
    half_life: float = HALF_LIFE_DAYS,
) -> Outlook:
    known = [e for e in events if e.recorded_at <= as_of and e.occurred_at <= as_of]
    shape = Shape.learn(events, as_of, half_life=half_life)

    settled = {pid for e in select.settlements(known) for pid in e.covers}
    pending: dict[date, int] = defaultdict(int)
    overdue = 0
    for e in select.captures(known):
        if e.payment_id in settled:
            continue
        net = e.gross - e.fee - e.tax - e.tds
        lands = add_bank_days(e.occurred_at, lag)
        if lands <= as_of:
            overdue += net.paise
        else:
            pending[lands] += net.paise

    from residual.recon.linkage import link_events

    credited = {a.settlement_id for a in link_events(known) if a.linked}
    written_off = Money.zero()
    for payout in select.settlements(known):
        if payout.settlement_id in credited:
            continue
        age = (as_of - payout.occurred_at).days
        if age > stale_after:
            written_off = written_off + payout.net
            continue
        pending[_next_open(as_of + timedelta(days=1))] += payout.net.paise

    lands_on: dict[date, list[date]] = defaultdict(list)
    for offset in range(1, horizon + 1):
        capture_day = as_of + timedelta(days=offset)
        lands_on[add_bank_days(capture_day, lag)].append(capture_day)

    days: list[DayForecast] = []
    for offset in range(1, horizon + 1):
        day = as_of + timedelta(days=offset)
        if not is_bank_day(day):
            days.append(DayForecast(day, Money.zero(), Money.zero(), {"bank_closed": Money.zero()}))
            continue

        committed = Money(pending.get(day, 0))
        committed = committed - committed.apply_rate(shape.deduction_rate * 100)
        committed = committed.apply_rate(shape.realisation * 100)

        projected = Money.zero()
        for capture_day in lands_on.get(day, []):
            gross = shape.gross_for(capture_day)
            after_fees = gross - gross.apply_rate(shape.fee_rate * 100)
            after_deductions = after_fees - after_fees.apply_rate(shape.deduction_rate * 100)
            projected = projected + after_deductions.apply_rate(shape.realisation * 100)

        days.append(
            DayForecast(
                day=day,
                committed=committed,
                projected=projected,
                basis={
                    "already captured": Money(pending.get(day, 0)),
                    "expected deductions": Money(pending.get(day, 0)).apply_rate(
                        shape.deduction_rate * 100
                    ),
                },
            )
        )
    return Outlook(as_of=as_of, days=days, overdue=Money(overdue), written_off=written_off)


def _next_open(day: date) -> date:
    while not is_bank_day(day):
        day += timedelta(days=1)
    return day


def attribute_error(
    events: list[EventBase],
    day: date,
    expected: Money,
    contracted: dict[str, str],
    lag: int = 2,
    since: date | None = None,
    shape: Shape | None = None,
) -> ForecastError:
    since = since or day
    shape = shape or Shape.learn(events, since - timedelta(days=1))

    def in_window(d: date) -> bool:
        return since <= d <= day

    actual = total(
        _amount(e)
        for e in events
        if e.type == "bank_credit_received" and in_window(e.occurred_at)
    )
    err = ForecastError(day=day, forecast=expected, actual=actual)

    landing = [
        e for e in select.captures(events) if in_window(add_bank_days(e.occurred_at, lag))
    ]
    gross_landing = total(e.gross for e in landing)

    overcharge = total(
        e.fee - e.gross.apply_rate(contracted.get(str(e.method), "0"))
        for e in landing
    )
    if overcharge.paise:
        err.contributors.append(
            Attribution(Cause.FEE_RATE_INCREASE, -overcharge, "billed above contract")
        )

    held = total(
        e.gross - e.fee - e.tax - e.tds
        for e in select.captures(events)
        if in_window(e.occurred_at + timedelta(days=lag))
        and add_bank_days(e.occurred_at, lag) > day
    )
    if held.paise:
        err.contributors.append(
            Attribution(Cause.BANK_HOLIDAY_DELAY, -held, "T+2 fell on a non-banking day")
        )

    modelled = gross_landing.apply_rate(shape.deduction_rate * 100)
    observed: dict[Cause, Money] = {}
    for kind, cause in (
        ("refund_issued", Cause.REFUNDS_ISSUED),
        ("dispute_opened", Cause.DISPUTE_RESERVE_HELD),
    ):
        observed[cause] = total(
            _amount(e) for e in events if e.type == kind and in_window(e.occurred_at)
        )
    seen = total(observed.values())
    surprise = seen - modelled
    if surprise.paise and seen.paise:
        live = [(cause, amount) for cause, amount in observed.items() if amount.paise]
        for (cause, amount), share in zip(
            live, allocate(surprise, [amount.paise for _, amount in live])
        ):
            err.contributors.append(
                Attribution(
                    cause, -share,
                    f"{amount} against {modelled} modelled across the window",
                )
            )

    frozen = total(
        _amount(e) for e in events
        if e.type == "risk_hold_applied" and in_window(e.occurred_at)
    )
    released = total(
        _amount(e) for e in events
        if e.type == "risk_hold_released" and in_window(e.occurred_at)
    )
    if (released - frozen).paise:
        err.contributors.append(
            Attribution(
                Cause.RISK_HOLD, released - frozen,
                "funds released" if released.paise > frozen.paise else "funds frozen",
            )
        )
    return err


@dataclass(slots=True)
class Backtest:

    errors: list[ForecastError] = field(default_factory=list)
    horizon: int = 7

    @property
    def mape(self) -> float:
        scored = [e for e in self.errors if e.actual.paise]
        if not scored:
            return 0.0
        return sum(
            abs(e.error.paise) / abs(e.actual.paise) for e in scored
        ) / len(scored)

    @property
    def bias(self) -> Money:
        return total(e.error for e in self.errors)

    @property
    def optimism(self) -> float:
        actual = sum(e.actual.paise for e in self.errors)
        if not actual:
            return 0.0
        return -self.bias.paise / actual

    @property
    def days_short(self) -> int:
        return sum(1 for e in self.errors if e.error.paise < 0)

    def worst(self, n: int = 3) -> list[ForecastError]:
        return sorted(self.errors, key=lambda e: -abs(e.error.paise))[:n]


def backtest(
    events: list[EventBase],
    start: date,
    days: int,
    contracted: dict[str, str],
    horizon: int = 7,
    warmup: int = LOOKBACK_DAYS,
    half_life: float = HALF_LIFE_DAYS,
) -> Backtest:
    out = Backtest(horizon=horizon)
    for offset in range(warmup, days - horizon):
        as_of = start + timedelta(days=offset)
        outlook = forecast(events, as_of, horizon=horizon, half_life=half_life)
        end = as_of + timedelta(days=horizon)
        out.errors.append(
            attribute_error(
                events, end, outlook.through(end), contracted,
                since=as_of + timedelta(days=1),
                shape=Shape.learn(events, as_of, half_life=half_life),
            )
        )
    return out
