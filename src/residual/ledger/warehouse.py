
from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import partial
from typing import Any

import duckdb
import polars as pl

from residual.domain.calendar import add_bank_days, is_bank_day
from residual.ledger.events import EventBase, SettlementExecuted
from residual.ledger.money import Money
from residual.ledger.project import project


def _calendar_rows(lo: date, hi: date) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    d = lo
    while d <= hi:
        naive = d + timedelta(days=2)
        actual = add_bank_days(d, 2)
        rows.append((d, is_bank_day(d), naive, actual, (actual - naive).days))
        d += timedelta(days=1)
    return rows

SCHEMA = """
CREATE TABLE events (
    seq           BIGINT,
    event_id      VARCHAR,
    type          VARCHAR,
    occurred_at   DATE,
    recorded_at   DATE,
    entity_id     VARCHAR,
    counterparty  VARCHAR,
    method        VARCHAR,
    amount_paise  BIGINT,
    fee_paise     BIGINT,
    tax_paise     BIGINT,
    tds_paise     BIGINT,
    utr           VARCHAR,
    narration     VARCHAR,
    detail        VARCHAR
);
CREATE TABLE settlement_covers (
    settlement_id VARCHAR,
    utr           VARCHAR,
    payment_id    VARCHAR,
    settled_on    DATE
);
CREATE TABLE calendar (
    d             DATE,
    is_bank_day   BOOLEAN,
    t2_naive      DATE,
    t2_actual     DATE,
    slipped_days  INTEGER
);
-- Created empty and populated by the linkage layer. It exists from the start
-- because a verifier that asks "did any credit link to this payout" must get
-- "no" rather than a missing-table error when nothing has been linked yet.
CREATE TABLE credit_links (
    bank_txn_id   VARCHAR,
    settlement_id VARCHAR,
    rule          VARCHAR,
    confidence    DOUBLE,
    candidates    INTEGER,
    reason        VARCHAR
);
CREATE TABLE postings (
    seq           BIGINT,
    event_id      VARCHAR,
    event_type    VARCHAR,
    occurred_at   DATE,
    recorded_at   DATE,
    account       VARCHAR,
    amount_paise  BIGINT,
    ref           VARCHAR,
    memo          VARCHAR
);
"""

_FIELDS: dict[str, dict[str, str]] = {
    "payment_captured": {
        "entity_id": "payment_id", "amount": "gross", "fee": "fee", "tax": "tax",
        "tds": "tds", "method": "method", "counterparty": "order_id",
    },
    "payment_failed": {
        "entity_id": "payment_id", "amount": "gross", "method": "method",
        "counterparty": "order_id", "detail": "error_code",
    },
    "refund_issued": {
        "entity_id": "refund_id", "amount": "amount", "counterparty": "payment_id",
        "detail": "speed",
    },
    "dispute_opened": {
        "entity_id": "dispute_id", "amount": "amount", "counterparty": "payment_id",
        "detail": "reason_code",
    },
    "dispute_resolved": {
        "entity_id": "dispute_id", "amount": "amount", "counterparty": "payment_id",
    },
    "risk_hold_applied": {"entity_id": "hold_id", "amount": "amount", "detail": "reason"},
    "risk_hold_released": {"entity_id": "hold_id", "amount": "amount"},
    "settlement_executed": {
        "entity_id": "settlement_id", "amount": "net", "utr": "utr",
        "fee": "instant_fee",
    },
    "bank_credit_received": {
        "entity_id": "bank_txn_id", "amount": "amount", "narration": "narration",
    },
    "bank_charge_applied": {
        "entity_id": "bank_txn_id", "amount": "amount", "narration": "narration",
    },
    "bank_debit": {
        "entity_id": "bank_txn_id", "amount": "amount", "narration": "narration",
    },
    "gateway_adjustment": {
        "entity_id": "adjustment_id", "amount": "amount", "detail": "reason",
    },
    "route_transfer": {
        "entity_id": "transfer_id", "amount": "amount", "counterparty": "payment_id",
        "detail": "to_account",
    },
    "fee_schedule_changed": {"method": "method", "detail": "new_rate"},
}


def _paise(v: Any) -> int | None:
    return v.paise if isinstance(v, Money) else None


_EVENT_COLS: list[tuple[str, Any]] = [
    ("seq", pl.Int64), ("event_id", pl.Utf8), ("type", pl.Utf8),
    ("occurred_at", pl.Date), ("recorded_at", pl.Date), ("entity_id", pl.Utf8),
    ("counterparty", pl.Utf8), ("method", pl.Utf8), ("amount_paise", pl.Int64),
    ("fee_paise", pl.Int64), ("tax_paise", pl.Int64), ("tds_paise", pl.Int64),
    ("utr", pl.Utf8), ("narration", pl.Utf8), ("detail", pl.Utf8),
]
_POSTING_COLS: list[tuple[str, Any]] = [
    ("seq", pl.Int64), ("event_id", pl.Utf8), ("event_type", pl.Utf8),
    ("occurred_at", pl.Date), ("recorded_at", pl.Date), ("account", pl.Utf8),
    ("amount_paise", pl.Int64), ("ref", pl.Utf8), ("memo", pl.Utf8),
]
_COVER_COLS: list[tuple[str, Any]] = [
    ("settlement_id", pl.Utf8), ("utr", pl.Utf8),
    ("payment_id", pl.Utf8), ("settled_on", pl.Date),
]
_CALENDAR_COLS: list[tuple[str, Any]] = [
    ("d", pl.Date), ("is_bank_day", pl.Boolean), ("t2_naive", pl.Date),
    ("t2_actual", pl.Date), ("slipped_days", pl.Int32),
]


def _insert(
    con: duckdb.DuckDBPyConnection,
    table: str,
    rows: list[tuple[Any, ...]],
    cols: list[tuple[str, Any]],
) -> None:
    if not rows:
        return
    frame = pl.DataFrame(  # noqa: F841 -- referenced by name in the SQL below
        rows, schema=dict(cols), orient="row"
    )
    con.execute(f"INSERT INTO {table} SELECT * FROM frame")


@dataclass(slots=True)
class Warehouse:

    con: duckdb.DuckDBPyConnection

    links_loaded: bool = False

    _local: threading.local = field(default_factory=threading.local, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def cursor(self) -> duckdb.DuckDBPyConnection:
        existing = getattr(self._local, "cursor", None)
        if existing is None:
            existing = self.con.cursor()
            self._local.cursor = existing
        return existing

    @classmethod
    def build(cls, events: Iterable[EventBase], path: str = ":memory:") -> Warehouse:
        con = duckdb.connect(path)
        for table in ("events", "postings", "settlement_covers", "calendar", "credit_links"):
            con.execute(f"DROP TABLE IF EXISTS {table}")
        con.execute(SCHEMA)

        def field(event: EventBase, spec: dict[str, str], key: str) -> Any:
            attr = spec.get(key)
            return getattr(event, attr, None) if attr else None

        erows: list[tuple[Any, ...]] = []
        prows: list[tuple[Any, ...]] = []
        crows: list[tuple[Any, ...]] = []
        for e in events:
            spec = _FIELDS.get(e.type, {})
            get = partial(field, e, spec)

            erows.append(
                (
                    e.seq, e.event_id, e.type, e.occurred_at, e.recorded_at,
                    get("entity_id"), get("counterparty"),
                    str(get("method")) if get("method") else None,
                    _paise(get("amount")), _paise(get("fee")),
                    _paise(get("tax")), _paise(get("tds")),
                    get("utr"), get("narration"),
                    str(get("detail")) if get("detail") is not None else None,
                )
            )
            if isinstance(e, SettlementExecuted):
                crows.extend(
                    (e.settlement_id, e.utr, pid, e.occurred_at) for pid in e.covers
                )

            entry = project(e)
            for p in entry.postings:
                prows.append(
                    (
                        e.seq, e.event_id, entry.event_type, e.occurred_at, e.recorded_at,
                        str(p.account), p.amount.paise, p.ref, p.memo,
                    )
                )

        _insert(con, "events", erows, _EVENT_COLS)
        _insert(con, "postings", prows, _POSTING_COLS)
        _insert(con, "settlement_covers", crows, _COVER_COLS)

        if erows:
            lo = min(r[3] for r in erows)
            hi = max(r[3] for r in erows) + timedelta(days=14)
            _insert(
                con, "calendar",
                _calendar_rows(lo - timedelta(days=14), hi),
                _CALENDAR_COLS,
            )

        con.execute("CREATE INDEX idx_p_occ ON postings(occurred_at);")
        con.execute("CREATE INDEX idx_e_occ ON events(occurred_at);")
        con.execute("CREATE INDEX idx_c_pay ON settlement_covers(payment_id);")
        return cls(con)

    def sql(self, query: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        return self.cursor.execute(query, list(params)).fetchall()

    def scalar_money(self, query: str, params: Sequence[Any] = ()) -> Money:
        row = self.cursor.execute(query, list(params)).fetchone()
        return Money(int(row[0])) if row and row[0] is not None else Money.zero()

    def columns(self, query: str, params: Sequence[Any] = ()) -> tuple[list[str], list[tuple]]:
        result = self.cursor.execute(query, list(params))
        return [d[0] for d in result.description or []], result.fetchall()

    def rendered(self, query: str, params: Sequence[Any] = ()) -> str:
        out = query
        for p in params:
            if isinstance(p, str) or hasattr(p, "isoformat"):
                literal = "'" + str(p).replace("'", "''") + "'"
            else:
                literal = str(p)
            out = out.replace("?", literal, 1)
        return " ".join(out.split())
