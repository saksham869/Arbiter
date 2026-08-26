"""
data/probe.py
Day 1 probe. Tests what actually works on your rzp_test_ account.
Writes findings to docs/constraints.md.
Run: python3 data/probe.py
"""

import json
import os
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv()

KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
BASE_URL   = "https://api.razorpay.com/v1"

if not KEY_ID or not KEY_SECRET:
    raise SystemExit("ERROR: Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env")

auth    = (KEY_ID, KEY_SECRET)
results = {}

# ── PROBE A: notes channel ─────────────────────────────────────
print("\n[PROBE A] notes channel — must pass for the project to work")

resp = httpx.post(
    f"{BASE_URL}/orders",
    auth=auth,
    json={
        "amount": 114900,
        "currency": "INR",
        "receipt": "mg_probe_notes_1",
        "notes": {
            "mg_v":     "1",
            "mg_items": '[{"s":"SHOE-001","q":1,"p":100000},{"s":"SOCK-3PK","q":1,"p":34900}]',
            "mg_ship":  "0",
            "mg_cat":   "footwear",
        },
    },
)
print(f"  create_order status: {resp.status_code}")

if resp.status_code == 200:
    order    = resp.json()
    order_id = order["id"]
    print(f"  order_id: {order_id}")

    fetch      = httpx.get(f"{BASE_URL}/orders/{order_id}", auth=auth)
    fetched    = fetch.json()
    notes_back = fetched.get("notes", {})
    has_mg     = "mg_items" in notes_back

    print(f"  notes round-trip: {'PASS' if has_mg else 'FAIL'}")
    results["notes_channel"] = "PASS" if has_mg else "FAIL — notes did not round-trip"
else:
    print(f"  FAIL: {resp.text}")
    results["notes_channel"] = f"FAIL — {resp.status_code}"

# ── PROBE B: line_items ────────────────────────────────────────
print("\n[PROBE B] line_items — expected to need Magic Checkout")

resp = httpx.post(
    f"{BASE_URL}/orders",
    auth=auth,
    json={
        "amount": 114900,
        "currency": "INR",
        "receipt": "mg_probe_li_1",
        "line_items_total": "114900",
        "line_items": [{
            "type": "e-commerce",
            "sku": "SHOE-001",
            "name": "Running Shoes",
            "price": "100000",
            "quantity": 1,
        }],
    },
)
print(f"  status: {resp.status_code}")
if resp.status_code == 200:
    results["line_items"] = "PASS — Magic Checkout active on this account"
else:
    results["line_items"] = f"FAIL (expected) — needs Magic Checkout activation"

# ── PROBE C: fetch_all_orders ──────────────────────────────────
print("\n[PROBE C] fetch_all_orders — for affinity model")

resp = httpx.get(f"{BASE_URL}/orders", auth=auth, params={"count": 5})
print(f"  status: {resp.status_code}")
if resp.status_code == 200:
    count = resp.json().get("count", 0)
    print(f"  orders visible: {count}")
    results["fetch_all_orders"] = f"PASS — {count} orders visible"
else:
    results["fetch_all_orders"] = f"FAIL — {resp.status_code}"

# ── PROBE D: fetch_all_payments ────────────────────────────────
print("\n[PROBE D] fetch_all_payments — for conversion measurement")

resp = httpx.get(f"{BASE_URL}/payments", auth=auth, params={"count": 5})
print(f"  status: {resp.status_code}")
if resp.status_code == 200:
    results["fetch_all_payments"] = "PASS"
else:
    results["fetch_all_payments"] = f"FAIL — {resp.status_code}"

# ── Write constraints.md ───────────────────────────────────────
output = f"""# Constraints — Day 1 Probe Results
Generated: {datetime.now().isoformat()}

## Probe Results

| Probe | Result |
|---|---|
| A: notes channel | {results.get('notes_channel', 'NOT RUN')} |
| B: line_items | {results.get('line_items', 'NOT RUN')} |
| C: fetch_all_orders | {results.get('fetch_all_orders', 'NOT RUN')} |
| D: fetch_all_payments | {results.get('fetch_all_payments', 'NOT RUN')} |

## Architecture decisions

- Primary catalogue channel: notes (mg_v, mg_items, mg_ship, mg_cat)
- Offer mechanism: Dashboard pre-registration + offer_id on create_order
- Fallback if offers fail: vary amount directly

## Four known constraints

| # | Constraint | Design response |
|---|---|---|
| 1 | line_items needs Magic Checkout | notes channel is primary |
| 2 | offers param not in core API ref | probe with real offer_id after Dashboard setup |
| 3 | UPI Reserve Pay unavailable | create_order covers all upsell paths |
| 4 | MDR refundability ambiguous | config flag mdr_refundable=false, both scenarios reported |
"""

os.makedirs("docs", exist_ok=True)
with open("docs/constraints.md", "w") as f:
    f.write(output)

print("\n" + "="*50)
print("PROBE COMPLETE")
print("="*50)
for k, v in results.items():
    status = "✓" if "PASS" in v else "✗"
    print(f"  {status} {k}: {v}")
print("\nWritten to docs/constraints.md")

if "PASS" not in results.get("notes_channel", ""):
    print("\n⚠️  CRITICAL: notes channel failed. Investigate before Day 2.")
else:
    print("\n✓ notes channel works. Safe to proceed.")
