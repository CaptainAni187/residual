from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from residual.explain.close import Close, run_close
from residual.explain.hypotheses import UnknownMethod
from residual.ingest import bank
from residual.ingest.razorpay import UnsupportedRow, payments_to_events, to_events
from residual.ledger.events import EventBase
from residual.ledger.warehouse import Warehouse

MAX_UPLOAD = 4 * 1024 * 1024
MAX_SESSIONS = 32
SESSION_TTL = 45 * 60

router = APIRouter(prefix="/api")


@dataclass
class Session:
    events: list[EventBase]
    contracted: dict[str, str]
    start: date
    end: date
    source: str
    warehouse: Warehouse = field(init=False)
    born: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.warehouse = Warehouse.build(self.events)

    def close(self) -> Close:
        return run_close(self.events, self.start, self.end, self.contracted, self.warehouse)


_SESSIONS: dict[str, Session] = {}


def _reap() -> None:
    now = time.monotonic()
    for key in [k for k, s in _SESSIONS.items() if now - s.born > SESSION_TTL]:
        _SESSIONS.pop(key, None)
    while len(_SESSIONS) > MAX_SESSIONS:
        _SESSIONS.pop(min(_SESSIONS, key=lambda k: _SESSIONS[k].born), None)


def _session(token: str) -> Session:
    _reap()
    found = _SESSIONS.get(token)
    if found is None:
        raise HTTPException(404, "that upload has expired; nothing is stored, so send it again")
    return found


def _contract(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in (text or "").split(","):
        method, _, rate = pair.partition("=")
        if method.strip() and rate.strip():
            out[method.strip()] = rate.strip()
    return out


async def _read(upload: UploadFile) -> bytes:
    blob = await upload.read()
    if len(blob) > MAX_UPLOAD:
        raise HTTPException(413, f"{upload.filename} is over the {MAX_UPLOAD // 1024 // 1024}MB limit")
    return blob


def _events_from(blob: bytes, name: str) -> list[EventBase]:
    text = blob.decode("utf-8-sig", errors="replace")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"{name} is not JSON. Upload a Razorpay recon or payments export.") from exc

    items = body.get("items", body) if isinstance(body, dict) else body
    if not isinstance(items, list) or not items:
        raise HTTPException(400, f"{name} has no rows in it")

    looks_like_payments = any(str(r.get("entity")) == "payment" for r in items if isinstance(r, dict))
    try:
        return payments_to_events(items, strict=False) if looks_like_payments else to_events(items, strict=False)
    except UnsupportedRow as exc:
        raise HTTPException(422, str(exc)) from exc


class Finding(BaseModel):
    cause: str
    title: str
    amount_paise: int
    amount: str
    alarming: bool
    note: str
    sql: str


class CloseOut(BaseModel):
    token: str
    source: str
    start: str
    end: str
    gross_paise: int
    landed_paise: int
    gap_paise: int
    residual_paise: int
    closes: bool
    covered: bool
    checked: int
    findings: list[Finding]
    unresolved: list[dict[str, Any]] = Field(default_factory=list)


def _shape(token: str, session: Session, close: Close) -> CloseOut:
    return CloseOut(
        token=token,
        source=session.source,
        start=session.start.isoformat(),
        end=session.end.isoformat(),
        gross_paise=close.variance.gross_captured.paise,
        landed_paise=close.variance.cash_landed.paise,
        gap_paise=close.gap.paise,
        residual_paise=close.residual.paise,
        closes=close.closes,
        covered=close.fully_covered,
        checked=close.checked,
        findings=[
            Finding(
                cause=str(f.cause),
                title=f.title,
                amount_paise=f.amount.paise,
                amount=str(f.amount),
                alarming=bool(f.alarming),
                note=f.evidence.note,
                sql=f.evidence.sql,
            )
            for f in close.findings
        ],
        unresolved=[
            {"kind": u.kind, "detail": u.detail, "amount": str(u.amount)} for u in close.unresolved
        ],
    )


@router.get("/health")
def health() -> dict[str, Any]:
    _reap()
    return {"ok": True, "sessions": len(_SESSIONS), "stores_uploads": False}


@router.post("/close", response_model=CloseOut)
async def close_upload(
    recon: Annotated[UploadFile, File(description="Razorpay recon or payments export, JSON")],
    statement: Annotated[UploadFile | None, File(description="Bank statement, CSV")] = None,
    contract: Annotated[str, Form(description="Rates, e.g. card=2.00,upi=2.36")] = "",
) -> CloseOut:
    events = _events_from(await _read(recon), recon.filename or "the recon file")

    source = recon.filename or "upload"
    if statement is not None and statement.filename:
        try:
            parsed = bank.parse((await _read(statement)).decode("utf-8-sig", errors="replace"))
        except bank.UnreadableStatement as exc:
            raise HTTPException(422, f"could not read {statement.filename}: {exc}") from exc
        source = f"{source} + {statement.filename}"
        if not parsed.reconciles:
            raise HTTPException(
                422,
                f"{statement.filename} does not tie to its own running balance "
                f"({len(parsed.balance_broken)} row(s) disagree), so it is refused rather than trusted",
            )

    days = sorted(e.occurred_at for e in events)
    session = Session(
        events=events,
        contracted=_contract(contract),
        start=days[0],
        end=days[-1],
        source=source,
    )
    try:
        close = session.close()
    except UnknownMethod as exc:
        raise HTTPException(422, str(exc)) from exc

    _reap()
    token = uuid.uuid4().hex
    _SESSIONS[token] = session
    return _shape(token, session, close)


class ExplainIn(BaseModel):
    token: str
    cause: str
    provider: str = ""


class ExplainOut(BaseModel):
    cause: str
    text: str
    source: str
    reason: str


@router.get("/providers")
def providers() -> dict[str, Any]:
    from residual.agents.chat import available

    ready = available()
    return {
        "configured": ready,
        "using": ready[0] if ready else "offline",
        "note": (
            "No model key is set, so explanations are written from the verifier's own output."
            if not ready
            else "Every figure a model writes is checked against what a verifier returned."
        ),
    }


@router.post("/explain", response_model=ExplainOut)
def explain_cause(body: ExplainIn) -> ExplainOut:
    from residual.agents.chat import Chat
    from residual.explain.plain import explain

    session = _session(body.token)
    close = session.close()
    finding = next((f for f in close.findings if str(f.cause) == body.cause), None)
    if finding is None:
        raise HTTPException(404, f"nothing called {body.cause!r} was found in this close")

    out = explain(finding, close, Chat(provider=body.provider))
    return ExplainOut(cause=out.cause, text=out.text, source=out.source, reason=out.reason)


class AskIn(BaseModel):
    token: str
    question: str = Field(min_length=2, max_length=400)


class AskOut(BaseModel):
    question: str
    sql: str
    source: str
    columns: list[str]
    rows: list[list[str]]
    note: str = ""


@router.post("/ask", response_model=AskOut)
def ask_question(body: AskIn) -> AskOut:
    from residual.explain import qa

    session = _session(body.token)
    try:
        answer = qa.ask(session.warehouse, body.question)
    except qa.UnsafeQuestion as exc:
        raise HTTPException(422, f"refused: {exc}") from exc

    return AskOut(
        question=body.question,
        sql=answer.sql,
        source=answer.source,
        columns=list(answer.columns),
        rows=[[qa.render(v, c) for v, c in zip(row, answer.columns, strict=False)]
              for row in answer.rows[:40]],
        note="" if answer.rows else "no rows matched",
    )


class InvestigateIn(BaseModel):
    token: str
    provider: str = ""


class StepOut(BaseModel):
    tool: str
    args: dict[str, Any]
    ok: bool
    found: str = ""


class InvestigateOut(BaseModel):
    driver: str
    short_by: str
    steps: list[StepOut]
    proposal: dict[str, Any] | None
    verdict: str
    accepted: bool


@router.post("/investigate", response_model=InvestigateOut)
def investigate_residual(body: InvestigateIn) -> InvestigateOut:
    from residual.agents.loop import PROVIDERS, Analyst, ModelDriver, OpenAIDriver, investigate
    from residual.agents.tools import Toolbelt
    from residual.explain.propose import adjudicate

    session = _session(body.token)
    close = session.close()
    if close.residual.paise == 0:
        raise HTTPException(
            409,
            "these books already close to zero, so there is nothing open to investigate",
        )

    driver: Any
    if body.provider == "anthropic":
        driver = ModelDriver()
    elif body.provider in PROVIDERS:
        driver = OpenAIDriver(provider=body.provider)
    else:
        driver = Analyst()

    belt = Toolbelt(session.warehouse, close, session.start, session.end, session.contracted)
    run = investigate(belt, driver)
    verdict = adjudicate(session.warehouse, run.proposal, close)

    return InvestigateOut(
        driver=driver.name,
        short_by=str(close.residual),
        steps=[
            StepOut(
                tool=s.tool,
                args=s.args,
                ok=s.ok,
                found=str(s.result.get("amount", "")) if s.ok and isinstance(s.result, dict) else "",
            )
            for s in run.steps
        ],
        proposal=(
            {"name": run.proposal.name, "accounts": list(run.proposal.accounts), "sql": run.proposal.sql}
            if run.proposal
            else None
        ),
        verdict=verdict.reason(),
        accepted=verdict.accepted,
    )
