from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from residual.explain.close import Close, Finding
from residual.explain.grounding import check, extract_amounts
from residual.explain.tax import Risk
from residual.ledger.money import Money

KINDS = ("escalate", "payout_trace", "fee_dispute", "gst_followup")

POLISH = """\
You are editing an email a merchant will send to their payment gateway.

Rewrite it to read naturally and professionally. Hard rules:
- Keep every amount, date, UTR, reference and identifier exactly as written.
- Do not add any number, date or reference that is not already in the draft.
- Do not remove any line item.
- Keep it short. No pleasantries beyond one line. No sign-off name.
- Reply with the email body only, no subject line and no commentary."""


class Speaker(Protocol):

    @property
    def ready(self) -> bool: ...

    def describe(self) -> str: ...

    def say(self, system: str, prompt: str, max_tokens: int = 400) -> str: ...


class NothingToDraft(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Draft:
    kind: str
    to: str
    subject: str
    body: str
    source: str
    reason: str
    items: int


def _flagged(close: Close, *causes: str) -> list[Finding]:
    return [f for f in close.findings if str(f.cause) in causes and f.amount.paise]


def _where(finding: Finding) -> str:
    ids = [i for i in finding.evidence.entity_ids[:3] if i]
    if not ids:
        return ""
    if str(finding.cause) == "fee_rate_increase":
        return f" (on {', '.join(ids)} payments)"
    if str(finding.cause) == "settlement_never_arrived":
        return f" (UTR {', '.join(ids)})"
    return f" (ref {', '.join(ids)})"


def _by_method(finding: Finding) -> str:
    from residual.explain.insights import METHOD_NAME

    lines = []
    for part in finding.evidence.note.split("--", 1)[-1].split(", "):
        method, _, rest = part.strip().partition(": ")
        if rest:
            lines.append(f"  - {METHOD_NAME.get(method, method.title())} payments: {rest}")
    return "\n".join(lines)


def _period(start: date, end: date) -> str:
    return f"{start:%d %b %Y} to {end:%d %b %Y}"


def _escalate(close: Close, start: date, end: date) -> tuple[str, str, str, int]:
    items = [f for f in close.findings if f.alarming and f.amount.paise]
    if not items:
        raise NothingToDraft("nothing in this period needs escalating")
    lines = "\n".join(f"  - {f.title}: {f.amount}{_where(f)}" for f in items)
    body = (
        f"Hello,\n\n"
        f"Reconciling our settlements for {_period(start, end)}, the following do not match "
        f"what we expected and need your review:\n\n{lines}\n\n"
        f"Please confirm the status of each and, where money is owed to us, when it will "
        f"be released.\n\nThank you."
    )
    return (
        "Your payment gateway's support team",
        f"Settlement discrepancies for {_period(start, end)}",
        body,
        len(items),
    )


def _payout_trace(close: Close, start: date, end: date) -> tuple[str, str, str, int]:
    items = _flagged(close, "settlement_never_arrived")
    if not items:
        raise NothingToDraft("every payout in this period reached the bank")
    finding = items[0]
    utrs = finding.evidence.entity_ids
    listed = "\n".join(f"  - UTR {utr}" for utr in utrs) or "  - (no UTR recorded)"
    body = (
        f"Hello,\n\n"
        f"The following payout(s) were marked as executed but have not been credited to our "
        f"bank account. The total outstanding is {finding.amount}.\n\n{listed}\n\n"
        f"Please trace these with the settling bank and share the credit confirmation, or "
        f"reissue them if they were returned.\n\nThank you."
    )
    return (
        "Your payment gateway's settlements team",
        f"Payout not received — {finding.amount}",
        body,
        max(len(utrs), 1),
    )


def _fee_dispute(close: Close, start: date, end: date) -> tuple[str, str, str, int]:
    items = _flagged(close, "fee_rate_increase")
    if not items or items[0].amount.paise <= 0:
        raise NothingToDraft("every fee in this period was charged at the contracted rate")
    finding = items[0]
    body = (
        f"Hello,\n\n"
        f"For {_period(start, end)}, fees were charged above our contracted rate. "
        f"The excess is {finding.amount}:\n\n{_by_method(finding)}\n\n"
        f"Please confirm the rate applied, and credit the difference against our next "
        f"settlement.\n\nThank you."
    )
    return (
        "Your payment gateway's account manager",
        f"Fees charged above contract — {finding.amount}",
        body,
        1,
    )


def _gst_followup(risks: list[Risk], start: date, end: date) -> tuple[str, str, str, int]:
    # Only the gateway's own invoices are the gateway's to fix; a vendor's bad GSTIN is not.
    items = [r for r in risks if r.amount.paise and r.kind == "gst_input_credit"]
    if not items:
        raise NothingToDraft("the gateway's invoices are all in your GSTR-2B, so there is nothing to ask it for")
    invoices = ", ".join(i for r in items for i in r.entity_ids) or "none yet"
    lines = "\n".join(f"  - GST on your fees not yet in our GSTR-2B: {r.amount}" for r in items)
    body = (
        f"Hello,\n\n"
        f"Our GSTR-2B for {_period(start, end)} does not show all the input tax credit we "
        f"paid on your fees:\n\n{lines}\n\nInvoices that do appear: {invoices}.\n\n"
        f"Please file or correct the invoices in your GSTR-1 so the credit reaches our "
        f"return before the claim window closes.\n\nThank you."
    )
    return ("Your payment gateway's GST / finance team", f"GST credit not reflected for {_period(start, end)}", body, len(items))


def _permitted(close: Close | None, risks: list[Risk]) -> list[Money]:
    found: list[Money] = [r.amount for r in risks]
    if close is not None:
        found += [f.amount for f in close.findings]
        found += [close.gap, close.residual, close.variance.gross_captured, close.variance.cash_landed]
        # Per-method or per-item figures the verifier itself wrote into its notes are verified too.
        found += [m for f in close.findings for _, m in extract_amounts(f.evidence.note)]
    return found


def _identifiers(close: Close | None) -> list[str]:
    if close is None:
        return []
    return [i for f in close.findings for i in f.evidence.entity_ids if len(i) > 6]


def draft(
    kind: str,
    start: date,
    end: date,
    close: Close | None = None,
    risks: list[Risk] | None = None,
    speaker: Speaker | None = None,
) -> Draft:
    if kind not in KINDS:
        raise ValueError(f"unknown draft kind {kind!r}; known: {', '.join(KINDS)}")
    risks = risks or []

    if kind == "gst_followup":
        to, subject, body, count = _gst_followup(risks, start, end)
    else:
        if close is None:
            raise NothingToDraft("there is no reconciliation to draft from")
        builder = {"escalate": _escalate, "payout_trace": _payout_trace, "fee_dispute": _fee_dispute}[kind]
        to, subject, body, count = builder(close, start, end)

    if speaker is None or not speaker.ready:
        return Draft(kind, to, subject, body, "template", "written from verified figures only", count)

    try:
        polished = speaker.say(POLISH, body, max_tokens=500).strip()
    except Exception as exc:  # noqa: BLE001 - a provider failure falls back, never loses the draft
        return Draft(kind, to, subject, body, "template", f"model unavailable ({exc or type(exc).__name__}); template used", count)

    known = _identifiers(close)
    gate = check(polished, _permitted(close, risks), identifiers=known)
    missing = [i for i in known if i in body and i not in polished]
    if not polished or not gate.ok or missing:
        why = gate.reason() if not gate.ok else (f"dropped {', '.join(missing)}" if missing else "empty reply")
        return Draft(kind, to, subject, body, "template", f"model draft withheld — {why}", count)

    return Draft(kind, to, subject, polished, speaker.describe(), gate.reason(), count)
