from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from residual.explain.close import Close, Finding
from residual.explain.grounding import check
from residual.ledger.money import Money

SYSTEM = """\
You explain one line of a merchant's payment reconciliation to a small-business
owner who is not an accountant.

Rules you must not break:
- Use ONLY the amounts in the brief, written exactly as given (with the ₹ sign).
  Never compute, add, round or invent an amount. Quote references (UTRs, dates)
  only if they are in the brief.
- Three short sentences at most: what happened, whether it is normal, what to do.
- Plain English. Avoid jargon such as "T+2", "ledger", "residual" or "posting".
- No bullet points, no headings, no preamble, no sign-off.
- If it needs chasing, say who to contact and what to ask for."""

# What each line means and what to do, in words a merchant would use.
MEANING: dict[str, tuple[str, str]] = {
    "settlement_never_arrived": (
        "The gateway marked this money as paid out, but it never reached your bank account.",
        "Ask the gateway's settlements team to trace it with the UTR and confirm where it was sent.",
    ),
    "settlement_in_flight": (
        "The gateway has sent this payout and your bank has not shown it yet.",
        "It is still inside the normal settlement window; check again in a few working days.",
    ),
    "fee_rate_increase": (
        "You were charged more in fees than your contracted rate allows.",
        "Ask your gateway account manager to apply the agreed rate and refund the difference.",
    ),
    "normal_fee": (
        "These are the gateway's fees, charged at the rate you agreed.",
        "Nothing to do; it is the cost of taking payments.",
    ),
    "gst_on_fee": (
        "This is the 18% GST added to the gateway's fees.",
        "Claim it back as input tax credit once it shows in your GSTR-2B.",
    ),
    "tds_194o": (
        "The gateway withheld this as TDS under section 194-O.",
        "It is not lost: claim it against your income tax using Form 26AS.",
    ),
    "instant_settlement_fee": (
        "You paid extra to have these payouts settled instantly.",
        "Nothing to chase; switch instant settlement off if you do not need it.",
    ),
    "refunds_issued": (
        "This is money you refunded to customers.",
        "Nothing to chase, as long as each refund was one you meant to give.",
    ),
    "captured_not_yet_settled": (
        "These payments came in near the end of the period, so their payout lands after it.",
        "Nothing to do; it will arrive in the next few working days.",
    ),
    "bank_holiday_delay": (
        "These payouts were due on a weekend or bank holiday, so they arrive on the next working day.",
        "Nothing to do; they are late, not lost.",
    ),
    "risk_hold": (
        "The gateway is holding this money for a risk review.",
        "Ask the gateway's risk team what they need from you to release it.",
    ),
    "dispute_reserve_held": (
        "The gateway is holding this money while customer disputes are open.",
        "Send your evidence for each dispute; money from disputes you win is released.",
    ),
    "chargeback_lost": (
        "Customers won these disputes, so the money went back to them.",
        "Check whether any were winnable, and tighten delivery proof for the future.",
    ),
    "route_split": (
        "This was split to other linked accounts as part of each payment, so it was never yours to receive.",
        "Nothing to do, as long as the split matches your agreements.",
    ),
    "gateway_adjustment": (
        "The gateway took this out of a payout without a matching fee or refund.",
        "Ask the gateway what the adjustment was for and ask for it back if it was not agreed.",
    ),
    "bank_charges": (
        "Your bank charged this directly to your account.",
        "Nothing to chase with the gateway; check the bank's charge schedule if it looks high.",
    ),
    "other_bank_outflow": (
        "This is money that left your bank account for other things, such as rent or suppliers.",
        "Nothing to chase; it is counted only so the bank balance adds up.",
    ),
}


def rupees(amount: Money) -> str:
    text = str(amount)
    return text.replace("-INR ", "-₹").replace("INR ", "₹")


class Speaker(Protocol):

    @property
    def ready(self) -> bool: ...

    def describe(self) -> str: ...

    def say(self, system: str, prompt: str, max_tokens: int = 400) -> str: ...


@dataclass(frozen=True, slots=True)
class Explanation:
    cause: str
    text: str
    grounded: bool
    source: str
    reason: str

    @property
    def trustworthy(self) -> bool:
        return self.grounded


def fallback(finding: Finding, close: Close) -> str:
    what, todo = MEANING.get(str(finding.cause), (f"{finding.title}.", ""))
    share = ""
    if close.gap.paise and abs(finding.amount.paise) <= abs(close.gap.paise):
        pct = abs(finding.amount.paise) / abs(close.gap.paise) * 100
        share = f", {'under 1' if pct < 1 else f'about {pct:.0f}'}% of the {rupees(close.gap)} difference"
    opening = f"This accounts for {rupees(abs(finding.amount))}{share}. "
    refs = _refs(finding)
    return (opening + what + (f" {refs}" if refs else "") + (f" {todo}" if todo else "")).strip()


def _refs(finding: Finding) -> str:
    ids = [i for i in finding.evidence.entity_ids[:3] if i]
    if not ids:
        return ""
    cause = str(finding.cause)
    if cause in ("settlement_never_arrived", "settlement_in_flight"):
        return f"UTR {', '.join(ids)}."
    if cause == "bank_holiday_delay":
        return f"Due on {', '.join(ids)}."
    if cause == "fee_rate_increase":
        return f"Affects {', '.join(ids)} payments."
    return ""


def permitted_figures(close: Close) -> list[Money]:
    return [
        *(f.amount for f in close.findings),
        close.gap,
        close.residual,
        close.variance.gross_captured,
        close.variance.cash_landed,
    ]


def brief(finding: Finding, close: Close) -> str:
    what, todo = MEANING.get(str(finding.cause), (finding.title, ""))
    refs = ", ".join(i for i in finding.evidence.entity_ids[:5] if i)
    return (
        f"Line: {finding.title}\n"
        f"Amount: {rupees(abs(finding.amount))}\n"
        f"What it means: {what}\n"
        f"Detail from the check: {finding.evidence.note or 'none'}\n"
        + (f"References: {refs}\n" if refs else "")
        + f"Needs chasing: {'yes' if finding.alarming else 'no'}\n"
        f"Suggested next step: {todo}\n"
        f"The whole difference between money captured and money banked this period is {rupees(close.gap)}."
    )


def identifiers(finding: Finding) -> list[str]:
    return [i for i in finding.evidence.entity_ids if i]


def explain(finding: Finding, close: Close, chat: Speaker | None = None) -> Explanation:
    if chat is None or not chat.ready:
        return Explanation(
            str(finding.cause), fallback(finding, close), True, "offline",
            "written from the verifier's own output, so every figure is sourced",
        )

    try:
        text = chat.say(SYSTEM, brief(finding, close))
    except Exception as exc:  # noqa: BLE001 - a provider failure must not lose the answer
        return Explanation(
            str(finding.cause), fallback(finding, close), True, "offline",
            f"the model did not answer ({exc or type(exc).__name__}), so the written fallback was used",
        )

    if not text:
        return Explanation(
            str(finding.cause), fallback(finding, close), True, "offline",
            "the model returned nothing, so the written fallback was used",
        )

    gate = check(text, permitted_figures(close), identifiers=identifiers(finding))
    if not gate.ok:
        return Explanation(
            str(finding.cause), fallback(finding, close), True, "offline",
            f"withheld: {gate.reason()}",
        )
    return Explanation(str(finding.cause), text, True, chat.describe(), gate.reason())
