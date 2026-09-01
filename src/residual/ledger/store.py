
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import TypeAdapter

from residual.ledger.events import Event, EventBase

GENESIS = "0" * 64
_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


class ChainBroken(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Record:
    seq: int
    event: EventBase
    prev_hash: str
    hash: str

    digest: str = ""


@dataclass(frozen=True, slots=True)
class Ingestion:

    recorded: tuple[Record, ...] = ()
    duplicates: tuple[Record, ...] = ()

    @property
    def total(self) -> int:
        return len(self.recorded) + len(self.duplicates)

    @property
    def all_new(self) -> bool:
        return not self.duplicates

    @property
    def nothing_new(self) -> bool:
        return bool(self.duplicates) and not self.recorded

    def summary(self) -> str:
        if not self.total:
            return "nothing to record"
        if self.nothing_new:
            return f"all {self.total} events were already in the log; nothing changed"
        if self.all_new:
            return f"{len(self.recorded)} events recorded"
        return (
            f"{len(self.recorded)} events recorded, "
            f"{len(self.duplicates)} already present and skipped"
        )


def _canonical(event: EventBase) -> str:
    payload = event.model_dump(mode="json")
    payload.pop("seq", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def content_digest(event: EventBase) -> str:
    return hashlib.sha256(_canonical(event).encode()).hexdigest()


def _hash(prev: str, event: EventBase) -> str:
    return hashlib.sha256(f"{prev}{_canonical(event)}".encode()).hexdigest()


class EventLog:
    def __init__(self) -> None:
        self._records: list[Record] = []
        self._seen: dict[str, Record] = {}


    def append(self, event: EventBase) -> Record:
        digest = content_digest(event)
        if (existing := self._seen.get(digest)) is not None:
            return existing

        prev = self._records[-1].hash if self._records else GENESIS
        seq = len(self._records) + 1
        event = event.model_copy(update={"seq": seq})
        record = Record(
            seq=seq, event=event, prev_hash=prev,
            hash=_hash(prev, event), digest=digest,
        )
        self._records.append(record)
        self._seen[digest] = record
        return record

    def contains(self, event: EventBase) -> bool:
        return content_digest(event) in self._seen

    def ingest(self, events: Iterable[EventBase]) -> Ingestion:
        recorded: list[Record] = []
        duplicates: list[Record] = []
        for event in events:
            before = len(self._records)
            record = self.append(event)
            (recorded if len(self._records) > before else duplicates).append(record)
        return Ingestion(tuple(recorded), tuple(duplicates))

    def extend(self, events: Iterable[EventBase]) -> None:
        self.ingest(events)


    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[Record]:
        return iter(self._records)

    @property
    def head(self) -> str:
        return self._records[-1].hash if self._records else GENESIS

    def events(self) -> list[EventBase]:
        return [r.event for r in self._records]

    def as_of(self, occurred_by: date, known_by: date | None = None) -> list[EventBase]:
        known_by = known_by or date.max
        return [
            r.event
            for r in self._records
            if r.event.occurred_at <= occurred_by and r.event.recorded_at <= known_by
        ]


    def verify_chain(self, since: int = 0) -> None:
        if since:
            prev = self._records[since - 1].hash if since <= len(self._records) else GENESIS
            records = self._records[since:]
        else:
            prev, records = GENESIS, self._records
        for r in records:
            if r.prev_hash != prev:
                raise ChainBroken(f"seq {r.seq}: prev_hash does not match seq {r.seq - 1}")
            expected = _hash(prev, r.event)
            if r.hash != expected:
                raise ChainBroken(f"seq {r.seq}: content hash mismatch (event was altered)")
            prev = r.hash


    def write_jsonl(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            for r in self._records:
                fh.write(
                    json.dumps(
                        {
                            "seq": r.seq,
                            "prev_hash": r.prev_hash,
                            "hash": r.hash,
                            "event": r.event.model_dump(mode="json"),
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        return path

    @classmethod
    def read_jsonl(cls, path: str | Path) -> EventLog:
        log = cls()
        with Path(path).open() as fh:
            for line in fh:
                row = json.loads(line)
                event = _ADAPTER.validate_python(row["event"])
                record = Record(
                    seq=row["seq"],
                    event=event,
                    prev_hash=row["prev_hash"],
                    hash=row["hash"],
                    digest=content_digest(event),
                )
                log._records.append(record)
                log._seen[record.digest] = record
        log.verify_chain()
        return log
