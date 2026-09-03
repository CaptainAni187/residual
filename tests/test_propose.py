from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest

from residual.domain.causes import Cause
from residual.explain.close import run_close
from residual.explain.propose import (
    LargestAccount,
    ModelProposer,
    Proposal,
    adjudicate,
    brief_for,
    rediscover,
    without,
)
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
