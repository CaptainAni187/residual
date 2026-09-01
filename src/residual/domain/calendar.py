
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

NATIONAL_HOLIDAYS_2026: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 26),
        date(2026, 3, 4),
        date(2026, 3, 21),
        date(2026, 4, 3),
        date(2026, 4, 14),
        date(2026, 5, 1),
        date(2026, 8, 15),
        date(2026, 10, 2),
        date(2026, 10, 20),
        date(2026, 11, 8),
        date(2026, 12, 25),
    }
)


def is_second_or_fourth_saturday(d: date) -> bool:
    if d.weekday() != 5:
        return False
    return 8 <= d.day <= 14 or 22 <= d.day <= 28


@lru_cache(maxsize=4096)
def is_bank_day(d: date) -> bool:
    if d.weekday() == 6:
        return False
    if is_second_or_fourth_saturday(d):
        return False
    return d not in NATIONAL_HOLIDAYS_2026


def next_bank_day(d: date) -> date:
    while not is_bank_day(d):
        d += timedelta(days=1)
    return d


def add_bank_days(d: date, n: int) -> date:
    cur = d
    for _ in range(n):
        cur += timedelta(days=1)
        cur = next_bank_day(cur)
    return cur
