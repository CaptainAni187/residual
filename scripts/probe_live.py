from __future__ import annotations

import json
import pathlib
import sys

import httpx

from residual import config
from residual.ingest.razorpay import _credentials

WRITE = "--write" in sys.argv
CHECKOUT = "--checkout" in sys.argv
OUT = "scripts/checkout"

PAGE = """<!doctype html>
<html><head><meta charset=utf-8><title>Residual test payments</title>
<script src="config.js"></script>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<style>
html,body{background:#fff;color:#111}
body{font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 20px}
h1{font-size:19px;margin-bottom:4px} p{color:#555}
.row{background:#fff;display:flex;justify-content:space-between;align-items:center;
border:1px solid #e2e2e2;border-radius:8px;padding:14px 16px;margin:10px 0}
.id{font:12px ui-monospace,Menlo,monospace;color:#888}
button{background:#0b5cff;color:#fff;border:0;border-radius:6px;padding:9px 20px;font-size:14px;cursor:pointer}
#out{margin-top:22px;font:13px ui-monospace,Menlo,monospace;white-space:pre-wrap;color:#0a7d28}
</style></head><body>
<h1>Residual &mdash; test mode payments</h1>
<p>Test mode. No real money.<br>Choose <b>Netbanking</b>, pick any bank, then click
<b>Success</b> on the simulated bank page. No card or credentials needed.</p>
__ROWS__
<div id=out></div>
<script>
function pay(orderId, amount) {
  new Razorpay({
    key: window.RZP_KEY, order_id: orderId, amount: amount, currency: "INR",
    name: "Residual", description: "Reconciliation test",
    handler: function (r) {
      document.getElementById("out").textContent += "paid " + r.razorpay_payment_id + "\\n";
    }
  }).open();
}
</script></body></html>
"""

READS = [
    "/v1/payments?count=5",
    "/v1/orders?count=5",
    "/v1/settlements?count=5",
    "/v1/refunds?count=5",
    "/v1/disputes?count=5",
]


def main() -> None:
    config.load()
    key_id, secret = _credentials()
    client = httpx.Client(auth=(key_id, secret), base_url="https://api.razorpay.com", timeout=30)
    print(f"account {key_id}\n")

    for path in READS:
        response = client.get(path)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        note = body.get("count", body.get("error", {}).get("description", ""))
        print(f"  {response.status_code}  {path:28} -> {note}")

    if CHECKOUT:
        existing = client.get("/v1/orders?count=20").json().get("items", [])
        unpaid = [o for o in existing if o.get("status") == "created"]
        if not unpaid:
            print("\nno unpaid orders. run with --write first.")
            return
        print()
        write_checkout_page(key_id, unpaid)
        return

    if not WRITE:
        print("\nread only. --write creates a test order, --checkout builds the pay page.")
        return

    print("\ncreating one test order")
    order = client.post(
        "/v1/orders",
        json={"amount": 50000, "currency": "INR", "receipt": "residual-probe-1"},
    )
    print(f"  POST /v1/orders -> {order.status_code}")
    print("  " + json.dumps(order.json())[:300])
    if order.status_code >= 400:
        return

    print("\ntrying a server-side payment (expected to fail unless S2S is enabled)")
    payment = client.post(
        "/v1/payments/create/upi",
        json={
            "amount": 50000,
            "currency": "INR",
            "order_id": order.json()["id"],
            "email": "test@example.com",
            "contact": "9999999999",
            "method": "upi",
            "upi": {"flow": "collect", "vpa": "success@razorpay"},
        },
    )
    print(f"  POST /v1/payments/create/upi -> {payment.status_code}")
    print("  " + json.dumps(payment.json())[:300])


def write_checkout_page(key_id: str, orders: list[dict]) -> None:
    out = pathlib.Path(OUT)
    out.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(
        f'<div class=row><div><b>INR {o["amount"] / 100:,.2f}</b><br>'
        f'<span class=id>{o["id"]}</span></div>'
        f'<button onclick="pay(\'{o["id"]}\', {o["amount"]})">Pay</button></div>'
        for o in orders
    )
    (out / "config.js").write_text(f'window.RZP_KEY = "{key_id}";\n')
    (out / "index.html").write_text(PAGE.replace("__ROWS__", rows))
    print(f"  wrote {OUT}/index.html for {len(orders)} order(s)")
    print(f"  serve it and pay in your own browser:\n    python3 -m http.server 8777 --directory {OUT}")


if __name__ == "__main__":
    main()
