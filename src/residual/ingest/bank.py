
from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from residual.ledger import events as ev
from residual.ledger.money import Money

_ROLES: dict[str, tuple[str, ...]] = {
    "value_date": ("valuedate", "valuedt", "valuedate1"),
    "txn_date": ("transactiondate", "txndate", "trandate", "postingdate", "date"),
    "narration": (
        "transactionremarks", "narration", "particulars", "description",
        "remarks", "details", "transactiondetails",
    ),
    "ref": (
        "chqrefnumber", "chqrefno", "chequenumber", "chequeno", "refnocheque",
        "refno", "reference", "chqno", "utr",
    ),
    "debit": (
        "withdrawalamountinr", "withdrawalamount", "withdrawalamt", "withdrawal",
        "debitamount", "debit", "dr",
    ),
    "credit": (
        "depositamountinr", "depositamount", "depositamt", "deposit",
        "creditamount", "credit", "cr",
    ),
    "amount": ("transactionamount", "amount", "amt"),
    "indicator": ("drcr", "crdr", "type", "transactiontype", "debitcredit"),
    "balance": ("closingbalance", "runningbalance", "balanceinr", "balance", "bal"),
}

_DATE_FORMATS = (
    "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y",
    "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %B %Y",
    "%Y-%m-%d", "%m/%d/%Y",
)

_TRAILING_MARK = re.compile(r"\s*(cr|dr)\s*$", re.IGNORECASE)


class PdfPage(Protocol):

    def extract_tables(self, settings: dict[str, str]) -> list[list[list[str | None]]]: ...

    def extract_words(self, **kwargs: Any) -> list[dict[str, Any]]: ...


MAX_BYTES = 256 * 1024 * 1024
MAX_FIELD = 64 * 1024


class UnreadableStatement(Exception):
    pass


def _norm(cell: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (cell or "").lower())


def parse_date(text: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:

            return datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def parse_amount(text: str) -> tuple[Money | None, int]:
    raw = (text or "").strip()
    if raw in {"", "-", "--", "NA", "N/A"}:
        return None, 0
    sign = 0
    if mark := _TRAILING_MARK.search(raw):
        sign = 1 if mark.group(1).lower() == "cr" else -1
        raw = _TRAILING_MARK.sub("", raw)
    if raw.startswith("(") and raw.endswith(")"):
        sign, raw = -1, raw[1:-1]
    raw = raw.replace(",", "").replace("₹", "").replace("INR", "").strip()
    if raw.startswith("-"):
        sign, raw = -1, raw[1:]
    if not raw:
        return None, 0
    try:
        return Money.parse(Decimal(raw)), sign
    except (InvalidOperation, ValueError, ArithmeticError):
        return None, 0


def parse_signed(text: str) -> Money | None:
    amount, sign = parse_amount(text)
    if amount is None:
        return None
    return -amount if sign < 0 else amount


@dataclass(frozen=True, slots=True)
class Row:
    txn_date: date
    value_date: date
    narration: str
    ref: str
    debit: Money
    credit: Money
    balance: Money | None
    line: int


@dataclass(slots=True)
class Statement:

    rows: list[Row] = field(default_factory=list)
    columns: dict[str, int] = field(default_factory=dict)
    header_line: int = 0
    skipped: list[tuple[int, str]] = field(default_factory=list)
    balance_checked: int = 0
    balance_broken: list[int] = field(default_factory=list)
    balances_seen: int = 0
    strategy: str = "csv"

    @property
    def reconciles(self) -> bool:
        return self.balance_checked > 0 and not self.balance_broken

    @property
    def balance_rate(self) -> float:
        if not self.balance_checked:
            return 0.0
        return 1 - len(self.balance_broken) / self.balance_checked

    @property
    def verifiable(self) -> bool:
        return self.balance_checked > 0

    def report(self) -> str:
        if not self.verifiable:
            if "balance" not in self.columns:
                why = "this statement carries no balance column"
            elif self.balances_seen < 2:
                why = (
                    f"only {self.balances_seen} balance figure in the file, so "
                    f"there is nothing to check it against"
                )
            else:
                why = "no two balances could be compared"
            return (
                f"{len(self.rows)} rows parsed; {why}, so the parse is "
                f"unverified -- treat the import as provisional"
            )
        if self.reconciles:
            return (
                f"{len(self.rows)} rows parsed; every one agrees with the "
                f"statement's own running balance"
            )
        return (
            f"{len(self.rows)} rows parsed but {len(self.balance_broken)} of "
            f"{self.balance_checked} disagree with the running balance "
            f"(lines {self.balance_broken[:5]}) -- the parse is wrong, not the bank"
        )


def _map_columns(row: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    keys = [(col, _norm(cell)) for col, cell in enumerate(row) if _norm(cell)]

    for role, words in _ROLES.items():
        for col, key in keys:
            if col in mapping.values() or role in mapping:
                continue
            if key in words:
                mapping[role] = col

    for role, words in _ROLES.items():
        if role in mapping:
            continue
        for col, key in keys:
            if col in mapping.values():
                continue
            if any(key.startswith(w) and len(key) - len(w) < 6 for w in words):
                mapping[role] = col
                break
    return mapping


def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    best_line, best_map, best_score = -1, {}, 0
    for i, row in enumerate(rows[:40]):
        mapping = _map_columns(row)
        score = len(mapping) + (2 if "narration" in mapping else 0)
        if ("debit" in mapping and "credit" in mapping) or "amount" in mapping:
            score += 2
        if score > best_score:
            best_line, best_map, best_score = i, mapping, score

    if best_score < 4 or "txn_date" not in best_map or "narration" not in best_map:
        raise UnreadableStatement(
            "no row in the first 40 lines looks like a statement header; "
            f"best guess was line {best_line + 1} with {sorted(best_map)}"
        )
    return best_line, best_map


def parse(text: str) -> Statement:
    previous = csv.field_size_limit(MAX_FIELD)
    try:
        return parse_rows(list(csv.reader(io.StringIO(text))))
    except csv.Error as exc:
        raise UnreadableStatement(
            f"could not read this as delimited text: {exc}. A field longer than "
            f"{MAX_FIELD:,} bytes is not a narration, and unbalanced quotes are "
            f"not a statement"
        ) from exc
    finally:
        csv.field_size_limit(previous)


def parse_rows(rows: list[list[str]]) -> Statement:
    header_line, columns = _find_header(rows)
    out = Statement(columns=columns, header_line=header_line + 1)

    def cell(row: list[str], role: str) -> str:
        idx = columns.get(role)
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    previous: Money | None = None
    pending = Money.zero()
    for i, row in enumerate(rows[header_line + 1:], start=header_line + 2):
        if not any(c.strip() for c in row):
            continue
        txn = parse_date(cell(row, "txn_date"))
        if txn is None:
            out.skipped.append((i, (cell(row, "txn_date") or row[0])[:40]))
            continue

        debit, credit = _amounts(row, cell)
        balance = parse_signed(cell(row, "balance"))

        if debit is None and credit is None:
            if balance is not None and previous is None:
                previous = balance
                out.balances_seen += 1
                out.skipped.append((i, "opening balance (used as the anchor)"))
            else:
                out.skipped.append((i, "no amount"))
            continue
        parsed = Row(
            txn_date=txn,
            value_date=parse_date(cell(row, "value_date")) or txn,
            narration=cell(row, "narration"),
            ref=cell(row, "ref"),
            debit=debit or Money.zero(),
            credit=credit or Money.zero(),
            balance=balance,
            line=i,
        )
        out.rows.append(parsed)

        pending = pending + parsed.credit - parsed.debit
        if balance is not None:
            out.balances_seen += 1
            if previous is not None:
                out.balance_checked += 1
                if (previous + pending).paise != balance.paise:
                    out.balance_broken.append(i)
            previous = balance
            pending = Money.zero()

    return out


def _amounts(row: list[str], cell) -> tuple[Money | None, Money | None]:
    debit, _ = parse_amount(cell(row, "debit"))
    credit, _ = parse_amount(cell(row, "credit"))
    if debit is not None or credit is not None:
        return debit, credit

    amount, sign = parse_amount(cell(row, "amount"))
    if amount is None:
        return None, None
    marker = _norm(cell(row, "indicator"))
    if marker.startswith("c") or sign > 0:
        return None, amount
    if marker.startswith("d") or sign < 0:
        return amount, None
    return None, amount


_CHARGE_WORDS = (
    "chrg", "charge", "chg", "comm", "amc", "maint", "sms alert",
    "annual fee", "service fee", "penalty", "gst on",
)


def looks_like_a_bank_charge(narration: str) -> bool:
    text = narration.lower()
    return any(word in text for word in _CHARGE_WORDS)


def to_events(statement: Statement, account: str = "bank") -> list[ev.EventBase]:
    out: list[ev.EventBase] = []
    for row in statement.rows:
        txn_id = f"{account}-{row.txn_date:%Y%m%d}-{row.line}"
        narration = " ".join(filter(None, (row.narration, row.ref)))
        if row.credit.paise:
            out.append(
                ev.BankCreditReceived(
                    event_id=f"stmt-cr-{txn_id}",
                    occurred_at=row.txn_date,
                    recorded_at=row.txn_date,
                    bank_txn_id=txn_id,
                    amount=row.credit,
                    narration=narration,
                    value_date=row.value_date,
                )
            )
        elif row.debit.paise:
            kind = (
                ev.BankChargeApplied
                if looks_like_a_bank_charge(narration)
                else ev.BankDebit
            )
            out.append(
                kind(
                    event_id=f"stmt-dr-{txn_id}",
                    occurred_at=row.txn_date,
                    recorded_at=row.txn_date,
                    bank_txn_id=txn_id,
                    amount=row.debit,
                    narration=narration,
                )
            )
    return out


def load(path: str | Path) -> Statement:
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise UnreadableStatement(
            f"{path} is {size / 1e6:,.0f} MB, past the {MAX_BYTES / 1e6:,.0f} MB "
            f"ceiling. A statement that large is a different kind of file"
        )
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return parse(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise UnreadableStatement(f"could not decode {path} as text")


def parse_pdf(path: str | Path, password: str | None = None) -> Statement:
    import pdfplumber

    size = Path(path).stat().st_size
    if size > MAX_BYTES:
        raise UnreadableStatement(
            f"{path} is {size / 1e6:,.0f} MB, past the {MAX_BYTES / 1e6:,.0f} MB ceiling"
        )

    candidates: list[tuple[str, list[list[str]]]] = []
    try:
        with pdfplumber.open(str(path), password=password or "") as pdf:
            for name, extract in _STRATEGIES:
                rows: list[list[str]] = []
                for page in pdf.pages:
                    rows.extend(extract(page))
                if len(rows) > 2:
                    candidates.append((name, rows))
    except UnreadableStatement:
        raise
    except Exception as exc:
        raise UnreadableStatement(
            f"could not open {path} as a PDF: {exc}. If it is encrypted, pass the "
            f"password; if it is a scan, it needs OCR, which this does not attempt"
        ) from exc

    if not candidates:
        raise UnreadableStatement(
            f"no text could be extracted from {path}; if it is a scan rather than "
            f"a generated PDF it needs OCR, which this does not attempt"
        )

    best: Statement | None = None
    for name, rows in candidates:
        try:
            attempt = parse_rows(rows)
        except UnreadableStatement:
            continue
        attempt.strategy = name
        if attempt.reconciles:
            return attempt
        if best is None or _score(attempt) > _score(best):
            best = attempt

    if best is None:
        raise UnreadableStatement(
            f"{len(candidates)} extraction strategies ran on {path} and none "
            f"produced anything with a header and a date column"
        )
    return best


def _score(statement: Statement) -> tuple[int, int, int]:
    return (
        statement.verifiable,
        len(statement.columns),
        len(statement.rows),
    )


def _ruled_tables(page: PdfPage) -> list[list[str]]:
    return _tables(page, {"vertical_strategy": "lines", "horizontal_strategy": "lines"})


def _text_tables(page: PdfPage) -> list[list[str]]:
    return _tables(page, {"vertical_strategy": "text", "horizontal_strategy": "text"})


def _tables(page: PdfPage, settings: dict[str, str]) -> list[list[str]]:
    try:
        tables = page.extract_tables(settings)
    except Exception:  # noqa: BLE001 -- a failed strategy is not an error
        return []
    return [
        [(cell or "").replace("\n", " ").strip() for cell in row]
        for table in tables or []
        for row in table
        if row
    ]


def _rows_from_words(page: PdfPage, line_tolerance: float = 2.5) -> list[list[str]]:
    try:
        words = page.extract_words(use_text_flow=False)
    except Exception:  # noqa: BLE001
        return []
    if not words:
        return []

    lines: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - word["top"]) <= line_tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])

    anchors = _column_anchors(lines)
    if not anchors:
        return []

    rows: list[list[str]] = []
    for line in lines:
        cells = [""] * len(anchors)
        for word in line:
            i = _nearest_column(word, anchors)
            cells[i] = f"{cells[i]} {word['text']}".strip() if cells[i] else word["text"]
        rows.append(cells)
    return rows


def _column_anchors(lines: list[list[dict[str, Any]]], gap: float = 12.0) -> list[float]:
    best: tuple[int, list[dict[str, Any]]] = (0, [])
    for line in lines[:40]:
        text = [_norm(w["text"]) for w in line]
        hits = sum(
            1
            for _role, words in _ROLES.items()
            if any(cell.startswith(w) or cell == w for cell in text for w in words)
        )
        if hits > best[0]:
            best = (hits, line)
    if best[0] < 3:
        return []

    anchors: list[float] = []
    previous_right = None
    for word in sorted(best[1], key=lambda w: w["x0"]):
        if previous_right is None or word["x0"] - previous_right > gap:
            anchors.append(word["x0"])
        previous_right = word["x1"]
    return anchors


def _nearest_column(word: dict[str, Any], anchors: list[float]) -> int:
    index = 0
    for i, anchor in enumerate(anchors):
        if word["x0"] >= anchor - 4:
            index = i
    return index


_STRATEGIES: list[tuple[str, Callable[[PdfPage], list[list[str]]]]] = [
    ("ruled tables", _ruled_tables),
    ("word coordinates", _rows_from_words),
    ("text tables", _text_tables),
]


def load_any(path: str | Path, password: str | None = None) -> Statement:
    path = Path(path)
    if path.suffix.lower() == ".pdf" or path.read_bytes()[:5] == b"%PDF-":
        return parse_pdf(path, password=password)
    return load(path)
