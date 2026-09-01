
from __future__ import annotations

from datetime import date

from residual.ledger import events as ev
from residual.simulate.world import (
    DisputeSpike,
    FeeHike,
    InstantPull,
    LostPayout,
    MerchantConfig,
    PartialSettlement,
    RiskHold,
)

BENCHMARK = MerchantConfig(
    seed=20260822,
    start=date(2026, 1, 5),
    days=90,
    scenarios=(
        FeeHike(day=31, method=ev.Method.CARD, new_rate="2.36"),
        RiskHold(day=47, amount="412000", release_after=11),
        LostPayout(day=59),
        DisputeSpike(day=66, count=7),
        InstantPull(day=72, amount="100000"),
        InstantPull(day=74, amount="100000"),
        PartialSettlement(day=80, share=0.5),
    ),
)
