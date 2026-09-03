
from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from residual.ledger import events as ev
from residual.ledger.money import Money

API = "https://api.razorpay.com/v1"
RECON_URL = f"{API}/settlements/recon/combined"
PAYMENTS_URL = f"{API}/payments"

_METHODS = {
    "card": ev.Method.CARD,
    "upi": ev.Method.UPI,
    "netbanking": ev.Method.NETBANKING,
    "wallet": ev.Method.WALLET,
    "emi": ev.Method.EMI,
}


class NotConfigured(Exception):
    pass


class UnsupportedRow(Exception):
    pass


def _day(epoch: int | None, fallback: date) -> date:
    if not epoch:
        return fallback
    return datetime.fromtimestamp(int(epoch), tz=UTC).date()


def _money(paise: Any) -> Money:
    return Money(int(paise or 0))


KNOWN_TYPES = frozenset({"payment", "refund", "transfer", "adjustment"})


def to_events(
    rows: Iterable[dict[str, Any]],
    on: date | None = None,
    currency: str = "INR",
    strict: bool = True,
) -> list[ev.EventBase]:
    rows = list(rows)
    fallback = on or datetime.now(tz=UTC).date()

    foreign = sorted({str(r.get("currency") or currency) for r in rows} - {currency})
    unmapped = sorted({str(r.get("type")) for r in rows} - KNOWN_TYPES - {"None"})
    if strict and foreign:
        raise UnsupportedRow(
            f"this ledger is {currency}-only and the report contains {foreign}; "
            f"a foreign amount recorded at par is silently wrong, so it is refused"
        )
    if strict and unmapped:
        raise UnsupportedRow(
            f"unmapped row type(s) {unmapped}: their credits would still build "
            f"the settlement total while producing no posting, so the payout "
            f"would exceed what entered the receivable"
        )
    if not strict:
        rows = [
            r for r in rows
            if str(r.get("currency") or currency) == currency
            and str(r.get("type")) in KNOWN_TYPES
        ]
    out: list[ev.EventBase] = []
    batches: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"credit": 0, "debit": 0, "covers": [], "utr": "", "settled_at": None}
    )

    for i, row in enumerate(rows):
        kind = row.get("type")
        entity = row.get("entity_id") or f"unknown_{i}"
        occurred = _day(row.get("created_at"), fallback)
        recorded = fallback

        if sid := row.get("settlement_id"):
            batch = batches[sid]
            batch["credit"] += int(row.get("credit") or 0)
            batch["debit"] += int(row.get("debit") or 0)
            batch["covers"].append(entity)
            batch["utr"] = batch["utr"] or (row.get("settlement_utr") or "")
            batch["settled_at"] = batch["settled_at"] or row.get("settled_at")

        if kind == "payment":
            out.append(
                ev.PaymentCaptured(
                    event_id=f"rzp-cap-{entity}",
                    occurred_at=occurred,
                    recorded_at=recorded,
                    payment_id=entity,
                    order_id=row.get("order_id") or "",
                    gross=_money(row.get("amount")),
                    method=_METHODS.get(row.get("method") or "", ev.Method.NETBANKING),
                    fee=_money(row.get("fee")),
                    tax=_money(row.get("tax")),
                    card_network=row.get("card_network"),
                )
            )
        elif kind == "refund":
            out.append(
                ev.RefundIssued(
                    event_id=f"rzp-ref-{entity}",
                    occurred_at=occurred,
                    recorded_at=recorded,
                    refund_id=entity,
                    payment_id=row.get("payment_id") or "",
                    amount=_money(row.get("amount")),
                )
            )
        elif kind == "transfer":
            out.append(
                ev.RouteTransfer(
                    event_id=f"rzp-trf-{entity}",
                    occurred_at=occurred,
                    recorded_at=recorded,
                    transfer_id=entity,
                    payment_id=row.get("payment_id") or "",
                    amount=_money(row.get("amount")),
                    to_account=row.get("description") or "linked account",
                )
            )
        elif kind == "adjustment" and int(row.get("debit") or 0):
            out.append(
                ev.GatewayAdjustment(
                    event_id=f"rzp-adj-{entity}",
                    occurred_at=occurred,
                    recorded_at=recorded,
                    adjustment_id=entity,
                    amount=_money(row.get("debit")),
                    reason=row.get("description") or "gateway adjustment",
                )
            )

    for sid, batch in batches.items():
        net = batch["credit"] - batch["debit"]
        if net <= 0:
            continue
        settled_on = _day(batch["settled_at"], fallback)
        out.append(
            ev.SettlementExecuted(
                event_id=f"rzp-setl-{sid}",
                occurred_at=settled_on,
                recorded_at=fallback,
                settlement_id=sid,
                utr=batch["utr"],
                net=Money(net),
                covers=tuple(batch["covers"]),
            )
        )

    out.sort(key=lambda e: (e.occurred_at, e.event_id))
    return out


def _credentials() -> tuple[str, str]:
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not key_id or not key_secret:
        raise NotConfigured(
            "set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in a gitignored .env "
            "(test-mode keys only -- this repository is public)"
        )
    if not key_id.startswith("rzp_test_"):
        raise NotConfigured(
            f"refusing to use a non-test key ({key_id[:9]}...); this tool is for "
            "test mode only and will not touch a live merchant account"
        )
    return key_id, key_secret


def fetch(on: date, timeout: float = 30.0) -> list[dict[str, Any]]:
    key_id, key_secret = _credentials()

    import httpx

    response = httpx.get(
        RECON_URL,
        params={"year": on.year, "month": f"{on.month:02d}", "day": f"{on.day:02d}"},
        auth=(key_id, key_secret),
        timeout=timeout,
    )
    response.raise_for_status()
    return list(response.json().get("items", []))


def probe(timeout: float = 30.0) -> dict[str, Any]:
    key_id, key_secret = _credentials()

    import httpx

    response = httpx.get(
        PAYMENTS_URL, params={"count": 3}, auth=(key_id, key_secret), timeout=timeout
    )
    if response.status_code == 401:
        raise NotConfigured(
            "the gateway rejected these credentials; regenerate the pair from "
            "Dashboard -> Account & Settings -> API Keys and copy both halves"
        )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items", [])
    return {
        "authenticated": True,
        "key_id": key_id,
        "payments_visible": int(payload.get("count", len(items))),
        "sample_fields": sorted(items[0]) if items else [],
    }


PII_FIELDS = frozenset({"email", "contact", "vpa", "customer_id", "card_id", "notes"})

PAYMENT_STATES = frozenset({"created", "authorized", "captured", "refunded", "failed"})

# Razorpay reports `fee` inclusive of `tax`; this ledger keeps them apart.


def _strip_pii(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in PII_FIELDS}


def payments_to_events(
    rows: Iterable[dict[str, Any]], currency: str = "INR", strict: bool = True
) -> list[ev.EventBase]:
    rows = [_strip_pii(r) for r in rows]

    foreign = sorted({str(r.get("currency") or currency) for r in rows} - {currency})
    if strict and foreign:
        raise UnsupportedRow(
            f"this ledger is {currency}-only and the payments include {foreign}; "
            f"a foreign amount recorded at par is silently wrong, so it is refused"
        )
    unknown = sorted({str(r.get("status")) for r in rows} - PAYMENT_STATES)
    if strict and unknown:
        raise UnsupportedRow(f"unmapped payment status {unknown}")
    if not strict:
        rows = [
            r for r in rows
            if str(r.get("currency") or currency) == currency
            and str(r.get("status")) in PAYMENT_STATES
        ]

    out: list[ev.EventBase] = []
    for row in rows:
        payment_id = str(row.get("id") or "")
        if not payment_id:
            raise UnsupportedRow("a payment row carries no id")
        status = str(row.get("status") or "")
        when = _day(row.get("created_at"), datetime.now(tz=UTC).date())
        order_id = str(row.get("order_id") or "")
        method_name = str(row.get("method") or "")
        method = _METHODS.get(method_name)
        if method is None:
            if strict:
                raise UnsupportedRow(f"unmapped payment method {method_name!r} on {payment_id}")
            continue

        if status in {"captured", "refunded"}:
            billed = _money(row.get("fee"))
            tax = _money(row.get("tax"))
            if tax.paise > billed.paise:
                raise UnsupportedRow(
                    f"{payment_id}: tax {tax} exceeds the fee {billed} it is part of"
                )
            out.append(
                ev.PaymentCaptured(
                    event_id=payment_id,
                    occurred_at=when,
                    recorded_at=when,
                    payment_id=payment_id,
                    order_id=order_id,
                    gross=_money(row.get("amount")),
                    method=method,
                    fee=billed - tax,
                    tax=tax,
                )
            )
        elif status == "failed":
            out.append(
                ev.PaymentFailed(
                    event_id=payment_id,
                    occurred_at=when,
                    recorded_at=when,
                    payment_id=payment_id,
                    order_id=order_id,
                    gross=_money(row.get("amount")),
                    method=method,
                    error_code=str(row.get("error_code") or "unknown"),
                )
            )

        refunded = _money(row.get("amount_refunded"))
        if refunded.paise:
            out.append(
                ev.RefundIssued(
                    event_id=f"{payment_id}:refund",
                    occurred_at=when,
                    recorded_at=when,
                    refund_id=f"rfnd_{payment_id}",
                    payment_id=payment_id,
                    amount=refunded,
                )
            )

    out.sort(key=lambda e: (e.occurred_at, e.event_id))
    return out


def fetch_payments(count: int = 100, timeout: float = 30.0) -> list[dict[str, Any]]:
    key_id, key_secret = _credentials()

    import httpx

    response = httpx.get(
        PAYMENTS_URL,
        params={"count": min(count, 100)},
        auth=(key_id, key_secret),
        timeout=timeout,
    )
    response.raise_for_status()
    return list(response.json().get("items", []))
