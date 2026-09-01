
from __future__ import annotations

from datetime import timedelta

import pytest

from residual.eval.ablations import run_all
from residual.explain.agent import OfflineAgent, Session, write_memo
from residual.explain.close import run_close
from residual.explain.grounding import check, extract_amounts
from residual.ledger.money import Money
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate


@pytest.fixture(scope="module")
def setup():
    r = simulate(BENCHMARK)
    events = r.log.events()
    wh = Warehouse.build(events)
    contracted = {str(m): rate for m, rate in BENCHMARK.base_rates}
    return r, events, wh, contracted


@pytest.mark.parametrize(
    "text,expected",
    [
        ("INR 91,678.97", 9167897),
        ("₹1,23,456.78", 12345678),
        ("Rs. 500", 50000),
        ("a gap of -INR 6,633.52", -663352),
        ("12,34,567.89", 123456789),
    ],
)
def test_money_is_recognised_in_prose(text: str, expected: int) -> None:
    found = extract_amounts(text)
    assert found and found[0][1].paise == expected


def test_a_figure_with_no_source_is_caught() -> None:
    permitted = [Money.parse("91678.97"), Money.parse("658.77")]
    g = check(
        "A lost payout of INR 91,678.97 and roughly Rs 42,000 of unbilled interest.",
        permitted,
    )
    assert not g.ok
    assert [c.text for c in g.fabricated] == ["Rs 42,000"]


def test_sign_does_not_decide_grounding() -> None:
    g = check("released INR 4,12,000.00 back", [Money.parse("-412000")])
    assert g.ok


def test_the_model_starts_with_no_figures(setup) -> None:
    r, events, wh, contracted = setup
    start = r.start + timedelta(days=56)
    close = run_close(events, start, start + timedelta(days=6), contracted, wh)
    session = Session(wh, start, start + timedelta(days=6), contracted, close.variance)
    assert session.permitted() == []
    assert not check(f"The gap is {close.gap}.", session.permitted()).ok


def test_totals_require_asking_for_them(setup) -> None:
    r, events, wh, contracted = setup
    start = r.start + timedelta(days=56)
    close = run_close(events, start, start + timedelta(days=6), contracted, wh)
    session = Session(wh, start, start + timedelta(days=6), contracted, close.variance)
    session.verify_hypothesis("normal_fee")
    assert not check(f"The gap is {close.gap}.", session.permitted()).ok
    session.summarise_gap()
    assert check(f"The gap is {close.gap}.", session.permitted()).ok


def test_every_memo_in_the_run_is_grounded(setup) -> None:
    r, events, wh, contracted = setup
    for offset in range(0, BENCHMARK.days, 7):
        start = r.start + timedelta(days=offset)
        close = run_close(events, start, start + timedelta(days=6), contracted, wh)
        memo = write_memo(close, wh, contracted, agent=OfflineAgent())
        assert memo.trustworthy, f"{start}: {memo.grounding.reason()}"
        assert memo.grounding.citations, "a memo that quotes nothing proves nothing"


def test_an_ungrounded_memo_is_withheld_not_printed(setup) -> None:
    r, events, wh, contracted = setup
    start = r.start + timedelta(days=56)
    close = run_close(events, start, start + timedelta(days=6), contracted, wh)

    class Fabricator:
        def write(self, session, close):
            session.verify_hypothesis("normal_fee")
            return "Fees ran to about INR 7,77,777.00 this week.", "fabricator"

    memo = write_memo(close, wh, contracted, agent=Fabricator())
    assert not memo.trustworthy
    assert memo.text not in memo.rendered(), "the draft was printed anyway"
    assert "memo withheld" in memo.rendered()
    assert "no verified source" in memo.rendered()
    assert "Fees ran to" not in memo.rendered()


def test_ablations_still_favour_the_design(setup) -> None:
    r, events, _wh, contracted = setup
    results = {res.name.split(" vs")[0]: res for res in run_all(
        events, r.truth, r.start, BENCHMARK.days, contracted
    )}

    naive = results["linkage layer"]
    assert naive.ours.startswith("1 "), naive.ours
    assert int(naive.ablated.split()[0]) > 10, naive.ablated

    greedy = results["two-pass ambiguity detection"]
    assert greedy.ours.startswith("0 silently wrong")
    assert not greedy.ablated.startswith("0 silently wrong")

    gate = results["verifier-grounded memo"]
    assert gate.ours.startswith("0/"), gate.ours
