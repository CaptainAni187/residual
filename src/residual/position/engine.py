
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from residual.ledger import select
from residual.ledger.accounts import NEVER_NEGATIVE, NORMAL_BALANCE, Account, Side
from residual.ledger.events import EventBase
from residual.ledger.money import Money, total
from residual.ledger.project import project


class InvariantViolation(Exception):
    pass


class Balances(dict[Account, Money]):

    def __missing__(self, key: Account) -> Money:
        return Money.zero()

    def delta(self, other: Balances) -> Balances:
        out = Balances()
        for acct in set(self) | set(other):
            d = self[acct] - other[acct]
            if d.paise:
                out[acct] = d
        return out

    def check(self, *, complete: bool = True) -> None:
        by_currency: dict[str, Money] = {}
        for amount in list(self.values()):
            running = by_currency.get(amount.currency, Money.zero(amount.currency))
            by_currency[amount.currency] = running + amount
        for currency, imbalance in sorted(by_currency.items()):
            if imbalance.paise != 0:
                raise InvariantViolation(
                    f"{currency} books do not balance, off by {imbalance}"
                )
        if not complete:
            return
        for acct in NEVER_NEGATIVE:
            bal = self[acct]
            expected = NORMAL_BALANCE[acct]
            wrong_side = bal.paise < 0 if expected is Side.DEBIT else bal.paise > 0
            if wrong_side:
                raise InvariantViolation(f"{acct} went contra at {bal}")


def fold(events: Iterable[EventBase]) -> Balances:
    balances = Balances()
    for event in events:
        for posting in project(event).postings:
            balances[posting.account] = balances[posting.account] + posting.amount
    return balances


@dataclass(frozen=True, slots=True)
class Position:

    as_of: date
    bank: Money
    in_transit: Money
    receivable: Money
    on_hold: Money
    dispute_reserve: Money

    @property
    def available(self) -> Money:
        return self.bank

    @property
    def expected_soon(self) -> Money:
        return self.in_transit + self.receivable

    @property
    def blocked(self) -> Money:
        return self.on_hold + self.dispute_reserve


def position_at(events: Iterable[EventBase], as_of: date) -> Position:
    b = fold(events)
    b.check()
    return Position(
        as_of=as_of,
        bank=b[Account.BANK],
        in_transit=b[Account.SETTLEMENT_IN_TRANSIT],
        receivable=b[Account.GATEWAY_RECEIVABLE],
        on_hold=b[Account.ON_HOLD],
        dispute_reserve=b[Account.DISPUTE_RESERVE],
    )


_SUBJECT = frozenset({Account.REVENUE, Account.BANK})


@dataclass(frozen=True, slots=True)
class Movement:
    account: Account
    amount: Money


@dataclass(frozen=True, slots=True)
class Variance:
    window: tuple[date, date]
    gross_captured: Money
    cash_landed: Money
    credits_received: Money
    movements: tuple[Movement, ...]

    @property
    def gap(self) -> Money:
        return self.gross_captured - self.cash_landed

    @property
    def explained(self) -> Money:
        return total(m.amount for m in self.movements)

    @property
    def residual(self) -> Money:
        return self.gap - self.explained

    @property
    def closes(self) -> bool:
        return self.residual.paise == 0


def decompose(events: Sequence[EventBase], start: date, end: date) -> Variance:
    window = [e for e in events if start <= e.occurred_at <= end]

    gross = total(e.gross for e in select.captures(window))
    credits = total(e.amount for e in select.credits(window))

    deltas: dict[Account, Money] = defaultdict(Money.zero)
    for event in window:
        for p in project(event).postings:
            if p.account in _SUBJECT:
                continue
            deltas[p.account] = deltas[p.account] + p.amount

    route_split = total(e.amount for e in select.transfers(window))
    if route_split.paise:
        deltas[Account.REVENUE] = route_split

    landed = Money.zero()
    for event in window:
        for p in project(event).postings:
            if p.account is Account.BANK:
                landed = landed + p.amount

    movements = tuple(
        Movement(acct, amt) for acct, amt in sorted(deltas.items()) if amt.paise
    )
    return Variance(
        window=(start, end),
        gross_captured=gross,
        cash_landed=landed,
        credits_received=credits,
        movements=movements,
    )
