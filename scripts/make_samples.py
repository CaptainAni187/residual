from __future__ import annotations

import csv
import io
import json
import random
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "samples"
FIXTURES = ROOT / "tests" / "fixtures"
GATEWAY_GSTIN = "29AAGCR4375J1ZU"
MERCHANT_GSTIN = "29AACCA1234M1ZX"
START = date(2026, 3, 2)
DAYS = 21
RATES = "card=2.00,upi=0,netbanking=2.00,wallet=2.00,emi=2.00"


@dataclass
class Scenario:
    key: str
    title: str
    story: str
    seed: int
    card_rate: str = "2.00"
    lost: tuple[int, ...] = ()
    refunds: int = 3
    route: bool = False
    adjustment: int = 0
    rows: list[dict] = field(default_factory=list)
    batches: dict[str, dict] = field(default_factory=dict)


SCENARIOS = [
    Scenario("clean", "Clean three weeks", "Every payout arrives and every fee matches the contract.", 11),
    Scenario("missing", "A payout goes missing", "One payout is sent by the gateway but never reaches the bank.", 22, lost=(9,)),
    Scenario("overcharged", "Card fees above contract", "Card payments are billed at 2.36% against a 2.00% contract.", 33,
             card_rate="2.36", refunds=5, route=True, adjustment=1),
]


def paise(rupees: float | Decimal) -> int:
    return int((Decimal(str(rupees)) * 100).quantize(Decimal(1), ROUND_HALF_UP))


def pct(amount: int, rate: str) -> int:
    return int((Decimal(amount) * Decimal(rate) / 100).quantize(Decimal(1), ROUND_HALF_UP))


def epoch(d: date, hour: int = 11) -> int:
    return int(datetime(d.year, d.month, d.day, hour, tzinfo=UTC).timestamp())


def bank_days_after(d: date, n: int) -> date:
    out = d
    while n:
        out += timedelta(days=1)
        if out.weekday() < 5:
            n -= 1
    return out


def inr(p: int) -> str:
    whole, frac = divmod(abs(p), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail, groups = digits[:-3], digits[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return f"{digits}.{frac:02d}"


def build(s: Scenario) -> None:
    rnd = random.Random(s.seed)
    end = START + timedelta(days=DAYS - 1)
    n = 0
    for offset in range(DAYS):
        day = START + timedelta(days=offset)
        settles = bank_days_after(day, 2)
        sid = f"setl_{s.key[:3]}{day:%m%d}"
        utr = f"{day:%Y%m%d}{rnd.randrange(100000, 999999)}"
        is_settled = settles <= end
        for _ in range(rnd.randint(5, 9) if day.weekday() < 5 else rnd.randint(2, 4)):
            n += 1
            method = rnd.choices(["upi", "card", "netbanking"], [60, 25, 15])[0]
            amount = paise(rnd.choice([299, 499, 799, 1299, 1999, 2499, 4999, 7499, 12999]) + rnd.randint(0, 99))
            rate = {"upi": "0", "card": s.card_rate, "netbanking": "2.00"}[method]
            fee = pct(amount, rate)
            tax = pct(fee, "18")
            s.rows.append({
                "entity_id": f"pay_{s.key[:3]}{n:04d}", "type": "payment", "amount": amount,
                "debit": 0, "credit": amount - fee - tax, "currency": "INR", "fee": fee, "tax": tax,
                "method": method, "order_id": f"order_{s.key[:3]}{n:04d}", "created_at": epoch(day),
                "settled": is_settled, "on_hold": False,
                "settlement_id": sid if is_settled else None,
                "settlement_utr": utr if is_settled else None,
                "settled_at": epoch(settles, 6) if is_settled else None,
                "description": None, "payment_id": None,
            })
        if is_settled:
            s.batches[sid] = {"utr": utr, "on": settles, "index": len(s.batches)}

    payments = [r for r in s.rows if r["settlement_id"]]
    for i in range(s.refunds):
        paid = payments[rnd.randrange(len(payments))]
        back = paid["amount"] // (2 if i % 2 else 1)
        day = datetime.fromtimestamp(paid["created_at"], tz=UTC).date() + timedelta(days=1)
        sid = f"setl_{s.key[:3]}{day:%m%d}"
        if sid not in s.batches:
            continue
        s.rows.append({
            "entity_id": f"rfnd_{s.key[:3]}{i:03d}", "type": "refund", "amount": back, "debit": back,
            "credit": 0, "currency": "INR", "fee": 0, "tax": 0, "created_at": epoch(day, 15),
            "settled": True, "on_hold": False, "settlement_id": sid,
            "settlement_utr": s.batches[sid]["utr"], "settled_at": epoch(s.batches[sid]["on"], 6),
            "payment_id": paid["entity_id"], "description": None, "method": paid["method"],
        })
    if s.route:
        paid = payments[3]
        cut = paid["credit"] // 5
        sid = paid["settlement_id"]
        s.rows.append({
            "entity_id": f"trf_{s.key[:3]}001", "type": "transfer", "amount": cut, "debit": cut,
            "credit": 0, "currency": "INR", "fee": 0, "tax": 0, "created_at": paid["created_at"],
            "settled": True, "on_hold": False, "settlement_id": sid, "settlement_utr": paid["settlement_utr"],
            "settled_at": paid["settled_at"], "payment_id": paid["entity_id"], "description": "acc_partner_store",
        })
    for i in range(s.adjustment):
        paid = payments[-5]
        s.rows.append({
            "entity_id": f"adj_{s.key[:3]}{i:03d}", "type": "adjustment", "amount": 35000, "debit": 35000,
            "credit": 0, "currency": "INR", "fee": 0, "tax": 0, "created_at": paid["created_at"],
            "settled": True, "on_hold": False, "settlement_id": paid["settlement_id"],
            "settlement_utr": paid["settlement_utr"], "settled_at": paid["settled_at"],
            "description": "chargeback recovery fee", "payment_id": None,
        })
    s.rows.sort(key=lambda r: (r["created_at"], r["entity_id"]))


def statement(s: Scenario) -> str:
    end = START + timedelta(days=DAYS + 2)
    nets: dict[str, int] = {}
    for r in s.rows:
        if r["settlement_id"]:
            nets[r["settlement_id"]] = nets.get(r["settlement_id"], 0) + r["credit"] - r["debit"]
    moves: list[tuple[date, str, str, int, int]] = []
    for sid, batch in s.batches.items():
        if batch["index"] in s.lost or nets.get(sid, 0) <= 0:
            continue
        moves.append((batch["on"], f"NEFT CR-YESB0000001-RAZORPAY SOFTWARE PVT LTD-{batch['utr']}", batch["utr"], 0, nets[sid]))
    moves += [
        (START + timedelta(days=3), "ACCT MAINT CHRG INCL GST", "", 59000, 0),
        (START + timedelta(days=8), "UPI-PACKAGING SUPPLIES-9845XXXXX@okaxis", "000012345678", 1850000, 0),
        (START + timedelta(days=15), "RENT MAR 2026 NEFT", "RNT0326", 4500000, 0),
    ]
    moves.sort(key=lambda m: (m[0], m[3] > 0))
    out = io.StringIO()
    out.write(
        "Account Branch : INDIRANAGAR\nAddress : 100 FEET ROAD BENGALURU 560038\nCity : BENGALURU\n"
        f"Account No : 50100XXXXXX{s.seed:04d}\nStatement From : {START:%d/%m/%Y} To : {end:%d/%m/%Y}\n\n"
    )
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"])
    balance = paise(250000)
    for on, narration, ref, out_p, in_p in moves:
        balance += in_p - out_p
        w.writerow([f"{on:%d/%m/%y}", narration, ref, f"{on:%d/%m/%y}",
                    inr(out_p) if out_p else "", inr(in_p) if in_p else "", inr(balance)])
    out.write("*** End of Statement ***\n")
    return out.getvalue()


def gstr2b(s: Scenario, keep_weeks: int = 3, bogus: bool = False) -> dict:
    invoices = []
    for week in range(3):
        lo, hi = START + timedelta(days=7 * week), START + timedelta(days=7 * week + 6)
        fees = [r for r in s.rows if r["type"] == "payment" and lo <= datetime.fromtimestamp(r["created_at"], tz=UTC).date() <= hi]
        fee, tax = sum(r["fee"] for r in fees), sum(r["tax"] for r in fees)
        if week >= keep_weeks or not tax:
            continue
        half = tax // 2
        invoices.append({
            "inum": f"RZP/2526/03/{s.seed:02d}{week:03d}", "dt": f"{hi:%d-%m-%Y}",
            "val": inr(fee + tax).replace(",", ""),
            "itms": [{"itm_det": {"txval": inr(fee).replace(",", ""), "cgst": inr(half).replace(",", ""),
                                   "sgst": inr(tax - half).replace(",", ""), "igst": "0.00", "cess": "0.00"}}],
        })
    b2b = [{"ctin": GATEWAY_GSTIN, "trdnm": "RAZORPAY SOFTWARE PRIVATE LIMITED", "inv": invoices}]
    if bogus:
        b2b.append({"ctin": "29XXXXX9999X9X9", "trdnm": "UNVERIFIED VENDOR", "inv": [{
            "inum": "UV-0412", "dt": "18-03-2026", "val": "11800.00",
            "itms": [{"itm_det": {"txval": "10000.00", "cgst": "900.00", "sgst": "900.00", "igst": "0.00", "cess": "0.00"}}],
        }]})
    return {"data": {"rtnprd": "032026", "gstin": MERCHANT_GSTIN, "docdata": {"b2b": b2b}}}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for s in SCENARIOS:
        build(s)
        (OUT / f"razorpay_report_{s.key}.json").write_text(json.dumps({"entity": "collection", "count": len(s.rows), "items": s.rows}, indent=1) + "\n")
        (OUT / f"bank_statement_{s.key}.csv").write_text(statement(s))
    clean = SCENARIOS[0]
    (OUT / "gstr2b_all_claimable.json").write_text(json.dumps(gstr2b(clean), indent=1) + "\n")
    (OUT / "gstr2b_one_week_missing.json").write_text(json.dumps(gstr2b(clean, keep_weeks=2), indent=1) + "\n")
    (OUT / "gstr2b_bad_supplier.json").write_text(json.dumps(gstr2b(clean, bogus=True), indent=1) + "\n")
    for name in ("icici.csv", "sbi.csv", "axis.csv", "hdfc.pdf", "hdfc_locked.pdf"):
        shutil.copy(FIXTURES / "statements" / name, OUT / f"statement_{name}")
    report = lambda k: f"razorpay_report_{k}.json"
    sets = {
        s.key: {"title": s.title, "story": s.story,
                "files": {"recon": report(s.key), "statement": f"bank_statement_{s.key}.csv"}}
        for s in SCENARIOS
    }
    sets |= {
        "gst_all": {"title": "All credit claimable", "story": "Every rupee of GST on fees shows up in GSTR-2B.",
                    "files": {"recon": report("clean"), "gstr2b": "gstr2b_all_claimable.json"}},
        "gst_week": {"title": "A week of invoices missing", "story": "The gateway has not filed the last week's invoice yet.",
                     "files": {"recon": report("clean"), "gstr2b": "gstr2b_one_week_missing.json"}},
        "gst_bad": {"title": "A supplier with a bad GSTIN", "story": "Gateway credit is fine, but a ₹1,800 vendor invoice carries a GSTIN that does not validate.",
                    "files": {"recon": report("clean"), "gstr2b": "gstr2b_bad_supplier.json"}},
        "st_hdfc": {"title": "HDFC, CSV", "story": "Three weeks of payouts, rent and vendor payments.",
                    "files": {"statement": "bank_statement_clean.csv"}},
        "st_icici": {"title": "ICICI, CSV", "story": "A different bank's column layout.", "files": {"statement": "statement_icici.csv"}},
        "st_sbi": {"title": "SBI, CSV", "story": "SBI's layout, with a Debit and Credit column.", "files": {"statement": "statement_sbi.csv"}},
        "st_axis": {"title": "Axis, CSV", "story": "Axis prints DR/CR beside one amount column.", "files": {"statement": "statement_axis.csv"}},
        "st_pdf": {"title": "HDFC, PDF", "story": "A PDF statement, layout detected automatically.", "files": {"statement": "statement_hdfc.pdf"}},
        "st_locked": {"title": "Password-protected PDF", "story": "Locked like a real bank statement. Password: HDFC1234",
                      "files": {"statement": "statement_hdfc_locked.pdf"}, "password": "HDFC1234"},
    }
    trio = [s.key for s in SCENARIOS]
    manifest = {
        "rates": RATES,
        "sets": sets,
        "tools": {"reconcile": trio, "missing": trio, "fees": trio, "dashboard": trio, "ask": trio,
                  "gst": ["gst_all", "gst_week", "gst_bad"],
                  "statement": ["st_hdfc", "st_icici", "st_sbi", "st_axis", "st_pdf", "st_locked"]},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print("\n".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
