from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from residual.explain.close import run_close
from residual.explain.insights import analyse
from residual.ledger.warehouse import Warehouse
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import simulate

SEEDS = (20260822, 1002582, 2325324, 4242, 6978961, 7571439, 8245463, 9362092)
OUT = Path(__file__).resolve().parents[3] / "lib" / "sizing.json"


def measure() -> dict[str, float | int]:
    captured = recoverable = chase = tax = 0
    for seed in SEEDS:
        world = simulate(dataclasses.replace(BENCHMARK, seed=seed))
        events = world.log.events()
        first = min(e.occurred_at for e in events)
        last = max(e.occurred_at for e in events)
        rates = {str(m): r for m, r in BENCHMARK.base_rates}
        close = run_close(events, first, last, rates, Warehouse.build(events))
        found = analyse(close, first, last)
        captured += close.variance.gross_captured.paise
        recoverable += found.recoverable_paise
        chase += max(found.totals["chase"], 0)
        tax += max(found.totals["tax"], 0)
    n = len(SEEDS)
    return {
        "merchants": n,
        "days": BENCHMARK.days,
        "avg_captured_paise": captured // n,
        "avg_recoverable_paise": recoverable // n,
        "avg_chase_paise": chase // n,
        "avg_tax_paise": tax // n,
        "recoverable_share_of_captured": round(recoverable / captured, 4),
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(measure(), indent=2) + "\n")
    print(OUT.read_text())
