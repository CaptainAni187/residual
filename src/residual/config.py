
from __future__ import annotations

import os
from pathlib import Path

FILENAME = ".env"


def find(start: Path | None = None) -> Path | None:
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / FILENAME
        if candidate.is_file():
            return candidate
    return None


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


def load(path: Path | None = None, *, override: bool = False) -> list[str]:
    found = path or find()
    if found is None:
        return []
    try:
        text = found.read_text(encoding="utf-8")
    except OSError:
        return []

    applied = []
    for key, value in parse(text).items():
        if not value:
            continue
        if key in os.environ and not override:
            continue
        os.environ[key] = value
        applied.append(key)
    return applied
