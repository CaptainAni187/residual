from __future__ import annotations

import ast
import subprocess
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pytest

import residual

SRC = Path(__file__).resolve().parents[1] / "src" / "residual"

CORE = {"domain", "ledger", "recon", "position", "explain", "ingest"}
HARNESS = {"simulate", "dst", "eval"}
SURFACE = {"web"}


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
    return {m.split(".")[1] for m in out if m.startswith("residual.") and m.count(".") >= 1}


def _package_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for path in SRC.rglob("*.py"):
        pkg = path.relative_to(SRC).parts[0].removesuffix(".py")
        graph[pkg] |= _imports(path) - {pkg}
    return graph


@pytest.mark.parametrize("name", residual.__all__)
def test_every_advertised_export_resolves(name: str) -> None:
    assert getattr(residual, name) is not None


def test_the_export_list_matches_what_dir_reports() -> None:
    assert dir(residual) == sorted(residual.__all__)


def test_the_export_list_covers_every_lazy_target() -> None:
    assert residual.__all__ == sorted(residual._EXPORTS)


def test_an_unknown_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        _ = residual.no_such_thing


def test_importing_the_package_does_not_load_the_heavy_dependencies() -> None:
    code = "import residual, sys; print(int(any(m in sys.modules for m in ('duckdb','polars','pdfplumber'))))"
    loaded = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert loaded.stdout.strip() == "0"


def test_core_never_imports_the_harness_or_a_surface() -> None:
    graph = _package_graph()
    leaks = {pkg: deps & (HARNESS | SURFACE) for pkg, deps in graph.items() if pkg in CORE}
    assert not {k: v for k, v in leaks.items() if v}


def test_the_package_graph_is_acyclic() -> None:
    graph = _package_graph()
    state: dict[str, int] = {}

    def walk(node: str, trail: list[str]) -> None:
        if state.get(node) == 1:
            raise AssertionError(f"cycle: {' -> '.join([*trail, node])}")
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in sorted(graph.get(node, ())):
            walk(dep, [*trail, node])
        state[node] = 2

    for pkg in sorted(graph):
        walk(pkg, [])


@pytest.fixture(scope="module")
def world():
    from residual.simulate.presets import BENCHMARK
    from residual.simulate.world import simulate

    result = simulate(BENCHMARK)
    events = result.log.events()
    return (
        result,
        events,
        residual.Warehouse.build(events),
        {str(m): rate for m, rate in BENCHMARK.base_rates},
    )


def test_a_caller_can_close_a_week_through_the_public_api_alone(world) -> None:
    result, events, warehouse, contracted = world
    start = result.start + timedelta(days=56)

    close = residual.run_close(events, start, start + timedelta(days=6), contracted, warehouse)

    assert close.closes
    assert close.residual == residual.Money.zero()
    assert close.variance.gross_captured.paise > 0
    assert residual.write_memo(close, warehouse, contracted).trustworthy
    assert residual.ask(warehouse, "which settlements never arrived?").ok
