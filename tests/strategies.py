
from __future__ import annotations

from datetime import date, timedelta

from hypothesis import strategies as st

from residual.ledger import events as ev
from residual.ledger.money import Money

DAY0 = date(2026, 1, 1)

paise = st.integers(min_value=100, max_value=50_000_00)
methods = st.sampled_from(list(ev.Method))
day_offsets = st.integers(min_value=0, max_value=60)


@st.composite
def payment_bundle(draw, idx: int) -> list[ev.EventBase]:
    out: list[ev.EventBase] = []
    gross = Money(draw(paise))
    rate = draw(st.sampled_from(["1.75", "2.00", "2.36", "0.90"]))
    fee = gross.apply_rate(rate)
    tax = fee.apply_rate("18.00")
    tds = gross.apply_rate("0.10") if draw(st.booleans()) else Money(0)
    d0 = DAY0 + timedelta(days=draw(day_offsets))

    out.append(
        ev.PaymentCaptured(
            event_id=f"cap-{idx}",
            occurred_at=d0,
            recorded_at=d0,
            payment_id=f"pay_{idx}",
            order_id=f"ord_{idx}",
            gross=gross,
            method=draw(methods),
            fee=fee,
            tax=tax,
            tds=tds,
        )
    )

    remaining = gross - fee - tax - tds

    if remaining.paise > 200 and draw(st.booleans()):
        amt = Money(draw(st.integers(min_value=100, max_value=remaining.paise // 2)))
        d = d0 + timedelta(days=draw(st.integers(0, 10)))
        out.append(
            ev.RefundIssued(
                event_id=f"ref-{idx}", occurred_at=d, recorded_at=d,
                refund_id=f"rfnd_{idx}", payment_id=f"pay_{idx}", amount=amt,
            )
        )
        remaining = remaining - amt

    if remaining.paise > 200 and draw(st.integers(0, 9)) == 0:
        amt = Money(draw(st.integers(min_value=100, max_value=remaining.paise)))
        opened = d0 + timedelta(days=draw(st.integers(1, 5)))
        out.append(
            ev.DisputeOpened(
                event_id=f"dis-{idx}", occurred_at=opened,
                recorded_at=opened + timedelta(days=draw(st.integers(7, 21))),
                dispute_id=f"disp_{idx}", payment_id=f"pay_{idx}", amount=amt,
                reason_code=draw(st.sampled_from(["4853", "4855", "10.4"])),
            )
        )
        remaining = remaining - amt
        if draw(st.booleans()):
            r = opened + timedelta(days=draw(st.integers(22, 45)))
            won = draw(st.booleans())
            out.append(
                ev.DisputeResolved(
                    event_id=f"disr-{idx}", occurred_at=r, recorded_at=r,
                    dispute_id=f"disp_{idx}", payment_id=f"pay_{idx}",
                    amount=amt, won=won,
                )
            )
            if won:
                remaining = remaining + amt

    if remaining.paise > 200 and draw(st.integers(0, 11)) == 0:
        amt = Money(draw(st.integers(min_value=100, max_value=remaining.paise)))
        h = d0 + timedelta(days=draw(st.integers(0, 3)))
        out.append(
            ev.RiskHoldApplied(
                event_id=f"hold-{idx}", occurred_at=h, recorded_at=h,
                hold_id=f"hold_{idx}", amount=amt,
                reason=draw(st.sampled_from(list(ev.HoldReason))),
            )
        )
        remaining = remaining - amt

    if remaining.paise > 0:
        d = d0 + timedelta(days=draw(st.integers(2, 5)))
        instant = draw(st.integers(0, 7)) == 0
        ifee = remaining.apply_rate("0.20") if instant else Money(0)
        net = remaining - ifee
        if net.paise > 0:
            out.append(
                ev.SettlementExecuted(
                    event_id=f"setl-{idx}", occurred_at=d, recorded_at=d,
                    settlement_id=f"setl_{idx}", utr=f"{d:%Y%m%d}{idx:06d}",
                    net=net, covers=(f"pay_{idx}",),
                    instant=instant, instant_fee=ifee,
                )
            )
            if draw(st.integers(0, 4)):
                b = d + timedelta(days=draw(st.integers(0, 2)))
                out.append(
                    ev.BankCreditReceived(
                        event_id=f"bank-{idx}", occurred_at=b, recorded_at=b,
                        bank_txn_id=f"btx_{idx}", amount=net,
                        narration=f"NEFT CR-RAZORPAY SOFTWARE-{d:%Y%m%d}{idx:06d}",
                        value_date=b,
                    )
                )
    return out


@st.composite
def event_stream(draw, max_payments: int = 12) -> list[ev.EventBase]:
    n = draw(st.integers(min_value=1, max_value=max_payments))
    stream: list[ev.EventBase] = []
    for i in range(n):
        stream.extend(draw(payment_bundle(i)))

    for j in range(draw(st.integers(min_value=0, max_value=3))):
        d = DAY0 + timedelta(days=draw(day_offsets))
        stream.append(
            ev.BankChargeApplied(
                event_id=f"chg-{j}", occurred_at=d, recorded_at=d,
                bank_txn_id=f"btx_chg_{j}", amount=Money(draw(st.integers(1000, 500000))),
                narration="ACCT MAINT CHRG INCL GST",
            )
        )

    stream.sort(key=lambda e: (e.recorded_at, e.occurred_at, e.event_id))
    return stream
