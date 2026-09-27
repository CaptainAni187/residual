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
    kind: str = "settlement report"
    rows_in: int = 0
    rows_used: int = 0
    statement_rows: int = 0
    notes: list[str] = field(default_factory=list)
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


def _events_from(blob: bytes, name: str, tally: dict[str, Any] | None = None) -> list[EventBase]:
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
        events = (
            payments_to_events(items, strict=False)
            if looks_like_payments
            else to_events(items, strict=False)
        )
    except UnsupportedRow as exc:
        raise HTTPException(422, str(exc)) from exc

    if tally is not None:
        tally["rows_in"] = len(items)
        tally["kind"] = "payments export" if looks_like_payments else "settlement recon report"
        tally["currencies"] = sorted({str(r.get("currency") or "INR") for r in items if isinstance(r, dict)})
    return events


class Finding(BaseModel):
    cause: str
    title: str
    amount_paise: int
    amount: str
    alarming: bool
    note: str
    sql: str


class Inputs(BaseModel):
    files: list[str]
    kind: str
    rows_in: int
    events: int
    period: str
    days: int
    checks_run: int


class CloseOut(BaseModel):
    inputs: Inputs
    assumptions: list[str]
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


def _assumptions(session: Session, close: Close) -> list[str]:
    out: list[str] = []
    if session.contracted:
        rates = ", ".join(f"{m} at {r}%" for m, r in sorted(session.contracted.items()))
        out.append(
            f"Your contracted rates are {rates}. Fees are priced against these, so a "
            f"difference shows as charged above contract. Change them above if they are wrong."
        )
    else:
        out.append(
            "No contracted rates were given, so fees are taken as billed and cannot be "
            "checked for overcharging. Add your rate card to check them."
        )
    out.append(
        f"The period is taken from the file itself, {session.start} to {session.end}. "
        f"Anything settled after that window counts as still owed, not lost."
    )
    if session.statement_rows:
        out.append(
            f"Cash landed is read from your bank statement, {session.statement_rows} rows, "
            f"and each row was checked against the statement's own running balance."
        )
    else:
        out.append(
            "No bank statement was given, so cash landed is taken from the gateway's own "
            "record of what it paid out. Add a statement to check that against your bank."
        )
    if session.rows_in and session.rows_used < session.rows_in:
        out.append(
            f"{session.rows_in - session.rows_used} of {session.rows_in} rows were not in a "
            f"form this could map, and were left out rather than guessed at."
        )
    if close.unresolved:
        out.append(
            f"{len(close.unresolved)} bank credit(s) could not be matched to a payout with "
            f"confidence, so they are listed separately rather than attributed to one."
        )
    out.extend(session.notes)
    return out


def _shape(token: str, session: Session, close: Close) -> CloseOut:
    return CloseOut(
        inputs=Inputs(
            files=[part.strip() for part in session.source.split("+")],
            kind=session.kind,
            rows_in=session.rows_in,
            events=len(session.events),
            period=f"{session.start} to {session.end}",
            days=(session.end - session.start).days + 1,
            checks_run=close.checked,
        ),
        assumptions=_assumptions(session, close),
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
    tally: dict[str, Any] = {}
    events = _events_from(await _read(recon), recon.filename or "the recon file", tally)

    statement_rows = 0
    notes: list[str] = []
    foreign = [c for c in tally.get("currencies", []) if c != "INR"]
    if foreign:
        notes.append(
            f"Rows in {', '.join(foreign)} were left out. This works in rupees only, and "
            f"recording a foreign amount at par would be silently wrong."
        )

    source = recon.filename or "upload"
    if statement is not None and statement.filename:
        try:
            parsed = bank.parse((await _read(statement)).decode("utf-8-sig", errors="replace"))
        except bank.UnreadableStatement as exc:
            raise HTTPException(422, f"could not read {statement.filename}: {exc}") from exc
        source = f"{source} + {statement.filename}"
        statement_rows = len(parsed.rows)
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
        kind=tally.get("kind", "settlement report"),
        rows_in=tally.get("rows_in", 0),
        rows_used=len({getattr(e, "payment_id", e.event_id) for e in events}),
        statement_rows=statement_rows,
        notes=notes,
    )
    try:
        close = session.close()
    except UnknownMethod as exc:
        raise HTTPException(422, str(exc)) from exc

    _reap()
    token = uuid.uuid4().hex
    _SESSIONS[token] = session
    return _shape(token, session, close)


@router.post("/demo", response_model=CloseOut)
def demo_close(week: int = -1, seed: int = 0) -> CloseOut:
    import dataclasses
    import secrets
    from datetime import timedelta

    from residual.simulate.presets import BENCHMARK
    from residual.simulate.world import simulate

    chosen_seed = seed or secrets.randbelow(9_000_000) + 1_000_000
    config = dataclasses.replace(BENCHMARK, seed=chosen_seed)
    world = simulate(config)
    events = world.log.events()

    weeks = max(1, config.days // 7)
    chosen_week = secrets.randbelow(weeks) if week < 0 else max(0, min(week, weeks - 1))
    start = world.start + timedelta(days=chosen_week * 7)
    session = Session(
        events=events,
        contracted={str(m): rate for m, rate in config.base_rates},
        start=start,
        end=start + timedelta(days=6),
        source=f"generated merchant #{chosen_seed}, week {chosen_week + 1}",
        kind="generated sample, not your data",
        rows_in=len(events),
        rows_used=len(events),
        notes=[
            (
                "This is a generated merchant, not your data. It is built to behave like a "
                "real one: fees at published rates, T+2 settlement on the RBI calendar, and a "
                "few things deliberately going wrong so there is something to find."
            )
        ],
    )
    _reap()
    token = uuid.uuid4().hex
    _SESSIONS[token] = session
    return _shape(token, session, session.close())


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
    from residual.agents.chat import Chat
    from residual.explain import qa

    session = _session(body.token)
    chat = Chat()
    try:
        answer = qa.ask(session.warehouse, body.question, speaker=chat if chat.ready else None)
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


class StatementRow(BaseModel):
    date: str
    narration: str
    ref: str
    debit_paise: int
    credit_paise: int
    balance_paise: int | None


class StatementOut(BaseModel):
    rows: list[StatementRow]
    strategy: str
    ties_to_balance: bool
    balances_checked: int
    rows_disagreeing: int
    skipped: list[str]
    credits_paise: int
    debits_paise: int


@router.post("/statement", response_model=StatementOut)
async def read_statement(
    statement: Annotated[UploadFile, File(description="Bank statement, CSV")],
) -> StatementOut:
    blob = await _read(statement)
    try:
        parsed = bank.parse(blob.decode("utf-8-sig", errors="replace"))
    except bank.UnreadableStatement as exc:
        raise HTTPException(422, f"could not read {statement.filename}: {exc}") from exc
    if not parsed.rows:
        raise HTTPException(422, f"no transaction rows were found in {statement.filename}")

    return StatementOut(
        rows=[
            StatementRow(
                date=row.txn_date.isoformat(),
                narration=row.narration,
                ref=row.ref,
                debit_paise=row.debit.paise,
                credit_paise=row.credit.paise,
                balance_paise=row.balance.paise if row.balance else None,
            )
            for row in parsed.rows
        ],
        strategy=parsed.strategy,
        ties_to_balance=parsed.reconciles,
        balances_checked=parsed.balance_checked,
        rows_disagreeing=len(parsed.balance_broken),
        skipped=[f"line {line}: {why}" for line, why in parsed.skipped[:20]],
        credits_paise=sum(r.credit.paise for r in parsed.rows),
        debits_paise=sum(r.debit.paise for r in parsed.rows),
    )


class GstRisk(BaseModel):
    kind: str
    title: str
    amount: str
    amount_paise: int
    detail: str
    action: str


class GstOut(BaseModel):
    source: str
    paid_paise: int
    claimable_paise: int
    at_risk_paise: int
    invoices: int
    risks: list[GstRisk]


@router.post("/gst", response_model=GstOut)
async def gst_credit(
    recon: Annotated[UploadFile, File(description="Razorpay recon or payments export")],
    gstr2b: Annotated[UploadFile, File(description="GSTR-2B, JSON or CSV")],
) -> GstOut:
    from residual.explain import tax
    from residual.ingest import gst
    from residual.ledger import select
    from residual.ledger.money import total

    events = _events_from(await _read(recon), recon.filename or "the recon file")
    name = gstr2b.filename or "the GSTR-2B file"
    text = (await _read(gstr2b)).decode("utf-8-sig", errors="replace")
    try:
        book = gst.parse_csv(text) if name.lower().endswith(".csv") else gst.parse_json(text)
    except gst.UnreadableReturn as exc:
        raise HTTPException(422, f"could not read {name}: {exc}") from exc

    if not book.invoices:
        raise HTTPException(
            422,
            f"{name} has no invoices in it. Upload the GSTR-2B for the same period as the "
            f"payment report, otherwise every rupee of GST would look unclaimable.",
        )

    days = sorted(e.occurred_at for e in events)
    captures = [e for e in select.captures(events) if days[0] <= e.occurred_at <= days[-1]]
    paid = total(e.tax for e in captures)
    available = book.credit_from(tax.RAZORPAY_GSTIN)

    found = [
        r
        for r in (
            tax.gst_input_credit(events, book, days[0], days[-1]),
            tax.unmatched_suppliers(book),
        )
        if r is not None and r.material
    ]
    return GstOut(
        source=f"{recon.filename} + {name}",
        paid_paise=paid.paise,
        claimable_paise=available.paise,
        at_risk_paise=sum(r.amount.paise for r in found),
        invoices=len(book.invoices),
        risks=[
            GstRisk(
                kind=r.kind, title=r.title, amount=str(r.amount),
                amount_paise=r.amount.paise, detail=r.detail, action=r.action,
            )
            for r in found
        ],
    )
