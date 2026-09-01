
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest

from residual.explain.agent import ClaudeAgent, Session, write_memo
from residual.explain.close import run_close
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate


@dataclass
class _Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Reply:
    content: list[_Block]
    stop_reason: str


class _StubClient:

    def __init__(self, script: list[_Reply]) -> None:
        self.script = list(script)
        self.sent: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> _Reply:
        self.sent.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        if not self.script:
            raise AssertionError("the loop asked for more turns than the script has")
        return self.script.pop(0)


def _tool(name: str, **inp: Any) -> _Block:
    return _Block(type="tool_use", id=f"tu_{name}", name=name, input=inp)


@pytest.fixture(scope="module")
def setup():
    r = simulate(BENCHMARK)
    events = r.log.events()
    wh = Warehouse.build(events)
    contracted = {str(m): rate for m, rate in BENCHMARK.base_rates}
    start = r.start + timedelta(days=56)
    close = run_close(events, start, start + timedelta(days=6), contracted, wh)
    return wh, contracted, close


def _session(setup) -> Session:
    wh, contracted, close = setup
    return Session(wh, close.window[0], close.window[1], contracted, close.variance)


def test_the_loop_runs_tools_and_returns_the_memo(setup) -> None:
    *_, close = setup
    client = _StubClient([
        _Reply([_tool("list_hypotheses")], "tool_use"),
        _Reply([_tool("verify_hypothesis", cause="settlement_never_arrived"),
                _tool("verify_hypothesis", cause="fee_rate_increase")], "tool_use"),
        _Reply([_tool("summarise_gap")], "tool_use"),
        _Reply([_Block("text", text="A payout worth INR 1,08,107.64 never arrived.")], "end_turn"),
    ])
    session = _session(setup)
    text, model = ClaudeAgent(client=client).write(session, close)

    assert "1,08,107.64" in text
    assert session.calls == [
        "list_hypotheses",
        "verify_hypothesis(settlement_never_arrived)",
        "verify_hypothesis(fee_rate_increase)",
        "summarise_gap",
    ]
    assert model == ClaudeAgent().model


def test_tool_results_are_fed_back_in(setup) -> None:
    *_, close = setup
    client = _StubClient([
        _Reply([_tool("verify_hypothesis", cause="refunds_issued")], "tool_use"),
        _Reply([_Block("text", text="done")], "end_turn"),
    ])
    ClaudeAgent(client=client).write(_session(setup), close)

    last = client.sent[-1]["messages"][-1]
    assert last["role"] == "user"
    result = last["content"][0]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "tu_verify_hypothesis"
    assert "evidence_sql" in result["content"]


def test_a_hallucinated_tool_name_is_rejected(setup) -> None:
    _, _, close = setup
    client = _StubClient([_Reply([_tool("wire_money_to_me", amount="all")], "tool_use")])
    with pytest.raises(ValueError, match="does not exist"):
        ClaudeAgent(client=client).write(_session(setup), close)


def test_an_unknown_cause_comes_back_as_an_error_not_a_crash(setup) -> None:
    session = _session(setup)
    out = session.verify_hypothesis("the_dog_ate_it")
    assert "error" in out and out["known"]
    assert session.permitted() == [], "a failed verification must permit nothing"


def test_the_loop_gives_up_rather_than_spinning(setup) -> None:
    _, _, close = setup
    client = _StubClient([_Reply([_tool("summarise_gap")], "tool_use")] * 4)
    text, _ = ClaudeAgent(client=client, max_turns=3).write(_session(setup), close)
    assert text == "", "a run that never concluded must produce nothing to print"


def test_the_gate_applies_to_claude_exactly_as_it_does_offline(setup) -> None:
    wh, contracted, close = setup
    client = _StubClient([
        _Reply([_tool("verify_hypothesis", cause="normal_fee")], "tool_use"),
        _Reply([_Block("text", text="Fees came to roughly INR 9,99,999.00 this week.")],
               "end_turn"),
    ])
    memo = write_memo(close, wh, contracted, agent=ClaudeAgent(client=client))
    assert not memo.trustworthy
    assert "memo withheld" in memo.rendered()
    assert "Fees came to" not in memo.rendered()


def test_the_model_is_never_handed_a_figure_it_did_not_ask_for(setup) -> None:
    _, _, close = setup
    client = _StubClient([_Reply([_Block("text", text="ok")], "end_turn")])
    ClaudeAgent(client=client).write(_session(setup), close)

    first = client.sent[0]
    prompt = str(first["messages"]) + first["system"]
    for figure in (str(close.gap), str(close.variance.gross_captured)):
        assert figure not in prompt, "an amount leaked into the opening prompt"
