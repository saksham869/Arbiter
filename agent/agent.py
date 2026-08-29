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
from agent.experiment import ExperimentEngine

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
    """Real LLM via AWS Bedrock Claude Haiku 4.5. Falls back to mock on error."""
    import boto3
    import json as _json

    primary_sku = order["basket"][0]["sku"]
    p_price = catalog.get(primary_sku, {}).get("price_paise", 100000)
    safe = [c for c in companions
            if catalog.get(c["sku"], {}).get("return_rate", 0) <= 0.24]

    if denial:
        constraint = denial.get("constraint", {})
        max_d = constraint.get("max_discount_pct", 18.0)
        prompt = (open("agent/prompts/replan.txt").read()
            .replace("{reason}", str(denial.get("reason", "unknown")))
            .replace("{constraint}", _json.dumps(constraint))
            .replace("{max_discount_pct}", str(max_d))
            .replace("{previous_discount_pct}", str(denial.get("previous_discount_pct", 30)))
            .replace("{order}", _json.dumps(order["basket"]))
            .replace("{gate_pct}", "19.0")
        )
    else:
        prompt = (open("agent/prompts/propose.txt").read()
            .replace("{companions}", _json.dumps(safe[:3], indent=2))
            .replace("{order}", _json.dumps(order["basket"]))
            .replace("{floor_pct}", "18.0")
            .replace("{max_discount_pct}", "19.0")
        )

    try:
        client = boto3.client("bedrock-runtime", region_name="us-east-1")
        resp = client.invoke_model(
            modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            body=_json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 512,
                "messages": [{"role": "user", "content": prompt}],
            })
        )
        text = _json.loads(resp["body"].read())["content"][0]["text"].strip()
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
        start = text.find("{")
        end   = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end+1]
        proposal = _json.loads(text)

        # CRITICAL: override prices from catalog — never trust LLM prices
        # Claude frequently gets paise vs rupees wrong
        items = proposal.get("action", {}).get("items", [])
        for item in items:
            sku = item.get("sku", "")
            if sku in catalog:
                item["list_price_paise"] = catalog[sku]["price_paise"]

        # Validate discount is reasonable
        disc = proposal.get("action", {}).get("discount_pct", 0)
        if disc <= 0 or disc > 25:
            proposal["action"]["discount_pct"] = 15.0

        return proposal

    except Exception as e:
        print(f"      Bedrock error: {e}, using mock")
        import random
        companion = safe[0]["sku"] if safe else "SOCK-3PK"
        c_price = catalog.get(companion, {}).get("price_paise", 34900)
        if denial:
            constraint = denial.get("constraint", {})
            max_d = constraint.get("max_discount_pct", 18.0)
            disc = round(min(max_d - 1.5, 18.0), 1)
        else:
            disc = round(random.uniform(24, 28), 1)
        return {
            "objective": {"type": "INCREASE_AOV", "target_sku": primary_sku, "horizon_days": 7},
            "action": {
                "type": "DISCOUNT_OFFER",
                "items": [
                    {"sku": primary_sku, "quantity": 1, "list_price_paise": p_price},
                    {"sku": companion, "quantity": 1, "list_price_paise": c_price},
                ],
                "discount_pct": disc,
            },
            "rationale": f"Mock fallback: {disc}% off {primary_sku}+{companion}",
            "expected_outcome": {"aov_lift_pct": 10},
        }


def run_agent(split: str = "holdout", max_orders: int = 50):
    print(f"\n{'='*55}")
    print(f"  margin-guard agent  |  split={split}")
    print(f"{'='*55}\n")

    catalog  = load_catalog()
    model    = AffinityModel()
    model.load_from_file(TRAIN_PATH)
    orders   = load_orders(HOLDOUT_PATH if split == "holdout" else TRAIN_PATH)
    orders   = orders[:max_orders]
    exp      = ExperimentEngine(treatment_pct=0.5, seed=42)

    for idx, order in enumerate(orders):
        primary_sku = order["basket"][0]["sku"]
        cohort      = exp.assign_cohort(order.get("order_id", str(idx)))
        companions  = model.top_companions(primary_sku, k=3)

        if not companions:
            print(f"[{idx+1:3}] {primary_sku:15} no companions, skip")
            exp.record_control(order, catalog)
            continue

        if cohort == "CONTROL":
            print(f"[{idx+1:3}] {primary_sku:15} [CONTROL]")
            exp.record_control(order, catalog)
            continue

        print(f"\n[{idx+1:3}] {primary_sku:15} [TREATMENT] {[c['sku'] for c in companions]}")

        denial   = None
        final    = None
        proposal = None

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
            eco    = result.get("economic_score", {})
            score  = eco.get("score", "?") if eco else "?"
            print(f"      attempt {attempt}: {disc}% off -> "
                  f"{result['decision']} margin={result.get('margin_pct','?')}% "
                  f"eco={score}")

            if result["decision"] in ("ALLOW", "GATE"):
                final = result
                break

            denial = result
            denial["previous_discount_pct"] = disc

        exp.record_treatment(
            order, catalog,
            final or denial or {},
            action_id=final.get("action_id") if final else None,
        )

    report = exp.save_results("docs/results.md")
    print(report.summary())
    return exp._results


if __name__ == "__main__":
    import sys
    split = sys.argv[1] if len(sys.argv) > 1 else "holdout"
    run_agent(split=split)
