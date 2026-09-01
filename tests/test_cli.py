
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from residual.cli import app

runner = CliRunner()
FIXTURE = str(Path(__file__).parent / "fixtures" / "recon_sample.json")


def _run(*args: str):
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output + str(result.exception)
    return result.output


def test_a_log_survives_the_round_trip(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    _run("simulate-world", "--out", str(path))
    assert path.exists()

    out = _run("verify-log", "--path", str(path))
    assert "chain verifies" in out
    assert "13/13 weekly closes tie out" in out
    assert "13/13 partition every account" in out


def test_a_tampered_log_is_refused(tmp_path) -> None:
    import json

    path = tmp_path / "events.jsonl"
    _run("simulate-world", "--out", str(path))
    lines = path.read_text().splitlines()
    row = json.loads(lines[10])
    row["event"]["event_id"] = "sneaky"
    lines[10] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n")

    result = runner.invoke(app, ["verify-log", "--path", str(path)])
    assert result.exit_code != 0


def test_real_recon_data_closes_to_zero() -> None:
    out = _run("ingest", "--file", FIXTURE, "--contract", "card=2.90,upi=2.36")
    assert "5 recon rows -> 6 ledger events" in out
    assert "INR 0.00" in out.split("residual")[1]


def test_fee_findings_are_disclaimed_without_a_contract() -> None:
    out = _run("ingest", "--file", FIXTURE)
    assert "No --contract given" in out

    with_contract = _run("ingest", "--file", FIXTURE, "--contract", "card=2.90,upi=2.36")
    assert "fee_rate_increase" not in with_contract
    assert "No --contract given" not in with_contract


def test_ingest_needs_a_source() -> None:
    assert runner.invoke(app, ["ingest"]).exit_code != 0


@pytest.mark.parametrize(
    "args",
    [
        ("close", "--week", "8"),
        ("evaluate",),
        ("ablate",),
        ("memo", "--week", "8"),
        ("restate-close", "--week", "2"),
        ("outlook", "--day", "70"),
        ("backtest-forecast", "--horizon", "14"),
        ("position", "--day", "89"),
        ("ask", "which settlements never arrived?"),
        ("ask", "fees by method"),
        ("bank-statement", "--file", str(Path(__file__).parent
                                         / "fixtures" / "statements" / "hdfc.csv")),
        ("bank-statement", "--file", str(Path(__file__).parent
                                         / "fixtures" / "statements" / "hdfc.pdf")),
        ("benchmark", "--days", "30", "--volume", "40"),
    ],
)
def test_every_command_runs_offline(args) -> None:
    assert _run(*args)


def test_the_pack_command_verifies_what_it_wrote(tmp_path) -> None:
    out = _run("pack", "--week", "8", "--out", str(tmp_path / "close.json"))
    assert "verified" in out and "reproduces this pack exactly" in out


def test_a_real_three_source_close_runs_end_to_end() -> None:
    base = Path(__file__).parent / "fixtures"
    out = _run(
        "reconcile",
        "--recon", str(base / "recon_march.json"),
        "--statement", str(base / "statements" / "hdfc.csv"),
        "--contract", "card=2.00",
        "--gstr2b", str(base / "gst" / "gstr2b_march.json"),
    )
    assert "every one agrees with the statement" in out
    assert "INR 0.00" in out.split("residual")[1]
    assert "At risk" in out


def test_a_pdf_statement_closes_the_same_way() -> None:
    base = Path(__file__).parent / "fixtures"
    out = _run(
        "reconcile",
        "--recon", str(base / "recon_march.json"),
        "--statement", str(base / "statements" / "hdfc.pdf"),
        "--contract", "card=2.00",
    )
    assert "word coordinates" in out
    assert "INR 0.00" in out.split("residual")[1]


def test_check_live_explains_itself_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    result = runner.invoke(app, ["check-live"])
    assert result.exit_code == 1
    assert "dashboard.razorpay.com" in result.output


def test_the_demo_walks_the_whole_argument() -> None:
    out = _run("demo", "--no-pause")
    for act in (
        "The question", "The proof", "What that is worth", "The refusal",
        "The two clocks", "The gate", "On real files", "The numbers",
    ):
        assert act in out, f"missing act: {act}"

    assert "residual" in out and "INR 0.00" in out
    assert "settlement(s) reported missing" in out
    assert "not guessing" in out
    assert "trace to a verified amount" in out
    assert "agrees with the statement" in out
    assert "hallucinated-cause rate" in out


def test_the_outlook_can_show_a_certified_floor() -> None:
    out = _run("outlook", "--history", "400", "--day", "300")
    assert "at least" in out
    assert "of headroom" in out


def test_the_default_outlook_says_how_to_see_one() -> None:
    out = _run("outlook", "--day", "70")
    assert "too little history" in out
    assert "--history" in out
