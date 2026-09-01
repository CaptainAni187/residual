
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum

from residual.domain.text import normalise

COUNTERPARTY = [
    "RAZORPAY SOFTWARE PVT LTD",
    "RAZORPAY SOFTWARE PRIVATE LIMITED",
    "RAZORPAYSOFTWAR",
    "Razorpay Software Pvt. Ltd.",
]
IFSC = ["RATN0000088", "HDFC0000060", "ICIC0000104"]


class Style(StrEnum):

    CLEAN = "clean"
    CASED = "cased"
    TRUNCATED = "truncated"
    ABSENT = "absent"
    NOISY = "noisy"


@dataclass(frozen=True, slots=True)
class Narration:
    text: str
    style: Style
    utr: str


_TEMPLATES: list[tuple[str, int]] = [
    ("NEFT CR-{ifsc}-{party}-{utr}", 100),
    ("IMPS/{utr}/{party}/SETTLEMENT", 90),
    ("BY TRANSFER-NEFT*{ifsc5}*{utr}*{party}--", 34),
    ("RTGS CR {party} REF {utr}", 100),
    ("{utr} {party} NEFT CR", 100),
    ("NEFT CR-{ifsc}-{party}", 100),
    ("SETTLEMENT CREDIT {party} {utr}", 31),
]


def classify(text: str, utr: str) -> Style:
    if utr in text:
        tail = text.split(utr, 1)[1]
        return Style.NOISY if tail[:1].isdigit() else Style.CLEAN
    if utr in normalise(text):
        return Style.CASED
    norm = normalise(text)
    if any(utr[:n] in norm for n in (10, 8, 6)):
        return Style.TRUNCATED
    return Style.ABSENT


_IMPS_TERSE = [
    "IMPS IN {party}",
    "MB-IMPS CR {party}",
    "IMPS CR {party} SETTLEMENT",
]


def make_narration(
    rng: random.Random, utr: str, *, mess: float = 0.55, terse: bool = False
) -> Narration:
    party = rng.choice(COUNTERPARTY)
    if terse:
        text = rng.choice(_IMPS_TERSE).replace("{party}", party)[:31]
        return Narration(text, classify(text, utr), utr)
    ifsc = rng.choice(IFSC)

    if rng.random() >= mess:
        text = f"NEFT CR-{ifsc}-{party}-{utr}"
        return Narration(text, classify(text, utr), utr)

    template, width = rng.choice(_TEMPLATES)
    shown = utr
    if "{utr}" in template:
        roll = rng.random()
        if roll < 0.30:
            shown = utr.upper() if rng.random() < 0.5 else f"{utr[:8]}-{utr[8:]}"
        elif roll < 0.45:
            shown = f"{utr}{rng.randrange(100):02d}"

    text = (
        template.replace("{utr}", shown)
        .replace("{party}", party)
        .replace("{ifsc}", ifsc)
        .replace("{ifsc5}", ifsc[:5])
    )[:width]
    return Narration(text, classify(text, utr), utr)
