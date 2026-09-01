
from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date

from residual.dst import faults
from residual.dst.faults import Schedule
from residual.ledger.events import EventBase
from residual.ledger.project import project
from residual.ledger.store import ChainBroken, EventLog
from residual.position.engine import (
    Balances,
    InvariantViolation,
    decompose,
    fold,
)
from residual.simulate.presets import BENCHMARK
from residual.simulate.world import MerchantConfig, simulate

COMPACT = dataclasses.replace(BENCHMARK, days=21, base_daily_orders=18, scenarios=())


@dataclass(frozen=True, slots=True)
class Violation:

    step: int
    origin: int
    invariant: str
    detail: str

    def __str__(self) -> str:
        return f"step {self.step} (batch {self.origin}): {self.invariant} -- {self.detail}"


@dataclass(slots=True)
class Run:
    schedule: Schedule
    deliveries: int = 0
    offered: int = 0
    recorded: int = 0
    rejected_as_duplicate: int = 0
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        verdict = "ok" if self.ok else f"{len(self.violations)} violation(s)"
        return (
            f"{self.schedule}\n"
            f"  {self.deliveries} deliveries, {self.offered} events offered, "
            f"{self.recorded} recorded, {self.rejected_as_duplicate} rejected as "
            f"duplicates -> {verdict}"
        )


def _batches(events: Sequence[EventBase], start: date) -> list[list[EventBase]]:
    by_day: dict[date, list[EventBase]] = {}
    for event in events:
        by_day.setdefault(event.recorded_at, []).append(event)
    return [by_day[day] for day in sorted(by_day)]


@dataclass(slots=True)
class _Checker:

    balances: Balances = field(default_factory=Balances)
    verified_to: int = 0

    def step(
        self, log: EventLog, new: Sequence[EventBase], step: int, origin: int,
        *, complete: bool, thorough: bool,
    ) -> Iterator[Violation]:
        for event in new:
            for posting in project(event).postings:
                self.balances[posting.account] = (
                    self.balances[posting.account] + posting.amount
                )

        try:
            log.verify_chain(since=self.verified_to)
        except ChainBroken as exc:
            yield Violation(step, origin, "hash chain", str(exc))
        self.verified_to = len(log)

        try:
            self.balances.check(complete=complete)
        except InvariantViolation as exc:
            yield Violation(step, origin, "balance", str(exc))

        digests = {record.digest for record in log}
        if len(digests) != len(log):
            yield Violation(step, origin, "idempotency", "the same fact is in the log twice")

        if [r.seq for r in log] != list(range(1, len(log) + 1)):
            yield Violation(step, origin, "sequence", "sequence numbers are not contiguous")

        if not thorough:
            return

        events = log.events()
        rebuilt = fold(events)
        if rebuilt != self.balances:
            yield Violation(
                step, origin, "determinism",
                "balances carried forward differ from balances re-derived from the log",
            )
        if events:
            span = decompose(
                events,
                min(e.occurred_at for e in events),
                max(e.occurred_at for e in events),
            )
            if not span.closes:
                yield Violation(step, origin, "identity", f"residual {span.residual}")


def run_one(
    seed: int,
    config: MerchantConfig | None = None,
    rate: float = 0.35,
    schedule: Schedule | None = None,
    thorough_every: int = 8,
) -> Run:
    world = simulate(dataclasses.replace(config or COMPACT, seed=seed))
    events = world.log.events()
    batches = _batches(events, world.start)
    schedule = schedule or faults.plan(seed, len(batches), rate)

    log = EventLog()
    run = Run(schedule=schedule)
    checker = _Checker()
    plan_ = faults.apply(batches, schedule)

    for step, (origin, delivered) in enumerate(plan_):
        run.deliveries += 1
        run.offered += len(delivered)
        result = log.ingest(delivered)  # type: ignore[arg-type]
        run.recorded += len(result.recorded)
        run.rejected_as_duplicate += len(result.duplicates)

        run.violations.extend(
            checker.step(
                log, [r.event for r in result.recorded], step, origin,
                complete=False, thorough=(step % thorough_every == 0),
            )
        )
        if run.violations:
            return run

    if schedule.converges:
        clean = EventLog()
        clean.extend(events)
        if fold(log.events()) != fold(clean.events()):
            run.violations.append(
                Violation(
                    run.deliveries, -1, "convergence",
                    "the books differ from a clean run despite only survivable faults",
                )
            )
        if {r.digest for r in log} != {r.digest for r in clean}:
            run.violations.append(
                Violation(
                    run.deliveries, -1, "convergence",
                    f"recorded {len(log)} facts against {len(clean)} in a clean run",
                )
            )
        run.violations.extend(
            checker.step(log, [], run.deliveries, -1, complete=True, thorough=True)
        )

    return run


@dataclass(slots=True)
class Sweep:
    runs: int = 0
    deliveries: int = 0
    events: int = 0
    duplicates_rejected: int = 0
    failures: list[Run] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        return (
            f"{self.runs:,} seeded runs, {self.deliveries:,} deliveries, "
            f"{self.events:,} events offered, "
            f"{self.duplicates_rejected:,} duplicate facts rejected"
        )


def sweep(
    seeds: int = 200,
    first: int = 1,
    config: MerchantConfig | None = None,
    rate: float = 0.35,
    stop_early: bool = True,
) -> Sweep:
    out = Sweep()
    for seed in range(first, first + seeds):
        run = run_one(seed, config, rate)
        out.runs += 1
        out.deliveries += run.deliveries
        out.events += run.offered
        out.duplicates_rejected += run.rejected_as_duplicate
        if not run.ok:
            out.failures.append(run)
            if stop_early:
                break
    return out


def shrink(run: Run, config: MerchantConfig | None = None, rate: float = 0.35) -> Schedule:
    schedule = run.schedule
    changed = True
    while changed and schedule.injections:
        changed = False
        for index in range(len(schedule.injections)):
            candidate = schedule.without(index)
            if not run_one(schedule.seed, config, rate, schedule=candidate).ok:
                schedule = candidate
                changed = True
                break
    return schedule
