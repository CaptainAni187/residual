
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from residual.ingest.gst import Return
from residual.ledger import select
from residual.ledger.events import EventBase
from residual.ledger.money import Money, total

RAZORPAY_GSTIN = "29AAGCR4375J1ZU"


def _r(amount: Money) -> str:
    return str(amount).replace("-INR ", "-₹").replace("INR ", "₹")


def _deadline(start: date) -> str:
    from residual.explain.insights import gst_claim_deadline

    return f"{gst_claim_deadline(start):%d %b %Y}"


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
            detail=f"You paid {_r(paid)} and GSTR-2B shows {_r(available)} from the gateway ({supplier_gstin}).",
            action="Nothing to chase.",
            entity_ids=invoices,
        )

    return Risk(
        kind="gst_input_credit",
        title="Input credit paid but not available to claim",
        amount=shortfall,
        detail=(
            f"You paid {_r(paid)} in GST on gateway fees this period, but GSTR-2B shows only "
            f"{_r(available)} from the gateway ({supplier_gstin}). The rest can't be claimed until "
            f"the gateway files those invoices (Section 16(2)(aa))."
        ),
        action=(
            f"Ask the gateway to file the missing invoices before {_deadline(start)}, when the "
            f"claim window closes. Until then it is tax you have paid and cannot claim."
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
        detail=(
            f"{len(bad)} invoice{'s' if len(bad) != 1 else ''} from "
            f"{', '.join(sorted({i.supplier_name or i.supplier_gstin for i in bad}))} "
            f"carr{'y' if len(bad) != 1 else 'ies'} a supplier GSTIN that is not valid "
            f"({', '.join(sorted({i.supplier_gstin for i in bad}))})."
        ),
        action="Ask the supplier to confirm their GSTIN and reissue the invoice. The credit can't be claimed as it stands.",
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
            detail=f"The gateway withheld {_r(withheld)} as TDS in this period.",
            action="Check it appears in your Form 26AS before you set it against your income tax.",
        )

    gap = withheld - per_form_26as
    if gap.paise <= 0:
        return Risk(
            kind="tds_194o",
            title="TDS withheld is fully reflected in Form 26AS",
            amount=Money.zero(),
            detail=f"Withheld {_r(withheld)}; Form 26AS shows {_r(per_form_26as)}.",
            action="Nothing to chase.",
        )
    return Risk(
        kind="tds_194o",
        title="TDS withheld but not deposited against your PAN",
        amount=gap,
        detail=f"Withheld {_r(withheld)}, but Form 26AS shows only {_r(per_form_26as)}.",
        action="The difference can't be set against your tax. Ask the gateway to deposit it.",
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
