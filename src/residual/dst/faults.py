
from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class Fault(StrEnum):

    DUPLICATE_BATCH = "duplicate_batch"

    REORDER = "reorder"

    TRUNCATE = "truncate"

    CRASH_AND_REPLAY = "crash_and_replay"

    DELAY = "delay"

    REPLAY_OLD = "replay_old"

    SPLIT = "split"


CONVERGING: frozenset[Fault] = frozenset(
    {
        Fault.DUPLICATE_BATCH,
        Fault.REORDER,
        Fault.CRASH_AND_REPLAY,
        Fault.DELAY,
        Fault.REPLAY_OLD,
        Fault.SPLIT,
    }
)


@dataclass(frozen=True, slots=True)
class Injection:

    fault: Fault
    batch: int
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.fault}@{self.batch}{f' ({self.detail})' if self.detail else ''}"


@dataclass(frozen=True, slots=True)
class Schedule:

    seed: int
    injections: tuple[Injection, ...] = ()

    @property
    def converges(self) -> bool:
        return all(i.fault in CONVERGING for i in self.injections)

    def without(self, index: int) -> Schedule:
        remaining = self.injections[:index] + self.injections[index + 1:]
        return Schedule(self.seed, remaining)

    def __str__(self) -> str:
        if not self.injections:
            return f"seed {self.seed}: no faults"
        return f"seed {self.seed}: " + ", ".join(str(i) for i in self.injections)


def plan(seed: int, batches: int, rate: float = 0.35) -> Schedule:
    rng = random.Random(seed)
    injections: list[Injection] = []
    for batch in range(batches):
        if rng.random() >= rate:
            continue
        fault = rng.choice(list(Fault))
        detail = ""
        if fault is Fault.TRUNCATE:
            detail = f"keep {rng.randint(0, 90)}%"
        elif fault is Fault.DELAY:
            detail = f"+{rng.randint(1, max(1, batches - batch - 1))} batches"
        elif fault is Fault.REPLAY_OLD:
            detail = f"batch {rng.randint(0, max(0, batch - 1))}"
        injections.append(Injection(fault, batch, detail))
    return Schedule(seed, tuple(injections))


def apply(
    batches: Sequence[Sequence[object]], schedule: Schedule
) -> list[tuple[int, list[object]]]:
    plan_: list[tuple[int, list[object]]] = [(i, list(b)) for i, b in enumerate(batches)]
    by_batch = {i.batch: i for i in schedule.injections}
    out: list[tuple[int, list[object]]] = []
    deferred: list[tuple[int, int, list[object]]] = []

    for position, (origin, events) in enumerate(plan_):
        injection = by_batch.get(origin)

        for due_at, src, held in list(deferred):
            if due_at <= position:
                out.append((src, held))
                deferred.remove((due_at, src, held))

        if injection is None:
            out.append((origin, events))
            continue

        match injection.fault:
            case Fault.DUPLICATE_BATCH:
                out.append((origin, events))
                out.append((origin, list(events)))
            case Fault.CRASH_AND_REPLAY:
                half = len(events) // 2
                out.append((origin, events[:half]))
                out.append((origin, list(events)))
            case Fault.REORDER:
                out.append((origin, list(reversed(events))))
            case Fault.SPLIT:
                half = len(events) // 2
                out.append((origin, events[:half]))
                out.append((origin, events[half:]))
            case Fault.TRUNCATE:
                keep = int(len(events) * int(injection.detail.split()[1].rstrip("%")) / 100)
                out.append((origin, events[:keep]))
            case Fault.DELAY:
                offset = int(injection.detail.lstrip("+").split()[0])
                deferred.append((position + offset, origin, events))
            case Fault.REPLAY_OLD:
                out.append((origin, events))
                source = int(injection.detail.split()[1])
                if source < len(plan_):
                    out.append((source, list(plan_[source][1])))

    for _, src, held in deferred:
        out.append((src, held))
    return out
