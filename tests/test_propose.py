from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from residual.domain.causes import Cause
from residual.explain.close import run_close
from residual.explain.propose import (
    Brief,
    LargestAccount,
    ModelProposer,
    Proposal,
    Recorded,
    Searcher,
    adjudicate,
    brief_for,
    rediscover,
    refined_by,
    without,
)
from residual.ledger.money import Money
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate

HELD_OUT = Cause.NORMAL_FEE


@dataclass
class _Block:
    type: str
    text: str = ""


@dataclass
class _Reply:
    content: list[_Block]
    stop_reason: str = "end_turn"


class _StubClient:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.sent: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> _Reply:
        self.sent.append(kwargs)
        body = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return _Reply(content=[_Block(type="text", text=body)])


@pytest.fixture(scope="module")
def books():
    r = simulate(BENCHMARK)
    events = r.log.events()
    wh = Warehouse.build(events)
    rates = {str(m): rate for m, rate in BENCHMARK.base_rates}
    start = r.start + timedelta(days=56)
    end = start + timedelta(days=6)
    return events, wh, rates, start, end


@pytest.fixture(scope="module")
def opened(books):
    events, wh, rates, start, end = books
    close = run_close(events, start, end, rates, wh, hypotheses=without(HELD_OUT, rates))
    return wh, close, start, end


def _sql_for(close):
    return f"SELECT {close.residual.paise}"


def test_removing_a_verifier_opens_the_residual_by_exactly_its_amount(books, opened):
    events, wh, rates, start, end = books
    _, held, _, _ = opened
    full = run_close(events, start, end, rates, wh)
    claimed = next(f.amount for f in full.findings if f.cause is HELD_OUT)
    assert held.residual.paise == claimed.paise
    assert full.residual.paise == 0


def test_a_correct_proposal_is_accepted(opened):
    wh, close, _, _ = opened
    verdict = adjudicate(
        wh, Proposal("normal_fee", "Gateway fees", ("fee_expense",), _sql_for(close)), close
    )
    assert verdict.accepted
    assert verdict.amount == close.residual


def test_the_right_number_on_the_wrong_account_is_rejected(opened):
    wh, close, _, _ = opened
    verdict = adjudicate(wh, Proposal("x", "x", ("refunds",), _sql_for(close)), close)
    assert not verdict.accepted
    assert any("do not balance" in f for f in verdict.faults)


def test_a_wrong_number_on_the_right_account_is_rejected(opened):
    wh, close, _, _ = opened
    verdict = adjudicate(wh, Proposal("x", "x", ("fee_expense",), "SELECT 1"), close)
    assert not verdict.accepted
    assert any("is unexplained" in f for f in verdict.faults)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE postings",
        "SELECT * FROM read_csv('/etc/passwd')",
        "ATTACH '/tmp/x.db' AS x",
        "SELECT 1; DROP TABLE postings",
    ],
)
def test_an_unsafe_proposal_is_refused_before_it_runs(opened, sql):
    wh, close, _, _ = opened
    verdict = adjudicate(wh, Proposal("x", "x", ("fee_expense",), sql), close)
    assert not verdict.accepted
    assert any("unsafe SQL" in f for f in verdict.faults)


def test_a_proposal_that_returns_a_table_is_rejected(opened):
    wh, close, _, _ = opened
    verdict = adjudicate(wh, Proposal("x", "x", ("fee_expense",), "SELECT 1, 2"), close)
    assert not verdict.accepted
    assert any("one row and one column" in f for f in verdict.faults)


def test_an_unknown_account_is_rejected(opened):
    wh, close, _, _ = opened
    verdict = adjudicate(wh, Proposal("x", "x", ("slush_fund",), _sql_for(close)), close)
    assert not verdict.accepted
    assert any("unknown account" in f for f in verdict.faults)


def test_broken_sql_is_a_rejection_not_a_crash(opened):
    wh, close, _, _ = opened
    verdict = adjudicate(wh, Proposal("x", "x", ("fee_expense",), "SELECT nope FROM postings"), close)
    assert not verdict.accepted
    assert any("did not run" in f for f in verdict.faults)


def test_the_proposer_is_never_told_the_amount_it_has_to_match(opened):
    wh, close, start, end = opened
    client = _StubClient({"name": "x", "title": "x", "accounts": ["fee_expense"], "sql": "SELECT 0"})
    ModelProposer(client=client).propose(wh, brief_for(close, start, end))

    sent = json.dumps(client.sent)
    assert str(close.residual.paise) not in sent
    assert str(abs(close.residual.paise)) not in sent
    assert "fee_expense" in sent


def test_a_model_proposal_is_parsed_and_then_judged_by_the_books(opened):
    wh, close, start, end = opened
    client = _StubClient(
        {
            "name": "gateway_fees",
            "title": "Gateway fees at the contracted rate",
            "accounts": ["fee_expense"],
            "sql": _sql_for(close),
            "rationale": "fee expense moved and nothing claims it",
        }
    )
    proposal = ModelProposer(client=client).propose(wh, brief_for(close, start, end))
    assert proposal is not None
    assert adjudicate(wh, proposal, close).accepted


def test_a_model_that_replies_with_junk_yields_no_proposal(opened):
    wh, close, start, end = opened
    proposal = ModelProposer(client=_StubClient("not json at all")).propose(
        wh, brief_for(close, start, end)
    )
    assert proposal is None
    assert not adjudicate(wh, proposal, close).accepted


def test_a_fenced_json_reply_is_still_read(opened):
    wh, close, start, end = opened
    fenced = "```json\n" + json.dumps(
        {"name": "x", "title": "x", "accounts": ["fee_expense"], "sql": _sql_for(close)}
    ) + "\n```"
    proposal = ModelProposer(client=_StubClient(fenced)).propose(wh, brief_for(close, start, end))
    assert proposal is not None
    assert adjudicate(wh, proposal, close).accepted


def test_the_naive_baseline_does_not_pass_by_accident(books):
    events, wh, rates, start, end = books
    tried = accepted = 0
    for cause in Cause:
        close, verdict = rediscover(events, start, end, rates, wh, cause, LargestAccount())
        if close.residual.paise == 0:
            continue
        tried += 1
        accepted += verdict.accepted
    assert tried >= 8
    assert accepted == 0


def test_holding_out_a_verifier_that_found_nothing_leaves_nothing_open(books):
    events, wh, rates, start, end = books
    close, verdict = rediscover(events, start, end, rates, wh, Cause.BANK_CHARGES, LargestAccount())
    assert close.residual.paise == 0
    assert not verdict.accepted
    assert verdict.faults == ("nothing was left open",)


RECORDED = Path(__file__).parent / "fixtures" / "proposals" / "week8.json"

SHARED_ACCOUNT_CAUSES = {
    Cause.NORMAL_FEE,
    Cause.FEE_RATE_INCREASE,
    Cause.CAPTURED_NOT_YET_SETTLED,
}


def _sweep(books, proposer_for):
    events, wh, rates, start, end = books
    results = {}
    for cause in Cause:
        close, verdict = rediscover(events, start, end, rates, wh, cause, proposer_for(cause))
        if close.residual.paise == 0:
            continue
        results[cause] = verdict
    return results


def test_recorded_proposals_rediscover_six_of_nine(books):
    proposer = Recorded(RECORDED)

    def pick(cause):
        proposer.cause = str(cause)
        return proposer

    results = _sweep(books, pick)
    assert len(results) == 9
    assert sum(v.accepted for v in results.values()) == 6


def test_every_rejection_is_a_shared_account_cause(books):
    proposer = Recorded(RECORDED)

    def pick(cause):
        proposer.cause = str(cause)
        return proposer

    results = _sweep(books, pick)
    missed = {cause for cause, verdict in results.items() if not verdict.accepted}
    assert missed == SHARED_ACCOUNT_CAUSES


def test_no_wrong_proposal_was_ever_accepted(books):
    proposer = Recorded(RECORDED)

    def pick(cause):
        proposer.cause = str(cause)
        return proposer

    for verdict in _sweep(books, pick).values():
        if verdict.accepted:
            assert verdict.amount is not None
            assert verdict.amount.paise != 0
        else:
            assert verdict.faults


def test_the_two_fee_causes_together_equal_what_the_proposal_returned(books):
    events, wh, rates, start, end = books
    full = run_close(events, start, end, rates, wh)
    normal = next(f.amount for f in full.findings if f.cause is Cause.NORMAL_FEE)
    increase = next(f.amount for f in full.findings if f.cause is Cause.FEE_RATE_INCREASE)
    assert (normal + increase).paise == 1824114


WEEKS = BENCHMARK.days // 7


def _quarter(books, proposer):
    events, wh, rates, _, _ = books
    r_start = events[0].occurred_at
    out = []
    for week in range(WEEKS):
        start = r_start + timedelta(days=week * 7)
        end = start + timedelta(days=6)
        for cause in Cause:
            close, verdict = rediscover(events, start, end, rates, wh, cause, proposer)
            if close.residual.paise == 0:
                continue
            out.append((week, cause, close, verdict))
    return out


def _excluded(verdict):
    return bool(verdict.faults) and "not independently rediscoverable" in verdict.faults[0]


def test_the_searcher_rediscovers_every_recoverable_cause_all_quarter(books):
    rows = _quarter(books, Searcher())
    attempted = [r for r in rows if not _excluded(r[3])]
    assert len(attempted) >= 80
    missed = [(w, str(c), v.reason()) for w, c, _, v in attempted if not v.accepted]
    assert missed == []


def test_the_naive_baseline_never_passes_all_quarter(books):
    rows = _quarter(books, LargestAccount())
    attempted = [r for r in rows if not _excluded(r[3])]
    assert sum(v.accepted for _, _, _, v in attempted) == 0


def test_a_cause_that_another_verifier_refines_is_excluded_not_failed(books):
    rows = _quarter(books, Searcher())
    excluded = {c for _, c, _, v in rows if _excluded(v)}
    assert excluded == {Cause.CAPTURED_NOT_YET_SETTLED}


def test_the_refinement_artifact_is_the_parent_minus_the_child(books):
    events, wh, rates, start, end = books
    full = run_close(events, start, end, rates, wh)
    held = run_close(
        events, start, end, rates, wh,
        hypotheses=without(Cause.CAPTURED_NOT_YET_SETTLED, rates),
    )
    parent = next(f.amount for f in full.findings if f.cause is Cause.CAPTURED_NOT_YET_SETTLED)
    assert held.residual == parent


def test_matching_candidates_never_disagree_about_the_account(books):
    events, wh, rates, _, _ = books
    searcher = Searcher()
    r_start = events[0].occurred_at
    seen = 0
    for week in range(WEEKS):
        start = r_start + timedelta(days=week * 7)
        end = start + timedelta(days=6)
        for cause in Cause:
            held = run_close(events, start, end, rates, wh, hypotheses=without(cause, rates))
            if held.residual.paise == 0 or refined_by(cause, rates):
                continue
            brief = brief_for(held, start, end, rates)
            hits = [
                key
                for key, sql in searcher.candidates(wh, brief).items()
                if (rows := wh.sql(sql)) and rows[0][0] == held.residual.paise
            ]
            assert hits, f"no candidate matched {cause} in week {week}"
            assert len({searcher.accounts_for(k) for k in hits}) == 1
            seen += 1
    assert seen >= 80


def test_the_searcher_abstains_when_nothing_matches(books):
    events, wh, rates, start, end = books
    held = run_close(events, start, end, rates, wh, hypotheses=without(Cause.NORMAL_FEE, rates))
    brief = brief_for(held, start, end, rates)
    impossible = Brief(
        start=brief.start,
        end=brief.end,
        accounts=brief.accounts,
        explained=brief.explained,
        unexplained=Money(999_999_999),
        contracted=brief.contracted,
    )
    assert Searcher().propose(wh, impossible) is None


def test_the_searcher_needs_the_contract_for_the_fee_causes(books):
    events, wh, rates, start, end = books
    for cause in (Cause.NORMAL_FEE, Cause.FEE_RATE_INCREASE):
        held = run_close(events, start, end, rates, wh, hypotheses=without(cause, rates))
        without_contract = brief_for(held, start, end, contracted=None)
        assert Searcher().propose(wh, without_contract) is None
        assert Searcher().propose(wh, brief_for(held, start, end, rates)) is not None
