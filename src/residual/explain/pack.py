
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from residual.explain.close import Close
from residual.ledger.money import Money
from residual.ledger.store import EventLog


class PackMismatch(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Pack:
    body: dict[str, Any]

    @property
    def digest(self) -> str:
        return self.body["digest"]

    @property
    def log_head(self) -> str:
        return self.body["source"]["log_head"]

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.body, indent=2, sort_keys=True) + "\n")
        return path

    @classmethod
    def read(cls, path: str | Path) -> Pack:
        return cls(json.loads(Path(path).read_text()))


def _money(m: Money) -> dict[str, Any]:
    return {"paise": m.paise, "currency": m.currency, "display": str(m)}


def build(close: Close, log: EventLog, memo: object | None = None) -> Pack:
    start, end = close.window
    body: dict[str, Any] = {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "as_of": close.as_of.isoformat() if close.as_of else None,
        "source": {"log_head": log.head, "events": len(log)},
        "totals": {
            "gross_captured": _money(close.variance.gross_captured),
            "cash_landed": _money(close.variance.cash_landed),
            "gap": _money(close.gap),
            "explained": _money(close.explained),
            "residual": _money(close.residual),
            "explained_fraction": round(close.explained_fraction, 6),
            "permanent_loss": _money(close.permanent_loss),
        },
        "hypotheses_checked": close.checked,
        "findings": [
            {
                "cause": str(f.cause),
                "title": f.title,
                "amount": _money(f.amount),
                "escalate": f.alarming,
                "permanent": f.permanent,
                "note": f.evidence.note,
                "entities": list(f.evidence.entity_ids),
                "evidence_sql": f.evidence.sql,
            }
            for f in close.findings
        ],
        "exceptions": [
            {
                "kind": u.kind,
                "detail": u.detail,
                "amount": _money(u.amount),
                "entities": list(u.entity_ids),
            }
            for u in close.unresolved
        ],
        "partition_proof": [
            {
                "account": str(c.account),
                "claimed": _money(c.claimed),
                "actual": _money(c.actual),
                "ok": c.ok,
            }
            for c in close.coverage
        ],
        "risks": [
            {
                "kind": r.kind,
                "title": r.title,
                "amount": _money(r.amount),
                "detail": r.detail,
                "action": r.action,
                "entities": list(r.entity_ids),
            }
            for r in close.risks
        ],
        "closed": close.closes,
        "fully_covered": close.fully_covered,
    }
    body["digest"] = _digest(body)

    if memo is not None:
        body["memo"] = {
            "text": getattr(memo, "text", ""),
            "model": getattr(memo, "model", "offline"),
            "grounded": getattr(getattr(memo, "grounding", None), "ok", False),
            "figures_checked": len(getattr(getattr(memo, "grounding", None), "citations", [])),
            "excluded_from_digest": True,
        }
    return Pack(body)


def _digest(body: dict[str, Any]) -> str:
    payload = {k: v for k, v in body.items() if k not in ("digest", "memo")}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify(pack: Pack, log: EventLog, events: list, contracted: dict[str, str]) -> None:
    from residual.explain.close import run_close

    if _digest(pack.body) != pack.digest:
        raise PackMismatch("the pack's own contents do not match its digest")

    log.verify_chain()
    if log.head != pack.log_head:
        raise PackMismatch(
            f"pack was computed against log head {pack.log_head[:16]}… "
            f"but these books head at {log.head[:16]}… -- events were added or altered"
        )

    start = date.fromisoformat(pack.body["window"]["start"])
    end = date.fromisoformat(pack.body["window"]["end"])
    as_of = pack.body["as_of"]
    fresh = build(
        run_close(
            events, start, end, contracted,
            known_by=date.fromisoformat(as_of) if as_of else None,
        ),
        log,
    )
    if fresh.digest != pack.digest:
        raise PackMismatch(
            "re-running this window over the same log produced a different result; "
            "the close is not reproducible"
        )
