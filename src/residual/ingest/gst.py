
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from pathlib import Path

from residual.ledger.money import Money

GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


class UnreadableReturn(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Invoice:
    supplier_gstin: str
    supplier_name: str
    invoice_no: str
    invoice_date: date
    taxable_value: Money
    igst: Money
    cgst: Money
    sgst: Money
    cess: Money = field(default_factory=Money.zero)

    @property
    def total_tax(self) -> Money:
        return self.igst + self.cgst + self.sgst + self.cess

    @property
    def gstin_is_valid(self) -> bool:
        return bool(GSTIN.match(self.supplier_gstin or ""))


@dataclass(slots=True)
class Return:
    period: str = ""
    invoices: list[Invoice] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def from_supplier(self, gstin: str) -> list[Invoice]:
        return [i for i in self.invoices if i.supplier_gstin.upper() == gstin.upper()]

    def credit_from(self, gstin: str) -> Money:
        out = Money.zero()
        for invoice in self.from_supplier(gstin):
            out = out + invoice.total_tax
        return out

    @property
    def malformed_gstins(self) -> list[Invoice]:
        return [i for i in self.invoices if not i.gstin_is_valid]


def _money(value: object) -> Money:
    if value in (None, "", "-"):
        return Money.zero()
    text = str(value).replace(",", "").replace("₹", "").strip()
    try:
        return Money.parse(text)
    except (ValueError, ArithmeticError):
        return Money.zero()


def _date(text: str) -> date | None:
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(str(text).strip(), fmt).date()  # noqa: DTZ007
        except (ValueError, TypeError):
            continue
    return None


def parse_json(text: str) -> Return:
    payload = json.loads(text)
    data = payload.get("data", payload)
    out = Return(period=str(data.get("rtnprd", "")))

    for supplier in data.get("docdata", {}).get("b2b", []):
        gstin = supplier.get("ctin", "")
        name = supplier.get("trdnm", "")
        for inv in supplier.get("inv", []):
            when = _date(inv.get("dt", ""))
            if when is None:
                out.skipped.append(f"{gstin}/{inv.get('inum')}: unreadable date")
                continue
            igst = cgst = sgst = cess = Money.zero()
            taxable = Money.zero()
            for item in inv.get("itms", []):
                det = item.get("itm_det", item)
                taxable = taxable + _money(det.get("txval"))
                igst = igst + _money(det.get("igst"))
                cgst = cgst + _money(det.get("cgst"))
                sgst = sgst + _money(det.get("sgst"))
                cess = cess + _money(det.get("cess"))
            out.invoices.append(
                Invoice(gstin, name, str(inv.get("inum", "")), when,
                        taxable, igst, cgst, sgst, cess)
            )
    return out


_COLUMNS = {
    "supplier_gstin": ("gstinofsupplier", "suppliergstin", "ctin", "gstin"),
    "supplier_name": ("tradelegalname", "suppliername", "tradename", "legalname"),
    "invoice_no": ("invoicenumber", "invoiceno", "documentnumber", "inum"),
    "invoice_date": ("invoicedate", "documentdate", "date"),
    "taxable_value": ("taxablevalue", "taxablevaluers", "txval"),
    "igst": ("integratedtaxrs", "integratedtax", "igst"),
    "cgst": ("centraltaxrs", "centraltax", "cgst"),
    "sgst": (
        "stateutterritorytaxrs", "stateutterritorytax", "stateuttaxrs",
        "stateuttax", "statetaxrs", "statetax", "sgst", "sgstutgst",
    ),
    "cess": ("cessrs", "cess"),
}


def _norm(cell: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (cell or "").lower())


def parse_csv(text: str) -> Return:
    return parse_rows(list(csv.reader(io.StringIO(text))))


def parse_bytes(blob: bytes, name: str = "the return") -> Return:
    """GSTR-2B as the portal hands it out: JSON, the Excel workbook, or a CSV saved from it."""
    from residual.ingest import bank

    head = blob[:8].lstrip()
    if head.startswith(b"PK\x03\x04"):
        return parse_rows(_b2b_sheet(blob, name))
    text = bank.decode(blob)
    if text.lstrip().startswith(("{", "[")):
        try:
            return parse_json(text)
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
            raise UnreadableReturn(f"{name} is JSON but not a GSTR-2B download") from exc
    return parse_csv(text)


def _b2b_sheet(blob: bytes, name: str) -> list[list[str]]:
    import openpyxl

    from residual.ingest.bank import _cell_text

    try:
        book = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception as exc:
        raise UnreadableReturn(f"could not open {name} as an Excel workbook") from exc
    sheets = [ws for ws in book.worksheets if _norm(ws.title) == "b2b"] or list(book.worksheets)
    return [[_cell_text(v) for v in row] for ws in sheets for row in ws.iter_rows(values_only=True)]


def _roles(row: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for col, cell in enumerate(row):
        key = _norm(cell)
        for role, words in _COLUMNS.items():
            if role not in found and key in words:
                found[role] = col
    return found


def parse_rows(rows: list[list[str]]) -> Return:
    header_at = -1
    mapping: dict[str, int] = {}
    for i, row in enumerate(rows[:30]):
        found = _roles(row)
        if len(found) > len(mapping):
            header_at, mapping = i, found
        # The portal's workbook splits its header over two rows ("Invoice details" over
        # "Invoice number", "Tax amount" over "Integrated Tax"); read them as one.
        if i + 1 < len(rows):
            below = rows[i + 1]
            joined = [
                (below[k] if k < len(below) and below[k].strip() else cell) for k, cell in enumerate(row)
            ] + below[len(row):]
            found = _roles(joined)
            if len(found) > len(mapping):
                header_at, mapping = i + 1, found
    if "supplier_gstin" not in mapping or "invoice_no" not in mapping:
        raise UnreadableReturn(
            f"no GSTR-2B header found; best guess had columns {sorted(mapping)}"
        )

    def field(row: list[str], role: str) -> str:
        idx = mapping.get(role)
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    out = Return()
    for row in rows[header_at + 1:]:
        get = partial(field, row)

        when = _date(get("invoice_date"))
        if when is None or not get("supplier_gstin"):
            if any(c.strip() for c in row):
                out.skipped.append((get("invoice_no") or row[0])[:40])
            continue
        out.invoices.append(
            Invoice(
                get("supplier_gstin"), get("supplier_name"), get("invoice_no"), when,
                _money(get("taxable_value")), _money(get("igst")),
                _money(get("cgst")), _money(get("sgst")), _money(get("cess")),
            )
        )
    return out


def load(path: str | Path) -> Return:
    text = Path(path).read_text(encoding="utf-8-sig")
    if text.lstrip().startswith(("{", "[")):
        try:
            return parse_json(text)
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            pass
    try:
        return parse_csv(text)
    except UnreadableReturn:
        raise UnreadableReturn(
            f"{path} is neither a GSTR-2B JSON download nor a recognisable CSV export"
        ) from None
