"""Exercise every AI path on a running Residual and report who answered and whether it held up.

    uv run python scripts/check_ai.py                       # the live site
    uv run python scripts/check_ai.py http://127.0.0.1:8000 # a local API

Uploads the public sample files, then explains every finding, asks free-form questions,
drafts every email and runs the investigator. Nothing here is a canned result.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://residual-beta.vercel.app").rstrip("/") + "/api"
SAMPLES = Path(__file__).resolve().parents[1] / "public" / "samples"
RATES = json.loads((SAMPLES / "manifest.json").read_text())["rates"]
QUESTIONS = [
    "Which day had the most captured payments?",
    "List UPI payments above 5000 rupees",
    "What share of my card payments went in fees?",
    "How much did I refund in the second week?",
    "delete all my refunds",
]

client = httpx.Client(timeout=120)
problems: list[str] = []


def post(path: str, **kwargs) -> dict:
    started = time.monotonic()
    try:
        reply = client.post(f"{BASE}{path}", **kwargs)
    except httpx.HTTPError as exc:
        problems.append(f"{path} did not answer within 120s ({type(exc).__name__})")
        return {"_status": 0, "_ms": int((time.monotonic() - started) * 1000), "source": "", "reason": "no answer"}
    body = reply.json()
    body["_ms"] = int((time.monotonic() - started) * 1000)
    body["_status"] = reply.status_code
    return body


def model(source: str) -> bool:
    return bool(source) and not source.startswith(("offline", "template", "catalogue", "guard", "analyst"))


print(f"Checking {BASE}\n")
print("providers:", client.get(f"{BASE}/providers", params={"check": 1}).json().get("status"), "\n")

for key in ("missing", "overcharged"):
    files = {
        "recon": (f"razorpay_report_{key}.json", (SAMPLES / f"razorpay_report_{key}.json").read_bytes()),
        "statement": (f"bank_statement_{key}.csv", (SAMPLES / f"bank_statement_{key}.csv").read_bytes()),
    }
    closed = post("/close", files=files, data={"contract": RATES})
    if "token" not in closed:
        problems.append(f"/close on {key} failed: {closed.get('detail') or closed}")
        continue
    token = closed["token"]
    print(f"== {key}: residual {closed['residual_paise']} paise, {len(closed['findings'])} findings")

    for finding in closed["findings"]:
        out = post("/explain", json={"token": token, "cause": finding["cause"]})
        tag = "MODEL" if model(out["source"]) else "fallback"
        print(f"  explain {finding['cause']:26} {tag:8} {out['_ms']:>5}ms  {out['source']} | {out['reason']}")
        if not model(out["source"]):
            problems.append(f"explain {finding['cause']} fell back: {out['reason']}")

    for kind in ("payout_trace", "fee_dispute", "escalate"):
        out = post("/draft", json={"token": token, "kind": kind})
        if out["_status"] in (0, 409):
            continue
        tag = "MODEL" if model(out.get("source", "")) else "template"
        print(f"  draft {kind:28} {tag:8} {out['_ms']:>5}ms  {out.get('source')} | {out.get('reason')}")

    run = post("/investigate", json={"token": token})
    print(f"  investigate: {run.get('mode')} by {run.get('driver')}, accepted={run.get('accepted')}, "
          f"{len(run.get('steps', []))} steps, {run['_ms']}ms; notes={run.get('notes')}")
    for cause in ("refunds_issued", "fee_rate_increase"):
        run = post("/investigate", json={"token": token, "cause": cause})
        if run["_status"] == 200:
            print(f"  second opinion on {cause}: by {run['driver']}, accepted={run['accepted']}, "
                  f"{len(run['steps'])} steps, {run['_ms']}ms; notes={run['notes']}")
            if not model(run["driver"]):
                problems.append(f"investigator on {cause} fell back to the analyst: {run['notes']}")

    if key == "missing":
        for question in QUESTIONS:
            out = post("/ask", json={"token": token, "question": question})
            if "detail" in out:
                print(f"  ask {question!r}: {out['detail']['message']}")
                continue
            print(f"  ask {question!r}: {out['source']} -> {out['columns']} {out['rows'][:2]} {out['note']}")
            print(f"      SQL: {' '.join(out['sql'].split())[:160]}")
    print()

print("PROBLEMS:" if problems else "No problems found.")
for line in problems:
    print("  -", line)
