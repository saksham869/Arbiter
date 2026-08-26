"""
data/seed.py
Seeds 250 realistic orders into Razorpay test mode.
Splits 80/20 into train/holdout before affinity model sees anything.
Run: python3 data/seed.py
"""
import csv
import json
import os
import random
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
BASE_URL   = "https://api.razorpay.com/v1"
AUTH       = (KEY_ID, KEY_SECRET)

random.seed(42)   # reproducible

# ── Load catalog ──────────────────────────────────────────────
catalog = {}
with open("data/catalog.csv") as f:
    for row in csv.DictReader(f):
        catalog[row["sku"]] = {
            "price_paise": int(row["price_paise"]),
            "cogs_paise":  int(row["cogs_paise"]),
            "category":    row["category"],
            "name":        row["name"],
        }

# ── Co-purchase patterns (realistic, not deterministic) ───────
# Primary item → likely companions with probability
PATTERNS = {
    "SHOE-001": [("SOCK-3PK", 0.55), ("INSOLE-1", 0.30), ("BOTTLE-1", 0.20)],
    "SHOE-002": [("SOCK-3PK", 0.55), ("INSOLE-1", 0.28), ("CAP-1",    0.18)],
    "SHOE-003": [("SOCK-1PK", 0.45), ("BOTTLE-1", 0.35), ("BAND-1",   0.22)],
    "SHIRT-1":  [("SHORTS-1", 0.50), ("CAP-1",    0.30)],
    "SHORTS-1": [("SHIRT-1",  0.45), ("CAP-1",    0.25)],
    "MAT-1":    [("BAND-1",   0.55), ("FOAM-1",   0.40)],
    "WATCH-1":  [("EARPHONE-1", 0.45)],
    "FOAM-1":   [("BAND-1",   0.50), ("MAT-1",    0.35)],
}

PRIMARY_SKUS = list(PATTERNS.keys()) + ["SOCK-3PK", "BOTTLE-1", "CAP-1"]
PRIMARY_WEIGHTS = [0.18, 0.16, 0.10, 0.08, 0.07, 0.08,
                   0.06, 0.07, 0.06, 0.05, 0.05, 0.04]


def make_basket() -> list[dict]:
    primary = random.choices(PRIMARY_SKUS, weights=PRIMARY_WEIGHTS[:len(PRIMARY_SKUS)], k=1)[0]
    items   = [{"sku": primary, "quantity": 1}]

    for companion, prob in PATTERNS.get(primary, []):
        if random.random() < prob:
            items.append({"sku": companion, "quantity": 1})

    return items


def seed_order(basket: list[dict]) -> dict:
    total = sum(catalog[i["sku"]]["price_paise"] * i["quantity"] for i in basket)

    notes = {
        "mg_v":     "1",
        "mg_items": json.dumps([
            {"s": i["sku"], "q": i["quantity"],
             "p": catalog[i["sku"]]["price_paise"]}
            for i in basket
        ]),
        "mg_ship": "0",
        "mg_cat":  catalog[basket[0]["sku"]]["category"],
    }

    resp = httpx.post(
        f"{BASE_URL}/orders",
        auth=AUTH,
        json={"amount": total, "currency": "INR",
              "receipt": f"mg_seed_{int(time.time()*1000)}",
              "notes": notes},
        timeout=15.0,
    )

    if resp.status_code == 200:
        order = resp.json()
        return {
            "order_id":    order["id"],
            "amount":      total,
            "basket":      basket,
            "rzp_receipt": order.get("receipt", ""),
        }
    else:
        print(f"  WARN: order failed {resp.status_code}: {resp.text[:100]}")
        return {}


# ── Main ──────────────────────────────────────────────────────
print("Seeding 250 orders into Razorpay test mode...")
orders = []

for i in range(250):
    basket = make_basket()
    result = seed_order(basket)
    if result:
        orders.append(result)
    if (i + 1) % 25 == 0:
        print(f"  {i+1}/250 done")
    time.sleep(0.15)   # stay well under rate limit

print(f"\nCreated {len(orders)} orders")

# ── Split 80/20 ───────────────────────────────────────────────
random.shuffle(orders)
split      = int(len(orders) * 0.8)
train      = orders[:split]
holdout    = orders[split:]

with open("data/orders_train.json", "w") as f:
    json.dump(train, f, indent=2)

with open("data/orders_holdout.json", "w") as f:
    json.dump(holdout, f, indent=2)

print(f"Train:   {len(train)} orders  → data/orders_train.json")
print(f"Holdout: {len(holdout)} orders → data/orders_holdout.json")
print("\nDone. Safe to run agent.")
