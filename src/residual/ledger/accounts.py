
from __future__ import annotations

from enum import StrEnum


class Side(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class Account(StrEnum):
    BANK = "bank"
    GATEWAY_RECEIVABLE = "gateway_receivable"
    ON_HOLD = "on_hold"
    SETTLEMENT_IN_TRANSIT = "settlement_in_transit"
    DISPUTE_RESERVE = "dispute_reserve"
    TDS_RECEIVABLE = "tds_receivable"
    GST_INPUT_CREDIT = "gst_input_credit"

    REVENUE = "revenue"

    FEE_EXPENSE = "fee_expense"
    BANK_CHARGES = "bank_charges"
    GATEWAY_ADJUSTMENTS = "gateway_adjustments"
    OTHER_OUTFLOW = "other_outflow"
    REFUNDS = "refunds"
    CHARGEBACK_LOSS = "chargeback_loss"


NORMAL_BALANCE: dict[Account, Side] = {
    Account.BANK: Side.DEBIT,
    Account.GATEWAY_RECEIVABLE: Side.DEBIT,
    Account.ON_HOLD: Side.DEBIT,
    Account.SETTLEMENT_IN_TRANSIT: Side.DEBIT,
    Account.DISPUTE_RESERVE: Side.DEBIT,
    Account.TDS_RECEIVABLE: Side.DEBIT,
    Account.GST_INPUT_CREDIT: Side.DEBIT,
    Account.REVENUE: Side.CREDIT,
    Account.FEE_EXPENSE: Side.DEBIT,
    Account.BANK_CHARGES: Side.DEBIT,
    Account.GATEWAY_ADJUSTMENTS: Side.DEBIT,
    Account.OTHER_OUTFLOW: Side.DEBIT,
    Account.REFUNDS: Side.DEBIT,
    Account.CHARGEBACK_LOSS: Side.DEBIT,
}

NEVER_NEGATIVE: frozenset[Account] = frozenset(
    {Account.DISPUTE_RESERVE, Account.ON_HOLD, Account.GATEWAY_RECEIVABLE}
)

CASH_ACCOUNTS: frozenset[Account] = frozenset({Account.BANK})
