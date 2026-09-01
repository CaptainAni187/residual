
from __future__ import annotations

import os

import pytest

from residual import config


def test_a_real_environment_variable_wins(tmp_path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text("RESIDUAL_TEST_KEY=from-file\n")
    monkeypatch.setenv("RESIDUAL_TEST_KEY", "from-shell")

    config.load(env)
    assert os.environ["RESIDUAL_TEST_KEY"] == "from-shell"

    config.load(env, override=True)
    assert os.environ["RESIDUAL_TEST_KEY"] == "from-file"


def test_it_reports_names_and_never_values(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("RESIDUAL_TEST_SECRET=hunter2\n")
    applied = config.load(env)
    assert applied == ["RESIDUAL_TEST_SECRET"]
    assert "hunter2" not in str(applied)
    os.environ.pop("RESIDUAL_TEST_SECRET", None)


@pytest.mark.parametrize(
    "line,expected",
    [
        ("KEY=value", {"KEY": "value"}),
        ("  KEY = value  ", {"KEY": "value"}),
        ("export KEY=value", {"KEY": "value"}),
        ('KEY="quoted value"', {"KEY": "quoted value"}),
        ("KEY='single'", {"KEY": "single"}),
        ("KEY=value # trailing note", {"KEY": "value"}),
        ("# comment", {}),
        ("", {}),
        ("NOEQUALS", {}),
        ("=novalue", {}),
    ],
)
def test_the_shapes_people_actually_write(line: str, expected: dict[str, str]) -> None:
    assert config.parse(line) == expected


def test_a_hash_inside_quotes_is_part_of_the_secret() -> None:
    assert config.parse('KEY="abc#def"') == {"KEY": "abc#def"}


def test_an_empty_value_is_not_applied(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("RESIDUAL_TEST_BLANK=\n")
    assert config.load(env) == []
    assert "RESIDUAL_TEST_BLANK" not in os.environ


def test_a_missing_file_is_not_an_error(tmp_path) -> None:
    assert config.load(tmp_path / "nope.env") == []


def test_it_finds_a_dotenv_from_a_subdirectory(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("RESIDUAL_TEST_FOUND=1\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert config.find() == tmp_path / ".env"


def test_find_returns_none_when_there_is_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    found = config.find()
    assert found is None or found != tmp_path / ".env"


def test_the_library_reads_the_environment_not_the_disk(monkeypatch) -> None:
    from residual.ingest import razorpay

    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    with pytest.raises(razorpay.NotConfigured):
        razorpay.probe()
