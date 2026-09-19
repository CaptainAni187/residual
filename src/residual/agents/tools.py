from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from residual.explain.close import Close
from residual.explain.qa import UnsafeQuestion, validate
from residual.ledger.accounts import Account
from residual.ledger.money import Money
from residual.ledger.warehouse import Warehouse

MAX_ROWS = 40


class ToolError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Spec:
    name: str
    description: str
    schema: dict[str, Any]


SPECS: tuple[Spec, ...] = (
    Spec(
        "unbalanced_accounts",
        "Accounts whose verifier claims do not add up to what actually moved, and "
        "the amount each is short by. This is the observable: the books do not close.",
        {"type": "object", "properties": {}},
    ),
    Spec(
        "account_movement",
        "Net movement on one account over the window, optionally restricted to the "
        "kind of event that caused it. Returns the amount in paise and the SQL run.",
        {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "event_type": {"type": "string"},
            },
            "required": ["account"],
        },
    ),
    Spec(
        "event_types_touching",
        "Which kinds of event moved one account over the window, largest first.",
        {
            "type": "object",
            "properties": {"account": {"type": "string"}},
            "required": ["account"],
        },
    ),
    Spec(
        "fee_at_contracted_rate",
        "What the gateway fee should have been if every capture were priced at the "
        "merchant's contracted rate card. Needs a contract to be in evidence.",
        {"type": "object", "properties": {}},
    ),
    Spec(
        "fee_as_billed",
        "What the gateway actually billed in fees over the window.",
        {"type": "object", "properties": {}},
    ),
    Spec(
        "run_query",
        "Run one read-only SQL SELECT against the books. Validated on the parse "
        "tree, not a denylist. Tables: postings, events, calendar, credit_links, "
        "settlement_covers. Returns at most 40 rows.",
        {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    ),
)

FINISH = Spec(
    "propose",
    "State the explanation. Give the cause a snake_case name, the accounts it "
    "claims, and one SQL query returning a single signed integer of paise. It is "
    "accepted only if that number closes the gap and leaves every account balanced.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "accounts": {"type": "array", "items": {"type": "string"}},
            "sql": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": ["name", "accounts", "sql"],
    },
)


class Toolbelt:

    def __init__(
        self,
        warehouse: Warehouse,
        close: Close,
        start: date,
        end: date,
        contracted: dict[str, str] | None = None,
    ) -> None:
        self.wh = warehouse
        self.close = close
        self.start, self.end = start, end
        self.contracted = dict(contracted or {})
        self.calls = 0

    @property
    def window(self) -> str:
        return f"occurred_at BETWEEN DATE '{self.start}' AND DATE '{self.end}'"

    def _priced(self) -> str:
        return " ".join(
            f"WHEN method = '{method}' THEN CAST(ROUND(CAST(amount_paise AS DECIMAL(38,6))"
            f" * CAST({Decimal(rate)} AS DECIMAL(12,6)) / 100, 0) AS BIGINT)"
            for method, rate in sorted(self.contracted.items())
        )

    def unbalanced_accounts(self) -> dict[str, Any]:
        gaps = [
            {"account": str(c.account), "short_by_paise": (c.actual - c.claimed).paise}
            for c in self.close.coverage
            if c.claimed.paise != c.actual.paise
        ]
        return {"accounts": gaps, "count": len(gaps)}

    def account_movement(self, account: str, event_type: str = "") -> dict[str, Any]:
        if account not in {a.value for a in Account}:
            raise ToolError(f"no such account: {account}")
        clause = " AND event_type = ?" if event_type else ""
        sql = (
            "SELECT COALESCE(SUM(amount_paise), 0) FROM postings "
            "WHERE account = ? AND occurred_at BETWEEN ? AND ?" + clause
        )
        params: list[Any] = [account, self.start, self.end]
        if event_type:
            params.append(event_type)
        amount = self.wh.scalar_money(sql, params)
        return {
            "account": account,
            "event_type": event_type or "all",
            "amount_paise": amount.paise,
            "amount": str(amount),
            "sql": self.wh.rendered(sql, params),
        }

    def event_types_touching(self, account: str) -> dict[str, Any]:
        if account not in {a.value for a in Account}:
            raise ToolError(f"no such account: {account}")
        rows = self.wh.sql(
            "SELECT event_type, SUM(amount_paise) FROM postings "
            "WHERE account = ? AND occurred_at BETWEEN ? AND ? "
            "GROUP BY event_type ORDER BY abs(SUM(amount_paise)) DESC",
            [account, self.start, self.end],
        )
        return {
            "account": account,
            "moved_by": [
                {"event_type": str(t), "amount_paise": int(v or 0)} for t, v in rows
            ],
        }

    def fee_at_contracted_rate(self) -> dict[str, Any]:
        if not self.contracted:
            raise ToolError("no contract is in evidence, so a contracted rate cannot be priced")
        sql = (
            f"SELECT COALESCE(SUM(CASE {self._priced()} ELSE 0 END), 0) FROM events "
            f"WHERE type = 'payment_captured' AND {self.window}"
        )
        amount = Money(int(self.wh.sql(sql)[0][0] or 0))
        return {"amount_paise": amount.paise, "amount": str(amount), "sql": sql}

    def fee_as_billed(self) -> dict[str, Any]:
        sql = (
            "SELECT COALESCE(SUM(fee_paise), 0) FROM events "
            f"WHERE type = 'payment_captured' AND {self.window}"
        )
        amount = Money(int(self.wh.sql(sql)[0][0] or 0))
        return {"amount_paise": amount.paise, "amount": str(amount), "sql": sql}

    def run_query(self, sql: str) -> dict[str, Any]:
        try:
            safe = validate(sql)
        except UnsafeQuestion as exc:
            raise ToolError(f"refused: {exc}") from exc
        try:
            rows = self.wh.sql(safe)
        except Exception as exc:
            raise ToolError(f"query did not run: {type(exc).__name__}") from exc
        return {
            "rows": [[_plain(v) for v in row] for row in rows[:MAX_ROWS]],
            "row_count": len(rows),
            "truncated": len(rows) > MAX_ROWS,
        }

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        args = {k: v for k, v in args.items() if not k.startswith("_")}
        method = getattr(self, name, None)
        if name not in {s.name for s in SPECS} or method is None:
            raise ToolError(f"no such tool: {name}")
        self.calls += 1
        return dict(method(**args))


def _plain(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value
