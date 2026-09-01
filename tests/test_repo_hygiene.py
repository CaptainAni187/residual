
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SECRETS = re.compile(
    r"""(
        rzp_live_[A-Za-z0-9]{6,}
      | rzp_test_[A-Za-z0-9]{10,}
      | sk-ant-[A-Za-z0-9-]{20,}
      | AKIA[0-9A-Z]{16}
      | -----BEGIN\s+[A-Z ]*PRIVATE\s+KEY-----
    )""",
    re.VERBOSE,
)
PLACEHOLDER = re.compile(r"x{6,}|abcdefghij|your[_-]?key|<[^>]+>", re.IGNORECASE)

def _publishable_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if listed.returncode != 0:
        pytest.skip("not a git repository")
    paths = [ROOT / line for line in listed.stdout.splitlines() if line]
    return [p for p in paths if p.is_file() and p.suffix not in {".pdf", ".whl", ".png"}]


def test_no_credential_would_be_published() -> None:
    offenders = []
    for path in _publishable_files():
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in SECRETS.finditer(text):
            found = match.group(0)
            if PLACEHOLDER.search(found) or path.name == "test_repo_hygiene.py":
                continue
            offenders.append(f"{path.relative_to(ROOT)}: {found[:16]}...")
    assert not offenders, offenders


def test_the_env_file_is_ignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=ROOT, capture_output=True, check=False
    )
    assert result.returncode == 0, ".env is not gitignored"


def test_the_example_env_carries_no_real_values() -> None:
    example = ROOT / ".env.example"
    assert example.exists()
    for line in example.read_text().splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        _, _, value = line.partition("=")
        value = value.strip()
        assert not value or PLACEHOLDER.search(value), f"real-looking value: {line}"


def test_a_licence_is_present_and_filled_in() -> None:
    licence = (ROOT / "LICENSE").read_text()
    assert "MIT License" in licence
    assert "<YOUR NAME>" not in licence, "the copyright holder is still a placeholder"


@pytest.mark.parametrize("path", ["README.md", ".gitignore", ".env.example", "pyproject.toml"])
def test_the_files_a_reviewer_looks_for_exist(path: str) -> None:
    assert (ROOT / path).exists()


def test_the_env_file_is_not_publishable() -> None:
    published = {p.name for p in _publishable_files()}
    assert ".env" not in published
    assert ".env.example" in published, "the template should ship"


def test_the_scanner_would_actually_catch_something(tmp_path) -> None:
    planted = "rzp_live_" + "A1b2C3d4E5"
    assert SECRETS.search(planted)
    assert not PLACEHOLDER.search(planted)
    assert not SECRETS.search("rzp_test_xxxxxxxxxxxx") or PLACEHOLDER.search(
        "rzp_test_xxxxxxxxxxxx"
    )
