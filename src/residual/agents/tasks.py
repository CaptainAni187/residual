from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from residual.agents.loop import BUDGET, Driver, Run, investigate
from residual.agents.tools import Toolbelt
from residual.domain.causes import Cause
from residual.explain.close import Close, run_close
from residual.explain.propose import Verdict, adjudicate, refined_by, without
from residual.ledger.events import EventBase
from residual.ledger.warehouse import Warehouse


@dataclass(frozen=True, slots=True)
class Investigation:
    cause: Cause
    close: Close
    run: Run
    verdict: Verdict

    @property
    def solved(self) -> bool:
        return self.verdict.accepted


def rediscover_with_agent(
    events: list[EventBase],
    start: date,
    end: date,
    contracted: dict[str, str],
    warehouse: Warehouse,
    cause: Cause,
    driver: Driver,
    budget: int = BUDGET,
) -> Investigation:
    close = run_close(
        events, start, end, contracted, warehouse, hypotheses=without(cause, contracted)
    )
    if close.residual.paise == 0:
        return Investigation(
            cause, close, Run(stopped="nothing open"),
            Verdict(proposal=None, target=close.residual, faults=("nothing was left open",)),
        )
    if refined_by(cause, contracted):
        names = ", ".join(str(c) for c in refined_by(cause, contracted))
        return Investigation(
            cause, close, Run(stopped="excluded"),
            Verdict(
                proposal=None, target=close.residual,
                faults=(
                    (
                        f"not independently rediscoverable: {names} refines it, so removing "
                        f"the parent leaves parent minus child, an arithmetic artifact"
                    ),
                ),
            ),
        )

    belt = Toolbelt(warehouse, close, start, end, contracted)
    run = investigate(belt, driver, budget=budget)
    return Investigation(cause, close, run, adjudicate(warehouse, run.proposal, close))
