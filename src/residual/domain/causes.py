
from __future__ import annotations

from enum import StrEnum


class Cause(StrEnum):
    NORMAL_FEE = "normal_fee"
    GST_ON_FEE = "gst_on_fee"
    TDS_194O = "tds_194o"
    INSTANT_SETTLEMENT_FEE = "instant_settlement_fee"
    BANK_CHARGES = "bank_charges"
    GATEWAY_ADJUSTMENT = "gateway_adjustment"
    OTHER_BANK_OUTFLOW = "other_bank_outflow"

    FEE_RATE_INCREASE = "fee_rate_increase"

    REFUNDS_ISSUED = "refunds_issued"
    DISPUTE_RESERVE_HELD = "dispute_reserve_held"
    CHARGEBACK_LOST = "chargeback_lost"
    ROUTE_SPLIT = "route_split"

    CAPTURED_NOT_YET_SETTLED = "captured_not_yet_settled"
    SETTLEMENT_IN_FLIGHT = "settlement_in_flight"
    BANK_HOLIDAY_DELAY = "bank_holiday_delay"

    RISK_HOLD = "risk_hold"

    SETTLEMENT_NEVER_ARRIVED = "settlement_never_arrived"


PERMANENT: frozenset[Cause] = frozenset(
    {
        Cause.NORMAL_FEE, Cause.GST_ON_FEE, Cause.INSTANT_SETTLEMENT_FEE,
        Cause.BANK_CHARGES, Cause.GATEWAY_ADJUSTMENT, Cause.FEE_RATE_INCREASE,
        Cause.REFUNDS_ISSUED, Cause.CHARGEBACK_LOST, Cause.ROUTE_SPLIT,
        Cause.OTHER_BANK_OUTFLOW,
    }
)

ALARMING: frozenset[Cause] = frozenset(
    {Cause.SETTLEMENT_NEVER_ARRIVED, Cause.RISK_HOLD, Cause.FEE_RATE_INCREASE}
)
