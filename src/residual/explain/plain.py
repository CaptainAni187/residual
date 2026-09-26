from __future__ import annotations

from dataclasses import dataclass

from residual.agents.chat import Chat
from residual.explain.close import Close, Finding
from residual.explain.grounding import check
from residual.ledger.money import Money

SYSTEM = """\
You explain one line of a merchant's payment reconciliation to a finance team.

Rules you must not break:
- Use ONLY the figures given to you. Never compute, round, restate or invent a
  number. If a figure is not in the brief, do not write a number at all.
- Three sentences at most: what happened, whether it is normal, what to do.
- Plain English. No jargon, no bullet points, no preamble, no sign-off.
- If it needs escalating, say who should be chased and for what."""


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
    share = ""
    if close.gap.paise and abs(finding.amount.paise) <= abs(close.gap.paise):
        pct = abs(finding.amount.paise) / abs(close.gap.paise) * 100
        share = f", about {pct:.0f}% of the {close.gap} gap"
    opening = f"{finding.title} accounts for {finding.amount}{share}."
    middle = f" {finding.evidence.note.capitalize()}." if finding.evidence.note else ""
    tail = (
        " This one needs chasing rather than filing."
        if finding.alarming
        else " Nothing here needs action."
    )
    return opening + middle + tail


def permitted_figures(close: Close) -> list[Money]:
    return [
        *(f.amount for f in close.findings),
        close.gap,
        close.residual,
        close.variance.gross_captured,
        close.variance.cash_landed,
    ]


def brief(finding: Finding, close: Close) -> str:
    return (
        f"Cause: {finding.cause}\n"
        f"What the verifier calls it: {finding.title}\n"
        f"Amount: {finding.amount}\n"
        f"Basis: {finding.evidence.note or 'measured directly from the ledger'}\n"
        f"Needs escalation: {'yes' if finding.alarming else 'no'}\n"
        f"For context, the whole gap this period is {close.gap} and the "
        f"unexplained residual is {close.residual}."
    )


def explain(finding: Finding, close: Close, chat: Chat | None = None) -> Explanation:
    chat = chat or Chat()
    if not chat.ready:
        return Explanation(
            str(finding.cause), fallback(finding, close), True, "offline",
            "written from the verifier's own output, so every figure is sourced",
        )

    try:
        text = chat.say(SYSTEM, brief(finding, close))
    except Exception as exc:  # noqa: BLE001 - a provider failure must not lose the answer
        return Explanation(
            str(finding.cause), fallback(finding, close), True, "offline",
            f"{type(exc).__name__} from the model, so the written fallback was used",
        )

    if not text:
        return Explanation(
            str(finding.cause), fallback(finding, close), True, "offline",
            "the model returned nothing, so the written fallback was used",
        )

    gate = check(text, permitted_figures(close))
    if not gate.ok:
        return Explanation(
            str(finding.cause), fallback(finding, close), True, "offline",
            f"withheld: {gate.reason()}",
        )
    return Explanation(str(finding.cause), text, True, chat.describe(), gate.reason())
