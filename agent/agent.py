"""
agent/agent.py
The AI growth agent.
LLM calls live here and ONLY here.
No direct Razorpay imports. Only talks to the control plane.
"""
from __future__ import annotations
import json
import os
import csv
import httpx
import anthropic
from dotenv import load_dotenv
from agent.affinity import AffinityModel

load_dotenv()

CONTROL_URL    = "http://localhost:8085"
ANTHROPIC_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
client         = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
CATALOG_PATH   = "./data/catalog.csv"
TRAIN_PATH     = "./data/orders_train.json"
HOLDOUT_PATH   = "./data/orders_holdout.json"


def load_catalog() -> dict:
    catalog = {}
    with open(CATALOG_PATH) as f:
        for row in csv.DictReader(f):
            catalog[row["sku"]] = {
                "price_paise": int(row["price_paise"]),
                "cogs_paise":  int(row["cogs_paise"]),
                "return_rate": float(row["return_rate"]),
                "name":        row["name"],
                "category":    row["category"],
            }
    return catalog


def load_orders(path: str) -> list:
    with open(path) as f:
        return json.load(f)


def get_ceiling(items: list, list_total: int) -> float:
    resp = httpx.post(
        f"{CONTROL_URL}/control/margin/ceiling",
        json={"items": items, "list_total_paise": list_total},
        timeout=10.0,
    )
    if resp.status_code == 200:
        return resp.json().get("max_discount_pct", 18.0)
    return 18.0


def llm_propose(order: dict, companions: list, catalog: dict,
                denial: dict = None) -> dict:
    """
    MOCK LLM — replace with real Claude when API credits available.
    Swap: remove this function body, uncomment the anthropic call below.
    The prompts in agent/prompts/ show exact inputs the LLM would receive.

    Mock deliberately shows STRATEGIC replanning, not just discount decrement:
    - Attempt 1: aggressive discount (likely to trigger DENY)
    - Attempt 2: different strategy — lower rung + cheaper companion
    """
    import random
    random.seed()
    primary_sku = order["basket"][0]["sku"]
    p_price     = catalog.get(primary_sku, {}).get("price_paise", 100000)

    # filter safe companions (return_rate <= 24%)
    safe = [
        c for c in companions
        if catalog.get(c["sku"], {}).get("return_rate", 0) <= 0.24
    ]

    if denial:
        # STRATEGIC REPLAN — not just a smaller number
        constraint = denial.get("constraint", {})
        max_d      = constraint.get("max_discount_pct", 18.0)
        reason     = denial.get("reason", "")

        if reason == "return_risk":
            # strategy change: pick a completely different companion
            alt_companions = [
                c for c in companions
                if catalog.get(c["sku"], {}).get("return_rate", 0) <= 0.10
            ]
            companion = alt_companions[0]["sku"] if alt_companions else (safe[0]["sku"] if safe else "SOCK-3PK")
            disc      = round(min(max_d - 2.0, 18.0), 1)
            rationale = (
                f"Previous companion had high return rate. "
                f"Switching to {companion} (low return risk) "
                f"at {disc}% — within the {max_d}% economic ceiling."
            )
        else:
            # margin floor — pick cheapest companion to improve margin
            safe_sorted = sorted(
                safe,
                key=lambda c: catalog.get(c["sku"], {}).get("cogs_paise", 999999)
            )
            companion = safe_sorted[0]["sku"] if safe_sorted else (safe[0]["sku"] if safe else "SOCK-3PK")
            disc      = round(min(max_d - 2.0, 18.0), 1)
            rationale = (
                f"Discount strategy violated margin floor. "
                f"Switching to lowest-COGS companion ({companion}) "
                f"at {disc}% — preserves objective while clearing the {max_d}% ceiling."
            )
    else:
        # first attempt: slightly aggressive to show the DENY loop
        companion = safe[0]["sku"] if safe else "SOCK-3PK"
        disc      = round(random.uniform(26, 30), 1)
        rationale = (
            f"Co-purchase affinity: {companion} appears in "
            f"{companions[0].get('affinity_score', 0)*100:.0f}% of {primary_sku} orders. "
            f"Bundle at {disc}% to drive AOV lift."
        )

    c_price = catalog.get(companion, {}).get("price_paise", 34900)

    return {
        "objective": {
            "type": "INCREASE_AOV",
            "target_sku": primary_sku,
            "horizon_days": 7,
        },
        "action": {
            "type": "DISCOUNT_OFFER",
            "items": [
                {"sku": primary_sku, "quantity": 1, "list_price_paise": p_price},
                {"sku": companion,   "quantity": 1, "list_price_paise": c_price},
            ],
            "discount_pct": disc,
        },
        "rationale": rationale,
        "expected_outcome": {"aov_lift_pct": 12},
    }


def run_agent(split: str = "holdout", max_orders: int = 50):
    print(f"\n{'='*55}")
    print(f"  margin-guard agent  |  split={split}")
    print(f"{'='*55}\n")

    catalog = load_catalog()
    model   = AffinityModel()
    model.load_from_file(TRAIN_PATH)
    orders  = load_orders(HOLDOUT_PATH if split == "holdout" else TRAIN_PATH)
    orders  = orders[:max_orders]
    results = []

    for idx, order in enumerate(orders):
        primary_sku = order["basket"][0]["sku"]
        companions  = model.top_companions(primary_sku, k=3)

        if not companions:
            print(f"[{idx+1:3}] {primary_sku:15} no companions, skip")
            results.append({"order": order, "outcome": "skipped", "attempts": 0})
            continue

        print(f"\n[{idx+1:3}] {primary_sku:15} companions: {[c['sku'] for c in companions]}")

        denial     = None
        final      = None
        proposal   = None

        for attempt in range(1, 4):
            try:
                proposal = llm_propose(order, companions, catalog, denial)
            except Exception as e:
                print(f"      LLM error: {e}")
                break

            proposal["attempt_no"] = attempt
            proposal["model"]      = "claude-haiku-4-5"
            if denial:
                proposal["parent_id"] = denial.get("action_id")

            resp = httpx.post(
                f"{CONTROL_URL}/control/propose",
                json=proposal,
                timeout=15.0,
            )

            if resp.status_code != 200:
                print(f"      control plane error: {resp.status_code}")
                break

            result = resp.json()
            disc   = proposal.get("action", {}).get("discount_pct", "?")
            print(f"      attempt {attempt}: {disc}% off -> "
                  f"{result['decision']} margin={result.get('margin_pct','?')}%")

            if result["decision"] == "ALLOW":
                final = result
                break
            if result["decision"] == "GATE":
                # GATE = approved but needs human — treat as success for measurement
                final = result
                break

            denial = result
            denial["previous_discount_pct"] = disc

        if final and final["decision"] == "ALLOW":
            results.append({
                "order":        order,
                "outcome":      "converted",
                "attempts":     attempt,
                "rzp_order_id": final.get("rzp_entity_id"),
                "margin_pct":   final.get("margin_pct"),
                "discount_pct": proposal.get("action", {}).get("discount_pct"),
            })
        else:
            results.append({
                "order":   order,
                "outcome": "denied" if denial else "error",
                "attempts": attempt if proposal else 0,
            })

    converted = [r for r in results if r["outcome"] == "converted"]
    denied    = [r for r in results if r["outcome"] == "denied"]
    skipped   = [r for r in results if r["outcome"] == "skipped"]

    print(f"\n{'='*55}")
    print(f"  RESULTS")
    print(f"{'='*55}")
    print(f"  Total:     {len(results)}")
    print(f"  Converted: {len(converted)}")
    print(f"  Denied:    {len(denied)}")
    print(f"  Skipped:   {len(skipped)}")

    if converted:
        margins = [r["margin_pct"] for r in converted if r.get("margin_pct")]
        discs   = [r["discount_pct"] for r in converted if r.get("discount_pct")]
        if margins:
            print(f"  Avg margin:   {sum(margins)/len(margins):.2f}%")
        if discs:
            print(f"  Avg discount: {sum(discs)/len(discs):.2f}%")

    with open("docs/results.md", "w") as f:
        f.write("# Agent Results\n\n")
        f.write(f"- Orders processed: {len(results)}\n")
        f.write(f"- Converted: {len(converted)}\n")
        f.write(f"- Denied: {len(denied)}\n")
        f.write(f"- Skipped: {len(skipped)}\n")

    return results


if __name__ == "__main__":
    import sys
    split = sys.argv[1] if len(sys.argv) > 1 else "holdout"
    run_agent(split=split)
