"""Before a payout is called missing, look for it on the statement every way a bank could have mangled it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from residual.ledger.warehouse import Warehouse

WINDOW_DAYS = 7
TAIL = 8  # banks often truncate a UTR; its last digits usually survive


@dataclass
class Look:
    tool: str
    args: dict[str, Any]
    found: str


@dataclass
class Search:
    looks: list[Look] = field(default_factory=list)
    matches: list[str] = field(default_factory=list)

    @property
    def confirmed_missing(self) -> bool:
        return not self.matches

    def verdict(self) -> str:
        if self.confirmed_missing:
            return (
                "no credit on the statement carries these references, any part of them, or these amounts "
                f"within {WINDOW_DAYS} days — the payouts are missing"
            )
        return "possible match on the statement: " + "; ".join(self.matches)


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def search(wh: Warehouse, utrs: list[str]) -> Search:
    out = Search()
    for utr in utrs:
        rows = wh.sql(
            "SELECT entity_id, occurred_at, amount_paise FROM events "
            "WHERE type = 'settlement_executed' AND utr = ?",
            [utr],
        )
        if not rows:
            continue
        settlement, settled_on, amount = rows[0]
        until = settled_on + timedelta(days=WINDOW_DAYS)

        by_ref = wh.sql(
            "SELECT entity_id, occurred_at FROM events WHERE type = 'bank_credit_received' "
            "AND (narration LIKE '%' || ? || '%' OR narration LIKE '%' || ? || '%')",
            [utr, utr[-TAIL:]],
        )
        out.looks.append(Look("search_reference", {"utr": utr}, f"{len(by_ref)} credit(s)"))

        by_amount = wh.sql(
            "SELECT e.entity_id, e.occurred_at, e.narration FROM events e "
            "LEFT JOIN credit_links cl ON cl.bank_txn_id = e.entity_id AND cl.settlement_id IS NOT NULL "
            "WHERE e.type = 'bank_credit_received' AND e.amount_paise = ? "
            "AND e.occurred_at BETWEEN ? AND ? AND cl.bank_txn_id IS NULL",
            [amount, settled_on, until],
        )
        out.looks.append(Look("search_amount", {"amount": _rupees(amount)}, f"{len(by_amount)} unclaimed credit(s)"))

        for txn, on, *_ in [*by_ref, *by_amount]:
            out.matches.append(f"{settlement} ({_rupees(amount)}) may be bank credit {txn} on {on}")
    return out
