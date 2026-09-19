from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest

from residual.agents.loop import FINISH, Analyst, ModelDriver, Run, investigate
from residual.agents.tasks import rediscover_with_agent
from residual.agents.tools import SPECS, Toolbelt, ToolError
from residual.domain.causes import Cause
from residual.explain.close import run_close
from residual.explain.propose import without
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate

HELD_OUT = Cause.NORMAL_FEE


@pytest.fixture(scope="module")
def books():
    result = simulate(BENCHMARK)
    events = result.log.events()
    warehouse = Warehouse.build(events)
    rates = {str(m): rate for m, rate in BENCHMARK.base_rates}
    start = result.start + timedelta(days=56)
    return result, events, warehouse, rates, start, start + timedelta(days=6)


@pytest.fixture
def belt(books):
    _, events, warehouse, rates, start, end = books
    close = run_close(events, start, end, rates, warehouse, hypotheses=without(HELD_OUT, rates))
    return Toolbelt(warehouse, close, start, end, rates)


@dataclass
class _Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] | None = None


@dataclass
class _Reply:
    content: list[_Block]
    stop_reason: str = "tool_use"


class _StubClient:
    def __init__(self, script: list[_Reply]) -> None:
        self.script = list(script)
        self.sent: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> _Reply:
        self.sent.append(kwargs)
        return self.script.pop(0) if self.script else _Reply(content=[_Block("text", "done")])


class _Mute:
    name = "mute"

    def act(self, belt: Toolbelt, run: Run) -> None:
        return None


class _Spinner:
    name = "spinner"

    def act(self, belt: Toolbelt, run: Run) -> tuple[str, dict[str, Any]]:
        return ("unbalanced_accounts", {})


def test_the_agent_solves_every_recoverable_cause_all_quarter(books):
    result, events, warehouse, rates, _, _ = books
    tried = solved = 0
    for week in range(BENCHMARK.days // 7):
        start = result.start + timedelta(days=week * 7)
        end = start + timedelta(days=6)
        for cause in Cause:
            found = rediscover_with_agent(
                events, start, end, rates, warehouse, cause, Analyst()
            )
            if found.close.residual.paise == 0 or found.run.stopped == "excluded":
                continue
            tried += 1
            solved += found.solved
    assert tried >= 80
    assert solved == tried


def test_the_agent_reaches_its_answer_through_tool_calls(books):
    _, events, warehouse, rates, start, end = books
    found = rediscover_with_agent(
        events, start, end, rates, warehouse, HELD_OUT, Analyst()
    )
    assert found.solved
    assert found.run.calls >= 2
    assert found.run.stopped == "proposed"
    assert found.run.steps[0].tool == "unbalanced_accounts"
    assert all(s.ok for s in found.run.steps)


def test_the_shortfall_is_something_the_agent_must_ask_for(books):
    _, events, warehouse, rates, start, end = books
    close = run_close(events, start, end, rates, warehouse, hypotheses=without(HELD_OUT, rates))
    belt = Toolbelt(warehouse, close, start, end, rates)

    reported = belt.unbalanced_accounts()
    assert reported["count"] >= 1
    assert any(a["short_by_paise"] == close.residual.paise for a in reported["accounts"])
    assert belt.calls == 0


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE postings",
        "SELECT * FROM read_csv('/etc/passwd')",
        "ATTACH '/tmp/x.db' AS x",
        "SELECT 1; DROP TABLE postings",
        "SELECT getenv('ANTHROPIC_API_KEY')",
    ],
)
def test_the_query_tool_refuses_unsafe_sql(belt, sql):
    with pytest.raises(ToolError, match="refused"):
        belt.run_query(sql)


def test_the_query_tool_reports_a_broken_query_instead_of_crashing(belt):
    with pytest.raises(ToolError, match="did not run"):
        belt.run_query("SELECT nope FROM postings")


def test_the_query_tool_caps_how_much_it_returns(belt):
    out = belt.run_query("SELECT seq FROM postings")
    assert len(out["rows"]) == 40
    assert out["truncated"] is True
    assert out["row_count"] > 40


def test_an_unknown_account_is_a_tool_error(belt):
    with pytest.raises(ToolError, match="no such account"):
        belt.account_movement("slush_fund")


def test_an_unknown_tool_is_refused(belt):
    with pytest.raises(ToolError, match="no such tool"):
        belt.call("rm_rf", {})


def test_pricing_at_contract_needs_a_contract(books):
    _, events, warehouse, rates, start, end = books
    close = run_close(events, start, end, rates, warehouse, hypotheses=without(HELD_OUT, rates))
    with pytest.raises(ToolError, match="no contract"):
        Toolbelt(warehouse, close, start, end, {}).fee_at_contracted_rate()


def test_a_driver_that_gives_up_proposes_nothing(belt):
    run = investigate(belt, _Mute())
    assert run.proposal is None
    assert run.stopped == "gave up"
    assert run.calls == 0


def test_the_budget_is_enforced(belt):
    run = investigate(belt, _Spinner(), budget=5)
    assert run.calls == 5
    assert run.stopped == "budget"
    assert run.proposal is None


def test_a_failed_tool_call_stays_in_the_transcript(belt):
    class _Bad:
        name = "bad"

        def act(self, b: Toolbelt, r: Run) -> tuple[str, dict[str, Any]]:
            return ("account_movement", {"account": "nope"})

    run = investigate(belt, _Bad(), budget=2)
    assert run.calls == 2
    assert not any(s.ok for s in run.steps)
    assert "no such account" in run.transcript()[0]["result"]


def test_the_model_driver_calls_tools_then_proposes(belt):
    client = _StubClient([
        _Reply([_Block("tool_use", id="a", name="unbalanced_accounts", input={})]),
        _Reply([_Block("tool_use", id="b", name="fee_at_contracted_rate", input={})]),
        _Reply([_Block("tool_use", id="c", name=FINISH.name, input={
            "name": "gateway_fee",
            "accounts": ["fee_expense"],
            "sql": f"SELECT {belt.close.residual.paise}",
        })]),
    ])
    run = investigate(belt, ModelDriver(client=client))
    assert [s.tool for s in run.steps] == ["unbalanced_accounts", "fee_at_contracted_rate"]
    assert run.proposal is not None
    assert run.proposal.accounts == ("fee_expense",)


def test_the_model_driver_is_offered_every_tool(belt):
    client = _StubClient([_Reply([_Block("text", "no")])])
    investigate(belt, ModelDriver(client=client), budget=1)
    offered = {t["name"] for t in client.sent[0]["tools"]}
    assert offered == {s.name for s in SPECS} | {FINISH.name}


def test_a_malformed_model_proposal_is_not_a_crash(belt):
    client = _StubClient([
        _Reply([_Block("tool_use", id="z", name=FINISH.name, input={"name": "x"})]),
    ])
    run = investigate(belt, ModelDriver(client=client))
    assert run.proposal is None
    assert run.stopped == "malformed proposal"


def test_the_model_driver_may_answer_in_plain_json(belt):
    body = json.dumps({
        "name": "gateway_fee",
        "accounts": ["fee_expense"],
        "sql": f"SELECT {belt.close.residual.paise}",
    })
    client = _StubClient([_Reply([_Block("text", "```json\n" + body + "\n```")])])
    run = investigate(belt, ModelDriver(client=client))
    assert run.proposal is not None
    assert run.proposal.name == "gateway_fee"


def _openai_reply(tool: str = "", args: dict[str, Any] | None = None, text: str = ""):
    if tool:
        return {"choices": [{"message": {
            "role": "assistant",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args or {})},
            }],
        }}]}
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class _Poster:
    def __init__(self, script):
        self.script = list(script)
        self.bodies = []

    def __call__(self, body):
        self.bodies.append(body)
        return self.script.pop(0) if self.script else _openai_reply(text="stop")


def test_an_openai_compatible_driver_calls_tools_then_proposes(belt):
    from residual.agents.loop import OpenAIDriver

    post = _Poster([
        _openai_reply("unbalanced_accounts", {}),
        _openai_reply("fee_at_contracted_rate", {}),
        _openai_reply(FINISH.name, {
            "name": "gateway_fee",
            "accounts": ["fee_expense"],
            "sql": f"SELECT {belt.close.residual.paise}",
        }),
    ])
    run = investigate(belt, OpenAIDriver(provider="groq", post=post))
    assert [s.tool for s in run.steps] == ["unbalanced_accounts", "fee_at_contracted_rate"]
    assert run.proposal is not None
    assert adjudicate_ok(belt, run)


def adjudicate_ok(belt, run):
    from residual.explain.propose import adjudicate

    return adjudicate(belt.wh, run.proposal, belt.close).accepted


def test_the_openai_driver_sends_every_tool_as_a_function(belt):
    from residual.agents.loop import OpenAIDriver

    post = _Poster([_openai_reply(text="nothing")])
    investigate(belt, OpenAIDriver(provider="gemini", post=post), budget=1)
    sent = post.bodies[0]
    assert {t["function"]["name"] for t in sent["tools"]} == {s.name for s in SPECS} | {FINISH.name}
    assert sent["messages"][0]["role"] == "system"


def test_the_call_id_never_reaches_the_toolbelt(belt):
    from residual.agents.loop import OpenAIDriver

    post = _Poster([_openai_reply("account_movement", {"account": "fee_expense"})])
    run = investigate(belt, OpenAIDriver(provider="groq", post=post), budget=1)
    assert run.steps[0].ok, run.steps[0].result
    assert run.steps[0].result["account"] == "fee_expense"


def test_every_provider_preset_is_complete():
    from residual.agents.loop import PROVIDERS

    assert {"groq", "gemini", "grok", "openrouter"} <= set(PROVIDERS)
    for name, spec in PROVIDERS.items():
        assert spec["base_url"].startswith("https://"), name
        assert spec["env"].endswith("_API_KEY"), name
        assert spec["model"], name


def test_an_unknown_provider_is_refused():
    from residual.agents.loop import OpenAIDriver

    with pytest.raises(ValueError, match="unknown provider"):
        OpenAIDriver(provider="definitely-not-a-provider")


def test_a_missing_key_says_which_variable_to_set(belt, monkeypatch):
    from residual.agents.loop import OpenAIDriver

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        OpenAIDriver(provider="groq").key()


def test_garbled_tool_arguments_do_not_crash_the_loop(belt):
    from residual.agents.loop import OpenAIDriver

    post = _Poster([{"choices": [{"message": {
        "role": "assistant",
        "tool_calls": [{"id": "x", "function": {"name": "account_movement", "arguments": "{oops"}}],
    }}]}])
    run = investigate(belt, OpenAIDriver(provider="groq", post=post))
    assert run.proposal is None
    assert run.stopped == "gave up"
