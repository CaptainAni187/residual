
from __future__ import annotations

import pytest

from residual.explain.close import _ensure_links
from residual.explain.qa import (
    CATALOGUE,
    Answer,
    UnsafeQuestion,
    ask,
    render,
    run,
    validate,
)
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate


@pytest.fixture(scope="module")
def wh():
    warehouse = Warehouse.build(simulate(BENCHMARK).log.events())
    _ensure_links(warehouse)
    return warehouse


@pytest.mark.parametrize(
    "attack",
    [
        "SELECT 1; DROP TABLE events",
        "DROP TABLE events",
        "INSERT INTO events VALUES (1)",
        "UPDATE postings SET amount_paise = 0",
        "DELETE FROM events",
        "COPY events TO '/tmp/leak.csv'",
        "ATTACH '/tmp/x.db' AS x; SELECT 1",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_parquet('s3://somewhere/x.parquet')",
        "SELECT * FROM glob('/**')",
        "SELECT * FROM duckdb_settings()",
        "SELECT getenv('ANTHROPIC_API_KEY')",
        "SELECT * FROM pg_tables",
        "CREATE TABLE evil AS SELECT * FROM events",
        "PRAGMA database_list",
    ],
)
def test_dangerous_sql_is_refused(attack: str) -> None:
    with pytest.raises(UnsafeQuestion):
        validate(attack)


@pytest.mark.parametrize(
    "evasion",
    [
        "SELECT /* comment */ * FROM READ_CSV('x')",
        "select * from Read_Csv('x')",
        "SELECT * FROM read_csv\n  ('x')",
        "-- harmless\nSELECT * FROM glob('/**')",
    ],
)
def test_comments_and_case_do_not_get_past_it(evasion: str) -> None:
    with pytest.raises(UnsafeQuestion):
        validate(evasion)


def test_a_refused_query_is_reported_not_raised(wh) -> None:
    answer = run(wh, "DROP TABLE events", question="drop everything")
    assert isinstance(answer, Answer) and not answer.ok
    assert "not a SELECT" in answer.refused


def test_legitimate_questions_are_allowed(wh) -> None:
    sql = (
        "SELECT method, sum(fee_paise) AS fee_paise FROM events "
        "WHERE type = 'payment_captured' GROUP BY method"
    )
    assert validate(sql)
    answer = run(wh, sql)
    assert answer.ok and answer.rows


def test_results_are_capped(wh) -> None:
    answer = run(wh, "SELECT event_id FROM events")
    assert 0 < len(answer.rows) <= 200


@pytest.mark.parametrize(
    "question",
    [
        "which settlements never arrived?",
        "show me the biggest settlements",
        "what did we pay in fees by method?",
        "list the refunds",
        "any chargebacks?",
        "which credits could you not match?",
        "what is our balance by account?",
        "which days were delayed by a holiday?",
    ],
)
def test_the_questions_a_controller_actually_asks(wh, question: str) -> None:
    answer = ask(wh, question)
    assert answer.ok, answer.refused
    assert answer.sql and answer.columns


def test_every_catalogue_query_runs(wh) -> None:
    for words, title, sql in CATALOGUE:
        answer = run(wh, sql, source=title)
        assert answer.ok, f"{title}: {answer.refused}"


def test_an_unknown_question_says_so(wh) -> None:
    answer = ask(wh, "what is the airspeed velocity of an unladen swallow")
    assert not answer.ok
    assert "needs the AI model" in answer.refused


def test_an_injection_in_the_question_is_not_executed(wh) -> None:
    answer = ask(wh, "balance by account; DROP TABLE events")
    assert answer.ok, "the catalogue answer should still work"
    assert "DROP" not in answer.sql.upper()
    assert wh.sql("SELECT count(*) FROM events")[0][0] > 0, "the table survived"


def test_money_is_formatted_by_column_not_by_size() -> None:
    assert render(214900, "fee_paise") == "₹2,149.00"
    assert render(2149, "captures") == "2149"
    assert render(2149, "amount_paise") == "₹21.49"


def test_the_query_always_travels_with_the_answer(wh) -> None:
    for words, _, _ in CATALOGUE:
        answer = ask(wh, words[0])
        if answer.ok:
            assert answer.sql.strip().upper().startswith(("SELECT", "WITH"))


@pytest.mark.parametrize("leak", ["PRAGMA database_list", "PRAGMA show_tables"])
def test_pragma_is_refused_even_though_duckdb_calls_it_a_select(leak: str) -> None:
    import duckdb

    assert duckdb.extract_statements(leak)[0].type == duckdb.StatementType.SELECT
    with pytest.raises(UnsafeQuestion, match="starts with SELECT"):
        validate(leak)


class _Speaker:
    def __init__(self, reply: str, ready: bool = True) -> None:
        self._reply, self._ready = reply, ready
        self.asked: list[str] = []

    @property
    def ready(self) -> bool:
        return self._ready

    def describe(self) -> str:
        return "stub-provider"

    def say(self, system: str, prompt: str, max_tokens: int = 400) -> str:
        self.asked.append(prompt)
        if self._reply == "boom":
            raise RuntimeError("provider unreachable")
        return self._reply


def test_a_question_outside_the_catalogue_reaches_the_model(wh):
    speaker = _Speaker("SELECT method, count(*) AS n FROM events GROUP BY method")
    answer = ask(wh, "break the payments down however you like", speaker=speaker)
    assert answer.ok
    assert answer.source == "stub-provider"
    assert answer.rows
    assert speaker.asked


def test_the_model_leads_when_one_is_configured(wh):
    speaker = _Speaker("SELECT method, count(*) AS n FROM events GROUP BY method")
    answer = ask(wh, "which settlements never arrived?", speaker=speaker)
    assert answer.source == "stub-provider"
    assert speaker.asked


def test_the_catalogue_catches_the_model_when_it_writes_nonsense(wh):
    speaker = _Speaker("SELECT * FROM table_that_is_not_there")
    answer = ask(wh, "which settlements never arrived?", speaker=speaker)
    assert answer.source.startswith("catalogue")
    assert answer.ok


def test_hostile_sql_from_the_model_is_refused(wh):
    answer = ask(wh, "summarise everything", speaker=_Speaker("DROP TABLE postings"))
    assert not answer.ok
    assert not answer.rows
    assert "couldn't" in answer.refused


def test_a_request_to_change_data_is_refused_before_any_model_sees_it(wh):
    speaker = _Speaker("SELECT 1")
    answer = ask(wh, "delete all my refunds", speaker=speaker)
    assert not answer.ok
    assert "only read" in answer.refused
    assert not speaker.asked


def test_an_empty_model_reply_is_refused(wh):
    answer = ask(wh, "something", speaker=_Speaker(""))
    assert not answer.ok
    assert "couldn't" in answer.refused


def test_a_provider_failure_is_reported_not_raised(wh):
    answer = ask(wh, "something", speaker=_Speaker("boom"))
    assert not answer.ok
    assert "could not be reached" in answer.refused


def test_a_speaker_that_is_not_ready_is_not_used(wh):
    speaker = _Speaker("SELECT 1", ready=False)
    answer = ask(wh, "something off catalogue entirely", speaker=speaker)
    assert not answer.ok
    assert not speaker.asked


@pytest.mark.parametrize(
    ("question", "limit"),
    [
        ("What were my five biggest payouts?", 5),
        ("top 3 largest settlements", 3),
        ("show me the 2 biggest", 2),
        ("biggest payouts", 10),
    ],
)
def test_the_catalogue_honours_how_many_were_asked_for(question, limit):
    from residual.explain.qa import from_catalogue

    _, sql = from_catalogue(question)
    assert sql.rstrip().endswith(f"LIMIT {limit}")


def test_asking_for_five_returns_at_most_five(wh):
    answer = ask(wh, "What were my five biggest payouts?")
    assert answer.ok
    assert len(answer.rows) <= 5


class _Sequence(_Speaker):
    def __init__(self, *replies: str) -> None:
        super().__init__("")
        self._replies = list(replies)

    def say(self, system: str, prompt: str, max_tokens: int = 400) -> str:
        self.asked.append(prompt)
        return self._replies.pop(0) if self._replies else ""


def test_sql_wrapped_in_prose_is_still_run(wh):
    speaker = _Speaker("Sure! Here it is:\n```sql\nSELECT count(*) AS refunds FROM events WHERE type = 'refund_issued'\n```")
    answer = ask(wh, "how many refunds?", speaker=speaker)
    assert answer.ok and answer.rows


def test_the_model_repairs_a_query_that_fails(wh):
    speaker = _Sequence(
        "SELECT no_such_column FROM events",
        "SELECT count(*) AS refunds FROM events WHERE type = 'refund_issued'",
    )
    answer = ask(wh, "how many refunds were there in total?", speaker=speaker)
    assert answer.ok and answer.rows
    assert "corrected" in answer.source
    assert "no_such_column" in speaker.asked[1], "the second turn should show the model its error"


def test_the_model_is_told_the_real_values_in_this_data(wh):
    speaker = _Speaker("SELECT 1 AS one")
    ask(wh, "card payments over 5000?", speaker=speaker)
    assert "Payment methods:" in speaker.asked[0]
    assert "card" in speaker.asked[0]
    assert "Dates run from" in speaker.asked[0]


def test_catalogue_words_match_whole_words_only():
    from residual.explain.qa import from_catalogue

    assert from_catalogue("what is the latest payout?") is None or "T+2" not in from_catalogue("what is the latest payout?")[0]
    assert from_catalogue("how much did I refund?") is not None


def test_columns_read_like_words_not_database_names():
    from residual.explain.qa import heading

    assert heading("amount_paise") == "Amount"
    assert heading("total_revenue_paise") == "Total revenue"
    assert heading("settled_on") == "Settled on"


def test_common_questions_are_answered_without_a_model(wh):
    for question in ("what is my average order value?", "total sales", "payments by day"):
        assert ask(wh, question).ok, question
