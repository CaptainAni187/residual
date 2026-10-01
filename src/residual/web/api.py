from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from residual.explain.close import Close, run_close
from residual.explain.hypotheses import UnknownMethod
from residual.explain.tax import Risk
from residual.ingest import bank
from residual.ingest.razorpay import UnsupportedRow, payments_to_events, to_events
from residual.ledger.events import EventBase
from residual.ledger.warehouse import Warehouse

MAX_UPLOAD = 4 * 1024 * 1024
MAX_SESSIONS = 32
SESSION_TTL = 45 * 60
METHODS = ("card", "upi", "netbanking", "wallet", "emi")

router = APIRouter(prefix="/api")


def problem(status: int, code: str, message: str, fix: str = "", field: str = "") -> HTTPException:
    return HTTPException(status, {"code": code, "message": message, "fix": fix, "field": field})


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
    statement_verified: bool = False
    notes: list[str] = field(default_factory=list)
    risks: list[Risk] = field(default_factory=list)
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


def _keep(session: Session) -> str:
    _reap()
    token = uuid.uuid4().hex
    _SESSIONS[token] = session
    return token


def _session(token: str) -> Session:
    _reap()
    found = _SESSIONS.get(token)
    if found is None:
        raise problem(
            404,
            "session_expired",
            "This result has expired.",
            "Nothing you upload is kept, so results last 45 minutes. Run the tool again.",
        )
    return found


def _contract(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in (text or "").split(","):
        if not pair.strip():
            continue
        method, sep, rate = pair.partition("=")
        method, rate = method.strip().lower(), rate.strip().rstrip("%")
        if not sep or not method or not rate:
            raise problem(
                422, "bad_rates", f"“{pair.strip()}” is not a rate.",
                "Write each rate as method=percent, separated by commas, e.g. card=2.00,upi=0",
                "contract",
            )
        if method not in METHODS:
            raise problem(
                422, "bad_rates", f"“{method}” is not a payment method this knows.",
                f"Use any of: {', '.join(METHODS)}.", "contract",
            )
        try:
            value = Decimal(rate)
        except InvalidOperation as exc:
            raise problem(
                422, "bad_rates", f"“{rate}” is not a number.",
                "Rates are percentages, e.g. 2.00 for 2%.", "contract",
            ) from exc
        if not Decimal(0) <= value <= Decimal(10):
            raise problem(
                422, "bad_rates", f"A {method} rate of {rate}% is outside 0–10%.",
                "Gateway rates are a few percent. Check for a misplaced decimal point.", "contract",
            )
        out[method] = str(value)
    return out


async def _read(upload: UploadFile, field_name: str) -> bytes:
    blob = await upload.read()
    name = upload.filename or "that file"
    if not blob:
        raise problem(400, "empty_file", f"{name} is empty.", "Choose the file again; it may not have downloaded fully.", field_name)
    if len(blob) > MAX_UPLOAD:
        raise problem(
            413, "too_large",
            f"{name} is {len(blob) / 1024 / 1024:.1f} MB. The limit is {MAX_UPLOAD // 1024 // 1024} MB.",
            "Export a shorter date range and run it again.", field_name,
        )
    return blob


def _events_from(blob: bytes, name: str, tally: dict[str, Any] | None = None) -> list[EventBase]:
    text = blob.decode("utf-8-sig", errors="replace")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        first = text.splitlines()[0] if text.strip() else ""
        hint = (
            "This looks like a CSV. Download the report as JSON from the gateway dashboard."
            if "," in first
            else "Download the settlement or payments report from the gateway dashboard as JSON."
        )
        raise problem(400, "not_json", f"{name} is not a gateway report.", hint, "recon") from exc

    items = body.get("items", body) if isinstance(body, dict) else body
    if not isinstance(items, list) or not items:
        raise problem(
            400, "empty_report", f"{name} has no transactions in it.",
            "Check the date range you exported covers some activity.", "recon",
        )
    if not all(isinstance(r, dict) for r in items):
        raise problem(400, "not_a_report", f"{name} is JSON, but not a list of transactions.", "Upload the settlement or payments report itself.", "recon")

    looks_like_payments = any(str(r.get("entity")) == "payment" for r in items)
    try:
        events = payments_to_events(items, strict=False) if looks_like_payments else to_events(items, strict=False)
    except UnsupportedRow as exc:
        raise problem(422, "unmappable", f"{name} has rows this cannot read: {exc}", "", "recon") from exc

    if not events:
        raise problem(
            422, "nothing_usable", f"None of the {len(items)} rows in {name} could be used.",
            "They may all be in a foreign currency or an unsupported type.", "recon",
        )
    if tally is not None:
        tally["rows_in"] = len(items)
        tally["kind"] = "payments export" if looks_like_payments else "settlement recon report"
        tally["currencies"] = sorted({str(r.get("currency") or "INR") for r in items})
    return events


async def _read_statement(upload: UploadFile, password: str, field_name: str = "statement") -> bank.Statement:
    blob = await _read(upload, field_name)
    name = upload.filename or "the statement"
    is_pdf = name.lower().endswith(".pdf") or blob.lstrip().startswith(b"%PDF")
    try:
        parsed = (
            bank.parse_pdf_bytes(blob, password=password or None, label=name)
            if is_pdf
            else bank.parse(blob.decode("utf-8-sig", errors="replace"))
        )
    except bank.PasswordRequired as exc:
        if exc.wrong:
            raise problem(422, "bad_password", "That password did not open the statement.", "Banks usually use your customer ID or date of birth. Check the email the statement came with.", field_name) from exc
        raise problem(422, "needs_password", f"{name} is password protected.", "Enter its password to open it. The password is used once and never kept.", field_name) from exc
    except bank.NeedsOcr as exc:
        raise problem(422, "needs_ocr", f"{name} is a scanned image, not a text PDF.", "Download the statement from net banking as a PDF or CSV rather than scanning a printout.", field_name) from exc
    except bank.UnreadableStatement as exc:
        raise problem(422, "statement_unreadable", f"Could not read {name}: {exc}.", "Try the CSV or Excel-CSV download from net banking instead.", field_name) from exc

    if not parsed.rows:
        raise problem(422, "empty_statement", f"No transactions were found in {name}.", "Check it covers the same dates as the gateway report.", field_name)
    return parsed


class Finding(BaseModel):
    cause: str
    title: str
    amount_paise: int
    amount: str
    alarming: bool
    note: str
    sql: str
    refs: list[str] = Field(default_factory=list)


class Inputs(BaseModel):
    files: list[str]
    kind: str
    rows_in: int
    events: int
    period: str
    covers: str
    days: int
    checks_run: int


class ActionOut(BaseModel):
    cause: str
    title: str
    why: str
    amount_paise: int
    urgency: str
    who: str
    deadline: str
    draft: str
    impact: str


class InsightOut(BaseModel):
    totals: dict[str, int]
    labels: dict[str, str]
    recoverable_paise: int
    actions: list[ActionOut]


def _insight(close: Close, start: date, end: date) -> InsightOut:
    from residual.explain.insights import BUCKET_LABEL, analyse

    found = analyse(close, start, end)
    return InsightOut(
        totals=found.totals, labels=BUCKET_LABEL, recoverable_paise=found.recoverable_paise,
        actions=[
            ActionOut(cause=a.cause, title=a.title, why=a.why, amount_paise=a.amount_paise,
                      urgency=a.urgency, who=a.who, deadline=a.deadline, draft=a.draft, impact=a.impact)
            for a in found.actions
        ],
    )


class CloseOut(BaseModel):
    insight: InsightOut
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
        out.append(f"Your contracted rates are {rates}. Fees are priced against these, so a difference shows as charged above contract. Change them if they are wrong.")
    else:
        out.append("No contracted rates were given, so fees are taken as billed and cannot be checked for overcharging. Add your rate card to check them.")
    out.append(f"The period is taken from the file itself, {session.start} to {session.end}. Anything settled after that window counts as still owed, not lost.")
    if session.statement_rows and session.statement_verified:
        out.append(f"Cash landed is read from your bank statement, {session.statement_rows} rows, and each row was checked against the statement's own running balance.")
    elif session.statement_rows:
        out.append(f"Cash landed is read from your bank statement, {session.statement_rows} rows. It prints no running balance, so it could not be checked against itself — treat it as given.")
    else:
        out.append("No bank statement was given, so cash landed is taken from the gateway's own record of what it paid out. Add a statement to check that against your bank.")
    if session.rows_in and session.rows_used < session.rows_in:
        out.append(f"{session.rows_in - session.rows_used} of {session.rows_in} rows were not in a form this could map, and were left out rather than guessed at.")
    if close.unresolved:
        out.append(f"{len(close.unresolved)} bank credit(s) could not be matched to a payout with confidence, so they are listed separately rather than attributed to one.")
    out.extend(session.notes)
    return out


def _shape(token: str, session: Session, close: Close) -> CloseOut:
    return CloseOut(
        insight=_insight(close, session.start, session.end),
        inputs=Inputs(
            files=[part.strip() for part in session.source.split("+")],
            kind=session.kind,
            rows_in=session.rows_in,
            events=len(session.events),
            period=f"{session.start} to {session.end}",
            covers=(
                f"{min(e.occurred_at for e in session.events)} to "
                f"{max(e.occurred_at for e in session.events)}"
            ),
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
                cause=str(f.cause), title=f.title, amount_paise=f.amount.paise, amount=str(f.amount),
                alarming=bool(f.alarming), note=f.evidence.note, sql=f.evidence.sql,
                refs=list(f.evidence.entity_ids[:8]),
            )
            for f in close.findings
        ],
        unresolved=[{"kind": u.kind, "detail": u.detail, "amount": str(u.amount)} for u in close.unresolved],
    )


@router.get("/health")
def health() -> dict[str, Any]:
    _reap()
    return {"ok": True, "sessions": len(_SESSIONS), "stores_uploads": False}


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


@router.post("/close", response_model=CloseOut)
async def close_upload(
    recon: Annotated[UploadFile, File(description="Razorpay recon or payments export, JSON")],
    statement: Annotated[UploadFile | None, File(description="Bank statement, CSV or PDF")] = None,
    statement_password: Annotated[str, Form()] = "",
    contract: Annotated[str, Form(description="Rates, e.g. card=2.00,upi=2.36")] = "",
) -> CloseOut:
    rates = _contract(contract)
    tally: dict[str, Any] = {}
    events = _events_from(await _read(recon, "recon"), recon.filename or "the report", tally)

    notes: list[str] = []
    foreign = [c for c in tally.get("currencies", []) if c != "INR"]
    if foreign:
        notes.append(f"Rows in {', '.join(foreign)} were left out. This works in rupees only, and recording a foreign amount at par would be silently wrong.")

    source = recon.filename or "upload"
    statement_rows = 0
    statement_verified = False
    if statement is not None and statement.filename:
        parsed = await _read_statement(statement, statement_password)
        source = f"{source} + {statement.filename}"
        statement_rows = len(parsed.rows)
        statement_verified = parsed.reconciles
        if not parsed.reconciles and parsed.balance_checked:
            raise problem(
                422, "balance_mismatch",
                f"{statement.filename} does not add up against its own running balance ({len(parsed.balance_broken)} row(s) disagree).",
                "It may be a partial export or edited. Download it again from net banking.",
                "statement",
            )

    days = sorted(e.occurred_at for e in events)
    session = Session(
        events=events, contracted=rates, start=days[0], end=days[-1], source=source,
        kind=tally.get("kind", "settlement report"), rows_in=tally.get("rows_in", 0),
        rows_used=len({getattr(e, "payment_id", e.event_id) for e in events}),
        statement_rows=statement_rows, statement_verified=statement_verified, notes=notes,
    )
    try:
        close = session.close()
    except UnknownMethod as exc:
        raise problem(422, "bad_rates", str(exc), "", "contract") from exc

    return _shape(_keep(session), session, close)


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
        events=events, contracted={str(m): rate for m, rate in config.base_rates},
        start=start, end=start + timedelta(days=6),
        source=f"sample merchant #{chosen_seed}, week {chosen_week + 1}",
        kind="sample data, not yours", rows_in=len(events), rows_used=len(events),
        notes=[(
            "This is a generated merchant, not your data. It behaves like a real one — fees at "
            "published rates, T+2 settlement on the RBI calendar — with a few things deliberately "
            "going wrong so there is something to find."
        )],
    )
    return _shape(_keep(session), session, session.close())


class ExplainIn(BaseModel):
    token: str
    cause: str


class ExplainOut(BaseModel):
    cause: str
    text: str
    source: str
    reason: str


@router.post("/explain", response_model=ExplainOut)
def explain_cause(body: ExplainIn) -> ExplainOut:
    from residual.agents.chat import Chat
    from residual.explain.plain import explain

    session = _session(body.token)
    close = session.close()
    finding = next((f for f in close.findings if str(f.cause) == body.cause), None)
    if finding is None:
        raise problem(404, "not_found", "That line is not in this result.", "Run the tool again.")
    out = explain(finding, close, Chat())
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
        raise problem(422, "refused", f"That question was refused: {exc}", "Ask about your payments, fees, payouts or refunds.") from exc

    note = ""
    if answer.refused:
        note = answer.refused
    elif not answer.rows:
        note = "Nothing in your data matched that."
    return AskOut(
        question=body.question, sql=answer.sql, source=answer.source, columns=list(answer.columns),
        rows=[[qa.render(v, c) for v, c in zip(row, answer.columns, strict=False)] for row in answer.rows[:40]],
        note=note,
    )


class InvestigateIn(BaseModel):
    token: str
    cause: str = ""


class StepOut(BaseModel):
    tool: str
    args: dict[str, Any]
    ok: bool
    found: str = ""


class InvestigateOut(BaseModel):
    mode: str
    cause: str
    driver: str
    short_by: str
    steps: list[StepOut]
    proposal: dict[str, Any] | None
    verdict: str
    accepted: bool


@router.post("/investigate", response_model=InvestigateOut)
def investigate_residual(body: InvestigateIn) -> InvestigateOut:
    from residual.agents.loop import Analyst, investigate
    from residual.agents.tasks import rediscover_with_agent
    from residual.agents.tools import Toolbelt
    from residual.domain.causes import Cause
    from residual.explain.propose import adjudicate, refined_by

    session = _session(body.token)
    close = session.close()

    if close.residual.paise != 0 and not body.cause:
        belt = Toolbelt(session.warehouse, close, session.start, session.end, session.contracted)
        run = investigate(belt, Analyst())
        verdict = adjudicate(session.warehouse, run.proposal, close)
        mode, cause_name, short = "open_residual", "", str(close.residual)
    else:
        candidates = [f for f in close.findings if f.amount.paise and not refined_by(f.cause, session.contracted)]
        if body.cause:
            candidates = [f for f in candidates if str(f.cause) == body.cause]
        else:
            candidates.sort(key=lambda f: (not f.alarming, -abs(f.amount.paise)))
        if not candidates:
            raise problem(
                409, "nothing_to_check",
                "There is nothing here for the agent to check independently.",
                "It checks lines that stand on their own; this one is part of another.",
            )
        target = candidates[0]
        found = rediscover_with_agent(
            session.events, session.start, session.end, session.contracted,
            session.warehouse, Cause(str(target.cause)), Analyst(),
        )
        run, verdict = found.run, found.verdict
        mode, cause_name, short = "second_opinion", str(target.cause), str(target.amount)

    return InvestigateOut(
        mode=mode, cause=cause_name, driver="analyst", short_by=short,
        steps=[
            StepOut(tool=s.tool, args=s.args, ok=s.ok,
                    found=str(s.result.get("amount", "")) if s.ok and isinstance(s.result, dict) else "")
            for s in run.steps
        ],
        proposal=({"name": run.proposal.name, "accounts": list(run.proposal.accounts), "sql": run.proposal.sql} if run.proposal else None),
        verdict=verdict.reason(),
        accepted=verdict.accepted,
    )


class DraftIn(BaseModel):
    token: str
    kind: str


class DraftOut(BaseModel):
    kind: str
    to: str
    subject: str
    body: str
    source: str
    reason: str
    items: int


@router.post("/draft", response_model=DraftOut)
def draft_action(body: DraftIn) -> DraftOut:
    from residual.agents.chat import Chat
    from residual.agents.drafts import KINDS, NothingToDraft, draft

    if body.kind not in KINDS:
        raise problem(422, "unknown_draft", f"There is no “{body.kind}” draft.", f"Use one of: {', '.join(KINDS)}.")
    session = _session(body.token)
    close = None if body.kind == "gst_followup" else session.close()
    try:
        out = draft(body.kind, session.start, session.end, close=close, risks=session.risks, speaker=Chat())
    except NothingToDraft as exc:
        raise problem(409, "nothing_to_draft", f"Nothing to send — {exc}.", "") from exc
    return DraftOut(kind=out.kind, to=out.to, subject=out.subject, body=out.body, source=out.source, reason=out.reason, items=out.items)


class StatementRow(BaseModel):
    date: str
    narration: str
    ref: str
    debit_paise: int
    credit_paise: int
    balance_paise: int | None


class StatementOut(BaseModel):
    source: str
    rows: list[StatementRow]
    strategy: str
    ties_to_balance: bool
    balances_checked: int
    rows_disagreeing: int
    skipped: list[str]
    credits_paise: int
    debits_paise: int
    first: str
    last: str
    gateway_credits: int
    assumptions: list[str]


@router.post("/statement", response_model=StatementOut)
async def read_statement(
    statement: Annotated[UploadFile, File(description="Bank statement, CSV or PDF")],
    password: Annotated[str, Form()] = "",
) -> StatementOut:
    parsed = await _read_statement(statement, password)
    rows = parsed.rows
    gateway = sum(1 for r in rows if r.credit.paise and "razorpay" in r.narration.lower())

    assumed = [f"Read as {'a PDF' if parsed.strategy != 'csv' else 'a CSV'}; the layout was detected automatically."]
    if parsed.balance_checked:
        assumed.append(
            f"{parsed.balance_checked} rows carried a running balance and each was checked against the one before."
            + ("" if parsed.reconciles else f" {len(parsed.balance_broken)} did not agree.")
        )
    else:
        assumed.append("This statement prints no running balance, so it could not check itself.")
    if parsed.skipped:
        assumed.append(f"{len(parsed.skipped)} line(s) looked like headers, totals or notes and were left out.")

    return StatementOut(
        source=statement.filename or "statement",
        rows=[
            StatementRow(date=r.txn_date.isoformat(), narration=r.narration, ref=r.ref,
                         debit_paise=r.debit.paise, credit_paise=r.credit.paise,
                         balance_paise=r.balance.paise if r.balance else None)
            for r in rows
        ],
        strategy=parsed.strategy, ties_to_balance=parsed.reconciles,
        balances_checked=parsed.balance_checked, rows_disagreeing=len(parsed.balance_broken),
        skipped=[f"line {line}: {why}" for line, why in parsed.skipped[:20]],
        credits_paise=sum(r.credit.paise for r in rows), debits_paise=sum(r.debit.paise for r in rows),
        first=min(r.txn_date for r in rows).isoformat(), last=max(r.txn_date for r in rows).isoformat(),
        gateway_credits=gateway, assumptions=assumed,
    )


class GstRisk(BaseModel):
    kind: str
    title: str
    amount: str
    amount_paise: int
    detail: str
    action: str


class GstOut(BaseModel):
    token: str
    source: str
    period: str
    paid_paise: int
    claimable_paise: int
    at_risk_paise: int
    invoices: int
    risks: list[GstRisk]
    assumptions: list[str]


@router.post("/gst", response_model=GstOut)
async def gst_credit(
    recon: Annotated[UploadFile, File(description="Razorpay recon or payments export")],
    gstr2b: Annotated[UploadFile, File(description="GSTR-2B, JSON or CSV")],
) -> GstOut:
    from residual.explain import tax
    from residual.ingest import gst
    from residual.ledger import select
    from residual.ledger.money import total

    events = _events_from(await _read(recon, "recon"), recon.filename or "the report")
    name = gstr2b.filename or "the GSTR-2B"
    text = (await _read(gstr2b, "gstr2b")).decode("utf-8-sig", errors="replace")
    try:
        book = gst.parse_csv(text) if name.lower().endswith(".csv") else gst.parse_json(text)
    except gst.UnreadableReturn as exc:
        raise problem(422, "gst_unreadable", f"Could not read {name}: {exc}.", "Download GSTR-2B from the GST portal as JSON.", "gstr2b") from exc
    if not book.invoices:
        raise problem(
            422, "gst_empty", f"{name} has no invoices in it.",
            "Upload the GSTR-2B for the same period as the gateway report — otherwise every rupee would look unclaimable.",
            "gstr2b",
        )

    days = sorted(e.occurred_at for e in events)
    paid = total(e.tax for e in select.captures(events) if days[0] <= e.occurred_at <= days[-1])
    available = book.credit_from(tax.RAZORPAY_GSTIN)
    found = [
        r for r in (tax.gst_input_credit(events, book, days[0], days[-1]), tax.unmatched_suppliers(book))
        if r is not None and r.material
    ]
    session = Session(events=events, contracted={}, start=days[0], end=days[-1], source=f"{recon.filename} + {name}", risks=found)
    return GstOut(
        token=_keep(session), source=session.source, period=f"{days[0]} to {days[-1]}",
        paid_paise=paid.paise, claimable_paise=available.paise,
        at_risk_paise=sum(r.amount.paise for r in found), invoices=len(book.invoices),
        risks=[GstRisk(kind=r.kind, title=r.title, amount=str(r.amount), amount_paise=r.amount.paise, detail=r.detail, action=r.action) for r in found],
        assumptions=[
            f"GST paid is the tax on gateway fees in your report, {days[0]} to {days[-1]}.",
            "Claimable is what GSTR-2B shows against the gateway's GSTIN. Credit only becomes claimable once the gateway files that invoice.",
            f"{len(book.invoices)} invoice(s) were read from the return.",
        ],
    )


class WeekOut(BaseModel):
    start: str
    end: str
    gross_paise: int
    gap_paise: int
    totals: dict[str, int]
    fee_rate: float
    contract_rate: float
    overcharge_paise: int
    flagged: int


class QuarterOut(BaseModel):
    source: str
    covers: str
    days: int
    gross_paise: int
    landed_paise: int
    gap_paise: int
    residual_paise: int
    insight: InsightOut
    weeks: list[WeekOut]
    causes: list[dict[str, Any]]
    hike_started: str
    contracted_rate: float
    headline: list[str]
    assumptions: list[str]


class QuarterIn(BaseModel):
    token: str


@router.post("/quarter", response_model=QuarterOut)
def quarter(body: QuarterIn) -> QuarterOut:
    from residual.explain.insights import _rupees as rs
    from residual.explain.insights import bucket_of, week_of, weeks_between

    session = _session(body.token)
    first = min(e.occurred_at for e in session.events)
    last = max(e.occurred_at for e in session.events)

    whole = run_close(session.events, first, last, session.contracted, session.warehouse)
    weeks = [
        week_of(run_close(session.events, s, e, session.contracted, session.warehouse), s, e)
        for s, e in weeks_between(first, last)
    ]
    insight = _insight(whole, first, last)

    hike = next((w for w in weeks if w.overcharge_paise > 0), None)
    if hike is not None:
        since = sum(w.overcharge_paise for w in weeks if w.start >= hike.start)
        days = (last - hike.start).days + 1
        yearly = round(since * 365 / max(days, 1))
        for action in insight.actions:
            if action.cause == "fee_rate_increase":
                action.impact = f"Recover what was overcharged, and stop about ₹{yearly // 100:,} a year"

    gap = whole.gap.paise
    act = insight.recoverable_paise
    timing = insight.totals.get("timing", 0)
    headline = [
        f"{len(weeks)} weeks of activity, {rs(whole.variance.gross_captured.paise)} captured.",
        f"{rs(gap)} of it never reached the bank inside this window.",
    ]
    if gap:
        headline.append(
            f"{act / gap:.0%} of that gap ({rs(act)}) is money you can act on: "
            f"{rs(insight.totals.get('chase', 0))} to chase and {rs(insight.totals.get('tax', 0))} to claim back on tax."
        )
        if timing > 0:
            headline.append(f"{timing / gap:.0%} is timing and will settle on its own.")
    if hike is not None:
        headline.append(f"Fees went above your contract from the week of {hike.start:%d %b} onward.")

    rates = [float(r) for r in session.contracted.values()] if session.contracted else []
    return QuarterOut(
        source=session.source, covers=f"{first} to {last}", days=(last - first).days + 1,
        gross_paise=whole.variance.gross_captured.paise, landed_paise=whole.variance.cash_landed.paise,
        gap_paise=gap, residual_paise=whole.residual.paise, insight=insight,
        weeks=[
            WeekOut(start=w.start.isoformat(), end=w.end.isoformat(), gross_paise=w.gross_paise,
                    gap_paise=w.gap_paise, totals=w.totals, fee_rate=w.fee_rate, contract_rate=w.contract_rate,
                    overcharge_paise=w.overcharge_paise, flagged=w.flagged)
            for w in weeks
        ],
        causes=[
            {"cause": str(f.cause), "title": f.title, "amount_paise": f.amount.paise,
             "bucket": bucket_of(f), "note": f.evidence.note}
            for f in sorted(whole.findings, key=lambda f: -abs(f.amount.paise))
        ],
        hike_started=hike.start.isoformat() if hike else "",
        contracted_rate=(sum(rates) / len(rates) / 100) if rates else 0.0,
        headline=headline,
        assumptions=_assumptions(session, whole),
    )
