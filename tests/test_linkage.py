
from __future__ import annotations

from datetime import date, timedelta

import pytest

from residual.ledger import events as ev
from residual.ledger.money import Money
from residual.ledger.warehouse import Warehouse
from residual.recon.linkage import (
    ABSTAIN_AMBIGUOUS,
    AMOUNT_DATE_UNIQUE,
    EXACT_UTR,
    NORMALISED_UTR,
    TRUNCATED_PREFIX,
    link_credits,
)
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate

D = date(2026, 5, 4)


def _world(settlements, credits):
    out: list[ev.EventBase] = []
    for i, (utr, rupees, day) in enumerate(settlements):
        out.append(
            ev.SettlementExecuted(
                event_id=f"se{i}", occurred_at=day, recorded_at=day,
                settlement_id=f"setl_{i}", utr=utr, net=Money.parse(rupees), covers=(),
            )
        )
    for i, (narration, rupees, day) in enumerate(credits):
        out.append(
            ev.BankCreditReceived(
                event_id=f"bc{i}", occurred_at=day, recorded_at=day,
                bank_txn_id=f"btx_{i}", amount=Money.parse(rupees),
                narration=narration, value_date=day,
            )
        )
    return link_credits(Warehouse.build(out))


def test_exact_reference_in_narration() -> None:
    (link,) = _world(
        [("20260504123456", "50000", D)],
        [("NEFT CR-RATN0000088-RAZORPAY SOFTWARE PVT LTD-20260504123456", "50000", D)],
    )
    assert link.settlement_id == "setl_0"
    assert link.rule == EXACT_UTR


def test_reference_survives_separators_and_case() -> None:
    (link,) = _world(
        [("20260504123456", "50000", D)],
        [("IMPS/20260504-123456/RAZORPAY/SETTLEMENT", "50000", D)],
    )
    assert link.settlement_id == "setl_0"
    assert link.rule == NORMALISED_UTR


def test_truncated_reference_needs_the_amount_to_agree() -> None:
    (link,) = _world(
        [("20260504123456", "50000", D)],
        [("BY TRANSFER-NEFT*RATN0*2026050412", "50000", D)],
    )
    assert link.settlement_id == "setl_0"
    assert link.rule == TRUNCATED_PREFIX


def test_no_reference_but_only_one_candidate_fits() -> None:
    (link,) = _world(
        [("20260504123456", "50000", D)],
        [("IMPS IN RAZORPAY SOFTWARE PVT LTD", "50000", D)],
    )
    assert link.settlement_id == "setl_0"
    assert link.rule == AMOUNT_DATE_UNIQUE


def test_two_identical_credits_and_payouts_abstain_rather_than_guess() -> None:
    links = _world(
        [("20260504111111", "99800", D), ("20260506222222", "99800", D + timedelta(days=2))],
        [
            ("IMPS IN RAZORPAY SOFTWARE PVT LTD", "99800", D),
            ("MB-IMPS CR RAZORPAY SOFTWARE PVT LTD", "99800", D + timedelta(days=2)),
        ],
    )
    assert all(not link.linked for link in links)
    assert all(link.rule == ABSTAIN_AMBIGUOUS for link in links)
    assert all("not guessing" in link.reason for link in links)


def test_a_longer_reference_is_not_stolen_by_a_prefix() -> None:
    (link,) = _world(
        [("2026050412", "50000", D), ("20260504123456", "50000", D)],
        [("NEFT CR-RAZORPAY-20260504123456", "50000", D)],
    )
    assert link.settlement_id == "setl_1"


def test_a_credit_with_no_plausible_payout_is_not_forced() -> None:
    (link,) = _world(
        [("20260504123456", "50000", D)],
        [("NEFT CR-SOME OTHER PAYER", "17250", D)],
    )
    assert not link.linked


@pytest.fixture(scope="module")
def benchmark_links():
    r = simulate(BENCHMARK)
    return r, link_credits(Warehouse.build(r.log.events()))


def test_never_silently_wrong_on_the_benchmark(benchmark_links) -> None:
    r, links = benchmark_links
    wrong = [
        link
        for link in links
        if link.linked and r.truth.links.get(link.bank_txn_id) != link.settlement_id
    ]
    assert not wrong, [(link.bank_txn_id, link.rule, link.reason) for link in wrong]


def test_abstains_only_where_the_data_is_genuinely_ambiguous(benchmark_links) -> None:
    _, links = benchmark_links
    abstained = [link for link in links if not link.linked]
    assert len(abstained) == 2, [link.reason for link in abstained]
    assert all(link.rule == ABSTAIN_AMBIGUOUS for link in abstained)


def _pairs(n: int, per_day: int = 10) -> list[ev.EventBase]:
    from datetime import date as _date

    day_zero = _date(2026, 1, 1)
    out: list[ev.EventBase] = []
    for i in range(n):
        day = day_zero + timedelta(days=i // per_day)
        utr = f"{day:%Y%m%d}{i:06d}"
        amount = Money(100000 + i * 37)
        out.append(
            ev.SettlementExecuted(
                event_id=f"se{i}", occurred_at=day, recorded_at=day,
                settlement_id=f"setl_{i}", utr=utr, net=amount, covers=(),
            )
        )
        out.append(
            ev.BankCreditReceived(
                event_id=f"bc{i}", occurred_at=day, recorded_at=day,
                bank_txn_id=f"btx_{i}", amount=amount,
                narration=f"NEFT CR-RATN0000088-RAZORPAY-{utr}", value_date=day,
            )
        )
    return out


def test_linkage_does_not_grow_quadratically() -> None:
    from residual.recon import linkage as module

    work = {}
    for n in (500, 4000):
        warehouse = Warehouse.build(_pairs(n, per_day=10))
        links = link_credits(warehouse)
        assert all(link.linked for link in links), "correctness must not move"
        work[n] = module.last_work.per_credit

    assert work[4000] < work[500] * 1.2, (
        f"{work[500]:.1f} -> {work[4000]:.1f} comparisons per credit for 8x the history"
    )


def test_work_grows_with_daily_volume_not_with_history() -> None:
    from residual.recon import linkage as module

    link_credits(Warehouse.build(_pairs(2000, per_day=5)))
    sparse = module.last_work.per_credit
    link_credits(Warehouse.build(_pairs(2000, per_day=40)))
    dense = module.last_work.per_credit

    assert dense > sparse * 4, "density should drive the work"
    assert sparse < 60, f"{sparse:.0f} comparisons per credit at five payouts a day"


def test_a_large_run_still_never_matches_the_wrong_payout() -> None:
    warehouse = Warehouse.build(_pairs(4000))
    links = link_credits(warehouse)
    for link in links:
        assert link.settlement_id == link.bank_txn_id.replace("btx_", "setl_")
