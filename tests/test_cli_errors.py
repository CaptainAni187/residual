
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from residual.cli import app

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"
NOT_A_STATEMENT = str(Path(__file__).parent.parent / "pyproject.toml")


def _refuses(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code != 0, f"expected a refusal, got:\n{result.output}"
    text = result.output + str(result.exception or "")
    assert "Traceback" not in text, f"stack trace leaked:\n{text}"
    return text


@pytest.mark.parametrize(
    "args,expected",
    [
        (("benchmark", "--days", "0"), "at least 1"),
        (("benchmark", "--volume", "0"), "at least 1"),
        (("backtest-forecast", "--horizon", "0"), "at least 1"),
        (("outlook", "--horizon", "0"), "at least 1"),
        (("evaluate", "--window-days", "0"), "at least 1"),
        (("benchmark", "--days", "999999"), "at most"),
    ],
)
def test_out_of_range_options_are_explained(args, expected: str) -> None:
    assert expected in _refuses(*args)


@pytest.mark.parametrize(
    "args,expected",
    [
        (("bank-statement", "--file", "/nope.csv"), "no bank statement at"),
        (("verify-log", "--path", "/nope.jsonl"), "no event log at"),
        (("ingest", "--file", "/nope.json"), "no settlement recon report at"),
    ],
)
def test_a_missing_file_is_named_not_traced(args, expected: str) -> None:
    assert expected in _refuses(*args)


def test_a_file_that_is_not_json_says_what_it_found() -> None:
    text = _refuses("ingest", "--file", NOT_A_STATEMENT)
    assert "not valid JSON" in text
    assert "expected a settlement recon response" in text


def test_a_file_that_is_not_a_statement_says_so() -> None:
    assert "bank statement" in _refuses("bank-statement", "--file", NOT_A_STATEMENT)


def test_a_file_that_is_not_a_return_says_so() -> None:
    text = _refuses(
        "reconcile",
        "--recon", str(FIXTURES / "recon_march.json"),
        "--statement", str(FIXTURES / "statements" / "hdfc.csv"),
        "--gstr2b", NOT_A_STATEMENT,
    )
    assert "GSTR-2B" in text


@pytest.mark.parametrize("contract", ["garbage", "card", "card=abc", "card=2.00,upi"])
def test_a_malformed_contract_is_explained(contract: str) -> None:
    text = _refuses(
        "ingest", "--file", str(FIXTURES / "recon_march.json"), "--contract", contract
    )
    assert "rate" in text


def test_a_directory_is_not_mistaken_for_a_file(tmp_path) -> None:
    assert "is a directory" in _refuses("bank-statement", "--file", str(tmp_path))


@pytest.mark.parametrize(
    "args",
    [
        ("close", "--week", "999"),
        ("close", "--week", "-5"),
        ("position", "--day", "5000"),
        ("outlook", "--day", "900"),
        ("pack", "--week", "99"),
    ],
)
def test_windows_outside_the_run_are_empty_not_fatal(args) -> None:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output


def _shell(*args: str) -> tuple[int, str]:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "residual.cli", *args],
        capture_output=True, text=True, timeout=180, check=False,
    )
    return result.returncode, result.stdout + result.stderr


def test_the_installed_command_prints_a_message_not_a_stack_trace() -> None:
    code, out = _shell("bank-statement", "--file", "/nope.csv")
    assert code == 2
    assert "Traceback" not in out
    assert "no bank statement at /nope.csv" in out


def test_a_malformed_contract_through_the_real_entry_point() -> None:
    code, out = _shell(
        "ingest", "--file", str(FIXTURES / "recon_march.json"), "--contract", "card=abc"
    )
    assert code == 2
    assert "Traceback" not in out
    assert "not a percentage rate" in out


def test_a_working_command_still_exits_zero() -> None:
    code, out = _shell("close", "--week", "8")
    assert code == 0, out
    assert "residual" in out
