from __future__ import annotations

import sys
from pathlib import Path

import duckdb  # noqa: F401
import fastapi  # noqa: F401
import pydantic  # noqa: F401
import python_multipart  # noqa: F401
import starlette  # noqa: F401

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from residual.web.app import app

__all__ = ["app"]
