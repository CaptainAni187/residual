
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
    assert "no catalogue entry matches" in answer.refused


def test_an_injection_in_the_question_is_not_executed(wh) -> None:
    answer = ask(wh, "balance by account; DROP TABLE events")
    assert answer.ok, "the catalogue answer should still work"
    assert "DROP" not in answer.sql.upper()
    assert wh.sql("SELECT count(*) FROM events")[0][0] > 0, "the table survived"


def test_money_is_formatted_by_column_not_by_size() -> None:
    assert render(214900, "fee_paise") == "INR 2,149.00"
    assert render(2149, "captures") == "2149"
    assert render(2149, "amount_paise") == "INR 21.49"


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
