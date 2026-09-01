
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from residual.ledger.accounts import Account
from residual.ledger.money import Money, total


class Unbalanced(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Posting:
    account: Account
    amount: Money
    ref: str = ""
    memo: str = ""


@dataclass(frozen=True, slots=True)
class Entry:
    event_id: str
    event_type: str
    occurred_at: date
    recorded_at: date
    postings: tuple[Posting, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.postings:
            return
        imbalance = total(p.amount for p in self.postings)
        if imbalance.paise != 0:
            raise Unbalanced(
                f"{self.event_type} {self.event_id} is off by {imbalance}: "
                + ", ".join(f"{p.account}={p.amount}" for p in self.postings)
            )


def debit(account: Account, amount: Money, ref: str = "", memo: str = "") -> Posting:
    return Posting(account, abs(amount), ref, memo)


def credit(account: Account, amount: Money, ref: str = "", memo: str = "") -> Posting:
    return Posting(account, -abs(amount), ref, memo)
