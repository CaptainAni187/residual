
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from residual.domain.text import normalise
from residual.ledger import select
from residual.ledger.events import EventBase
from residual.ledger.money import Money
from residual.ledger.warehouse import Warehouse


class Rule(str):
    pass


EXACT_UTR = Rule("exact_utr")
NORMALISED_UTR = Rule("normalised_utr")
TRUNCATED_PREFIX = Rule("truncated_prefix")
AMOUNT_DATE_UNIQUE = Rule("amount_date_unique")
ABSTAIN_AMBIGUOUS = Rule("abstain_ambiguous")
ABSTAIN_NO_CANDIDATE = Rule("abstain_no_candidate")

CONFIDENCE: dict[Rule, float] = {
    EXACT_UTR: 1.00,
    NORMALISED_UTR: 0.97,
    TRUNCATED_PREFIX: 0.90,
    AMOUNT_DATE_UNIQUE: 0.80,
    ABSTAIN_AMBIGUOUS: 0.35,
    ABSTAIN_NO_CANDIDATE: 0.00,
}

DEFAULT_THRESHOLD = 0.75


@dataclass(slots=True)
class Work:

    credits: int = 0
    candidates_considered: int = 0

    @property
    def per_credit(self) -> float:
        return self.candidates_considered / self.credits if self.credits else 0.0


last_work = Work()


@dataclass(frozen=True, slots=True)
class Link:
    bank_txn_id: str
    settlement_id: str | None
    rule: Rule
    confidence: float
    candidates: int
    reason: str = ""

    @property
    def linked(self) -> bool:
        return self.settlement_id is not None


@dataclass(frozen=True, slots=True)
class _Candidate:
    settlement_id: str
    utr: str
    amount: Money
    settled_on: date


def link_credits(
    wh: Warehouse,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    lookback_days: int = 6,
    forward_days: int = 1,
    greedy: bool = False,
) -> list[Link]:
    credits = wh.sql(
        "SELECT entity_id, amount_paise, occurred_at, narration FROM events "
        "WHERE type = 'bank_credit_received' ORDER BY occurred_at, entity_id"
    )
    settlements = [
        _Candidate(sid, utr, Money(int(amt)), on)
        for sid, utr, amt, on in wh.sql(
            "SELECT entity_id, utr, amount_paise, occurred_at FROM events "
            "WHERE type = 'settlement_executed' ORDER BY occurred_at, entity_id"
        )
    ]
    return _link(credits, settlements, threshold, lookback_days, forward_days, greedy)


def link_events(
    events: Iterable[EventBase],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    lookback_days: int = 6,
    forward_days: int = 1,
) -> list[Link]:
    credits = sorted(
        (
            (e.bank_txn_id, e.amount.paise, e.occurred_at, e.narration)
            for e in select.credits(events)
        ),
        key=lambda row: (row[2], row[0]),
    )
    settlements = [
        _Candidate(s.settlement_id, s.utr, s.net, s.occurred_at)
        for s in sorted(
            select.settlements(events), key=lambda s: (s.occurred_at, s.settlement_id)
        )
    ]
    return _link(credits, settlements, threshold, lookback_days, forward_days, False)


def _link(
    credits: list[tuple[str, int, date, str]],
    settlements: list[_Candidate],
    threshold: float,
    lookback_days: int,
    forward_days: int,
    greedy: bool,
) -> list[Link]:

    by_day: dict[date, list[_Candidate]] = defaultdict(list)
    for candidate in settlements:
        by_day[candidate.settled_on].append(candidate)

    work = Work(credits=len(credits))

    def window_for(occurred: date, pool: set[str] | None = None) -> list[_Candidate]:
        found: list[_Candidate] = []
        for offset in range(-lookback_days, forward_days + 1):
            for candidate in by_day.get(occurred + timedelta(days=offset), ()):
                work.candidates_considered += 1
                if pool is None or candidate.settlement_id in pool:
                    found.append(candidate)
        found.sort(key=lambda c: (c.settled_on, c.settlement_id))
        return found

    links: dict[str, Link] = {}
    available = {c.settlement_id for c in settlements}
    unresolved: list[tuple[str, Money, date, str]] = []

    for btx, amount_paise, occurred, narration in credits:
        amount = Money(int(amount_paise))
        window = window_for(occurred, available)
        if not window:
            links[btx] = Link(
                btx, None, ABSTAIN_NO_CANDIDATE, 0.0, 0, "no settlement in the date window"
            )
            continue
        link = _match_by_reference(btx, amount, narration, window, threshold)
        if link is None:
            unresolved.append((btx, amount, occurred, narration))
            continue
        if link.settlement_id:
            available.discard(link.settlement_id)
        links[btx] = link

    remaining = set(available)
    for btx, amount, occurred, _ in unresolved:
        window = window_for(occurred, remaining)
        exact = [c for c in window if c.amount.paise == amount.paise]
        if greedy and exact:
            links[btx] = _accept(
                btx, exact[0], AMOUNT_DATE_UNIQUE, len(exact), threshold,
                "first candidate matching amount and date",
            )
            remaining.discard(exact[0].settlement_id)
            continue
        if not exact:
            links[btx] = Link(
                btx, None, ABSTAIN_NO_CANDIDATE, 0.0, len(window),
                "no candidate matches on reference or amount",
            )
            continue

        rivals = [
            other
            for other, oamount, ooccurred, _ in unresolved
            if other != btx
            and oamount.paise == amount.paise
            and any(c in exact for c in window_for(ooccurred, remaining))
        ]
        if len(exact) > 1 or rivals:
            links[btx] = Link(
                btx, None, ABSTAIN_AMBIGUOUS, CONFIDENCE[ABSTAIN_AMBIGUOUS],
                len(exact),
                f"{len(exact)} settlement(s) and {len(rivals) + 1} credits share this "
                f"amount and window, with no reference to separate them -- not guessing",
            )
            continue

        links[btx] = _accept(
            btx, exact[0], AMOUNT_DATE_UNIQUE, 1, threshold,
            "no reference in narration; unique amount and date match",
        )
        remaining.discard(exact[0].settlement_id)

    global last_work
    last_work = work
    return [links[btx] for btx, _, _, _ in credits]


def _match_by_reference(
    btx: str, amount: Money, narration: str, window: list[_Candidate], threshold: float
) -> Link | None:
    norm = normalise(narration)

    hits = sorted((c for c in window if c.utr in narration), key=lambda c: -len(c.utr))
    if len(hits) == 1 or (hits and len(hits[0].utr) > len(hits[1].utr)):
        return _accept(btx, hits[0], EXACT_UTR, len(hits), threshold, "UTR found in narration")

    hits = [c for c in window if c.utr in norm]
    if len(hits) == 1:
        return _accept(btx, hits[0], NORMALISED_UTR, 1, threshold, "UTR found after normalising")

    prefixed = [
        c
        for c in window
        if c.amount.paise == amount.paise and any(c.utr[:n] in norm for n in (12, 10, 8))
    ]
    if len(prefixed) == 1:
        return _accept(
            btx, prefixed[0], TRUNCATED_PREFIX, 1, threshold,
            "narration truncated; matched on UTR prefix plus exact amount",
        )
    return None


def _accept(
    btx: str, c: _Candidate, rule: Rule, n: int, threshold: float, reason: str
) -> Link:
    conf = CONFIDENCE[rule]
    if conf < threshold:
        return Link(btx, None, rule, conf, n, f"{reason} (below threshold {threshold})")
    return Link(btx, c.settlement_id, rule, conf, n, reason)


def load_links(wh: Warehouse, links: list[Link]) -> None:
    wh.cursor.execute("DELETE FROM credit_links")
    wh.links_loaded = True
    if links:
        wh.cursor.executemany(
            "INSERT INTO credit_links VALUES (?, ?, ?, ?, ?, ?)",
            [
                (a.bank_txn_id, a.settlement_id, str(a.rule), a.confidence, a.candidates, a.reason)
                for a in links
            ],
        )
