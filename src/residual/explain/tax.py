
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from residual.ingest.gst import Return
from residual.ledger import select
from residual.ledger.events import EventBase
from residual.ledger.money import Money, total

RAZORPAY_GSTIN = "29AAGCR4375J1ZU"


@dataclass(frozen=True, slots=True)
class Risk:

    kind: str
    title: str
    amount: Money
    detail: str
    action: str
    entity_ids: tuple[str, ...] = ()

    @property
    def material(self) -> bool:
        return self.amount.paise != 0


def gst_input_credit(
    events: list[EventBase],
    gstr2b: Return,
    start: date,
    end: date,
    supplier_gstin: str = RAZORPAY_GSTIN,
) -> Risk | None:
    paid = total(
        e.tax for e in select.captures(events) if start <= e.occurred_at <= end
    )
    if not paid.paise:
        return None

    available = gstr2b.credit_from(supplier_gstin)
    shortfall = paid - available
    invoices = tuple(i.invoice_no for i in gstr2b.from_supplier(supplier_gstin))

    if shortfall.paise <= 0:
        return Risk(
            kind="gst_input_credit",
            title="Input credit on gateway fees is fully available",
            amount=Money.zero(),
            detail=f"paid {paid}, GSTR-2B shows {available} against {supplier_gstin}",
            action="nothing to chase",
            entity_ids=invoices,
        )

    return Risk(
        kind="gst_input_credit",
        title="Input credit paid but not available to claim",
        amount=shortfall,
        detail=(
            f"{paid} of GST was charged on gateway fees in this period, but "
            f"GSTR-2B shows only {available} against {supplier_gstin}. "
            f"Under s.16(2)(aa) the difference cannot be claimed until the "
            f"supplier files it."
        ),
        action=(
            "raise the gap with the gateway before the claim window closes; "
            "it is cash the merchant has already paid out"
        ),
        entity_ids=invoices,
    )


def unmatched_suppliers(gstr2b: Return, supplier_gstin: str = RAZORPAY_GSTIN) -> Risk | None:
    bad = gstr2b.malformed_gstins
    if not bad:
        return None
    at_risk = total(i.total_tax for i in bad)
    return Risk(
        kind="malformed_gstin",
        title="Credit sitting against a GSTIN that does not validate",
        amount=at_risk,
        detail=f"{len(bad)} invoice(s) carry a supplier GSTIN that fails the format check",
        action="confirm the supplier's GSTIN before filing; the credit is not claimable as it stands",
        entity_ids=tuple(i.invoice_no for i in bad),
    )


def tds_deposited(
    events: list[EventBase],
    start: date,
    end: date,
    per_form_26as: Money | None = None,
) -> Risk | None:
    withheld = total(
        e.tds for e in select.captures(events) if start <= e.occurred_at <= end
    )
    if not withheld.paise:
        return None

    if per_form_26as is None:
        return Risk(
            kind="tds_194o",
            title="TDS withheld under s.194-O, not yet matched to Form 26AS",
            amount=withheld,
            detail=f"{withheld} was withheld by the gateway in this period",
            action="check it appears in Form 26AS before setting it against a liability",
        )

    gap = withheld - per_form_26as
    if gap.paise <= 0:
        return Risk(
            kind="tds_194o",
            title="TDS withheld is fully reflected in Form 26AS",
            amount=Money.zero(),
            detail=f"withheld {withheld}, 26AS shows {per_form_26as}",
            action="nothing to chase",
        )
    return Risk(
        kind="tds_194o",
        title="TDS withheld but not deposited against the merchant's PAN",
        amount=gap,
        detail=f"withheld {withheld}, Form 26AS shows only {per_form_26as}",
        action="the difference cannot be set against a liability; raise it with the gateway",
    )


def assess(
    events: list[EventBase],
    start: date,
    end: date,
    gstr2b: Return | None = None,
    form_26as: Money | None = None,
    supplier_gstin: str = RAZORPAY_GSTIN,
) -> list[Risk]:
    out: list[Risk] = []
    if gstr2b is not None:
        for risk in (
            gst_input_credit(events, gstr2b, start, end, supplier_gstin),
            unmatched_suppliers(gstr2b, supplier_gstin),
        ):
            if risk is not None:
                out.append(risk)
    if (tds := tds_deposited(events, start, end, form_26as)) is not None:
        out.append(tds)
    out.sort(key=lambda r: -abs(r.amount.paise))
    return out
