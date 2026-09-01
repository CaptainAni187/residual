
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Self


class CurrencyMismatch(Exception):
    pass


@dataclass(frozen=True, slots=True, order=True)
class Money:
    paise: int
    currency: str = "INR"

    def __post_init__(self) -> None:
        if not isinstance(self.paise, int) or isinstance(self.paise, bool):
            raise TypeError(f"money must be an integer number of paise, got {self.paise!r}")


    @classmethod
    def parse(cls, amount: str | int | Decimal, currency: str = "INR") -> Self:
        if isinstance(amount, float):
            raise TypeError("refusing to build Money from a float; pass a string or paise int")
        d = Decimal(amount) if not isinstance(amount, Decimal) else amount
        return cls(int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)), currency)

    @classmethod
    def zero(cls, currency: str = "INR") -> Self:
        return cls(0, currency)


    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(f"cannot combine {self.currency} and {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.paise + other.paise, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.paise - other.paise, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.paise, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.paise), self.currency)

    def __mul__(self, factor: int) -> Money:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise TypeError("money may only be scaled by an integer; use apply_rate() for rates")
        return Money(self.paise * factor, self.currency)

    def apply_rate(self, rate: str | Decimal, *, rounding: str = ROUND_HALF_UP) -> Money:
        r = Decimal(rate) if not isinstance(rate, Decimal) else rate
        product = (Decimal(self.paise) * r / Decimal(100)).quantize(Decimal(1), rounding=rounding)
        return Money(int(product), self.currency)

    def __bool__(self) -> bool:
        return self.paise != 0


    @property
    def rupees(self) -> Decimal:
        return (Decimal(self.paise) / 100).quantize(Decimal("0.01"))

    def __str__(self) -> str:
        sign = "-" if self.paise < 0 else ""
        whole, frac = divmod(abs(self.paise), 100)
        return f"{sign}{self.currency} {_indian_grouping(whole)}.{frac:02d}"

    def __format__(self, spec: str) -> str:
        return format(str(self), spec)

    def __repr__(self) -> str:
        return f"Money.parse('{self.rupees}', '{self.currency}')"


    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        from pydantic_core import core_schema

        return core_schema.no_info_plain_validator_function(
            _validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda m: {"paise": m.paise, "currency": m.currency}
            ),
        )


def _validate(v: Any) -> Money:
    if isinstance(v, Money):
        return v
    if isinstance(v, int) and not isinstance(v, bool):
        return Money(v)
    if isinstance(v, dict):
        return Money(int(v["paise"]), v.get("currency", "INR"))
    if isinstance(v, str):
        return Money.parse(v)
    raise TypeError(f"cannot coerce {v!r} to Money")


def _indian_grouping(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def allocate(amount: Money, weights: Iterable[int]) -> list[Money]:
    weights = list(weights)
    if not weights:
        return []
    if any(w < 0 for w in weights):
        raise ValueError(f"cannot apportion by negative weights: {weights}")
    denominator = sum(weights)
    if denominator == 0:
        if amount.paise:
            raise ValueError(f"cannot apportion {amount} across weights that sum to zero")
        return [Money.zero(amount.currency) for _ in weights]

    sign = -1 if amount.paise < 0 else 1
    magnitude = abs(amount.paise)
    shares, remainders = [], []
    for i, weight in enumerate(weights):
        exact, remainder = divmod(magnitude * weight, denominator)
        shares.append(exact)
        remainders.append((remainder, i))

    leftover = magnitude - sum(shares)
    for _, i in sorted(remainders, reverse=True)[:leftover]:
        shares[i] += 1
    return [Money(sign * share, amount.currency) for share in shares]


def total(amounts: Iterable[Money]) -> Money:
    acc: Money | None = None
    for a in amounts:
        acc = a if acc is None else acc + a
    return acc if acc is not None else Money.zero()
