"""
control/app.py
FastAPI — all endpoints.
The agent posts proposals here. Everything else is read-only.
"""
from __future__ import annotations
import hashlib
import json
import os
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from control.margin import FeeModel, LineItem, MarginEngine
from control.offer_selector import select_offer
from control.passport import issue_passport, passport_to_dict
from control.economic_score import compute_economic_score
from control.policy import PolicyEngine, MerchantLimits
from control.execution import mint_token, execute
import control.ledger as ledger

app = FastAPI(title="margin-guard", version="0.1.0")

# ── Load policy ───────────────────────────────────────────────
POLICY_PATH = os.getenv("POLICY_PATH", "./policies/default.yaml")

# Module-level velocity tracker — persists across requests
# This is the fix: VelocityTracker must NOT be per-request
from control.policy import VelocityTracker as _VT
_VELOCITY_TRACKER = _VT(window_seconds=60)

def _load_policy():
    with open(POLICY_PATH) as f:
        return yaml.safe_load(f)

def _build_engine(policy: dict, catalog: dict) -> PolicyEngine:
    fm = policy.get("fee_model", {})
    fee_model = FeeModel(
        platform_fee_pct=fm.get("platform_fee_pct", 2.0),
        gst_on_fee_pct=fm.get("gst_on_fee_pct", 18.0),
        mdr_refundable=fm.get("mdr_refundable", False),
    )
    m = policy.get("margin", {})
    li = policy.get("limits", {})
    g = policy.get("gates", {})
    v = policy.get("velocity", {})
    limits = MerchantLimits(
        floor_pct=m.get("floor_pct", 18.0),
        auto_approve_below_paise=li.get("auto_approve_below_paise", 50000),
        owner_approve_above_paise=li.get("owner_approve_above_paise", 500000),
        max_discount_pct=li.get("max_discount_pct", 20.0),
        daily_discount_budget_paise=li.get("daily_discount_budget_paise", 2500000),
        return_rate_threshold=g.get("return_rate_threshold", 0.25),
        max_actions_per_60s=v.get("max_actions_per_60s", 10),
        unknown_cogs=m.get("unknown_cogs", "deny"),
    )
    engine = PolicyEngine(limits, fee_model, catalog)
    engine.velocity = _VELOCITY_TRACKER  # shared across requests
    return engine


# ── Request / Response models ─────────────────────────────────
class ItemIn(BaseModel):
    sku: str
    quantity: int
    list_price_paise: int
    cogs_paise: Optional[int] = None

class ObjectiveIn(BaseModel):
    type: str
    target_sku: str
    horizon_days: int = 7

class ActionIn(BaseModel):
    type: str
    items: list[ItemIn]
    discount_pct: float

class ProposalIn(BaseModel):
    objective: ObjectiveIn
    action: ActionIn
    rationale: str
    expected_outcome: dict = {}
    model: Optional[str] = None
    prompt_hash: Optional[str] = None
    parent_id: Optional[str] = None
    attempt_no: int = 1

class CeilingRequest(BaseModel):
    items: list[ItemIn]
    list_total_paise: int
    shipping_paise: int = 0


# ── Catalog (loaded from catalog.csv) ────────────────────────
import csv

def _load_catalog() -> dict:
    catalog = {}
    path = "./data/catalog.csv"
    if not os.path.exists(path):
        return catalog
    with open(path) as f:
        for row in csv.DictReader(f):
            catalog[row["sku"]] = {
                "cogs_paise":  int(row["cogs_paise"]),
                "return_rate": float(row["return_rate"]),
                "name":        row["name"],
                "category":    row["category"],
            }
    return catalog


# ── Endpoints ─────────────────────────────────────────────────
@app.get("/control/health")
def health():
    return {"service": "margin-guard", "status": "UP"}


@app.post("/control/propose")
def propose(req: ProposalIn):
    """
    Agent submits a proposal.
    Returns ALLOW, GATE, or DENY with constraint.
    If ALLOW: executes the Razorpay action and returns the result.
    """
    policy  = _load_policy()
    catalog = _load_catalog()
    engine  = _build_engine(policy, catalog)
    merchant_id = policy.get("merchant", "unknown")

    # build LineItems — catalog is AUTHORITATIVE for COGS
    # agent cannot inject its own cost data — that breaks the thesis
    line_items = []
    for it in req.action.items:
        cat_entry = catalog.get(it.sku)
        if cat_entry is None:
            # SKU not in catalog → unknown_cogs → DENY
            cogs = None
        else:
            cogs = cat_entry.get("cogs_paise")  # catalog only, never request body
        line_items.append(LineItem(
            sku=it.sku,
            quantity=it.quantity,
            list_price_paise=it.list_price_paise,
            cogs_paise=cogs,
        ))

    list_total   = sum(i.list_price_paise * i.quantity for i in line_items)
    paid_paise   = int(list_total * (1 - req.action.discount_pct / 100))

    # compute economic score before adjudication
    # wrapped in try-except: unknown COGS raises UnknownCogsError
    # PolicyEngine will handle it as DENY — score is best-effort only
    try:
        margin_result = engine.engine.compute(line_items, paid_paise)
        avg_return_rate = sum(
            catalog.get(i.sku, {}).get("return_rate", 0) for i in line_items
        ) / max(len(line_items), 1)
        avg_stock = sum(
            catalog.get(i.sku, {}).get("stock_units", 100) for i in line_items
        ) / max(len(line_items), 1)
        eco = compute_economic_score(
            margin_pct=margin_result.margin_pct,
            return_rate=avg_return_rate,
            discount_pct=req.action.discount_pct,
            stock_units=int(avg_stock),
            floor_pct=policy.get("margin", {}).get("floor_pct", 18.0),
        )
    except Exception:
        from control.economic_score import EconomicScoreResult
        eco = EconomicScoreResult(
            score=0.0, margin_pct=0.0, margin_penalty=0.0,
            return_risk_penalty=0.0, discount_penalty=0.0,
            inventory_bonus=0.0, decision="DENY",
            explanation="Economic score unavailable — COGS unknown for one or more SKUs",
        )

    # adjudicate
    decision = engine.check(
        items=line_items,
        paid_paise=paid_paise,
        list_total_paise=list_total,
    )

    # build args for Razorpay
    args = {
        "amount":   paid_paise,
        "currency": "INR",
        "receipt":  f"mg_{req.objective.target_sku}_{req.attempt_no}",
        "notes": {
            "mg_v":      "1",
            "mg_items":  json.dumps([
                {"s": i.sku, "q": i.quantity, "p": i.list_price_paise}
                for i in line_items
            ]),
            "mg_disc":   str(req.action.discount_pct),
            "mg_obj":    req.objective.type,
        },
    }

    prompt_hash = req.prompt_hash or hashlib.sha256(
        req.rationale.encode()
    ).hexdigest()[:16]

    # record to ledger
    entry = ledger.append(
        merchant_id=merchant_id,
        tool="create_order",
        args=args,
        decision=decision.result,
        reason=decision.reason,
        constraint=decision.constraint if decision.constraint else None,
        margin_pct=decision.margin_pct,
        model=req.model,
        prompt_hash=prompt_hash,
        amount_paise=paid_paise,
        attempt_no=req.attempt_no,
        parent_id=req.parent_id,
    )

    if decision.result == "DENY":
        return {
            "action_id":  entry.id,
            "decision":   "DENY",
            "reason":     decision.reason,
            "constraint": decision.constraint,
            "margin_pct": decision.margin_pct,
            "economic_score": {
                "score":       eco.score,
                "decision":    eco.decision,
                "explanation": eco.explanation,
                "breakdown": {
                    "margin_penalty":      eco.margin_penalty,
                    "return_risk_penalty": eco.return_risk_penalty,
                    "discount_penalty":    eco.discount_penalty,
                    "inventory_bonus":     eco.inventory_bonus,
                },
            },
            "replan": {"required": True, "objective_preserved": True},
        }

    if decision.result == "GATE":
        return {
            "action_id": entry.id,
            "decision":  "GATE",
            "reason":    decision.reason,
            "margin_pct": decision.margin_pct,
        }

    # ALLOW — select the highest safe offer rung
    rungs   = policy.get("offers", {}).get("rungs", [])
    ceiling = decision.constraint.get("max_discount_pct", 100.0) if decision.constraint else 100.0

    # compute ceiling from margin engine
    margin_ceiling = engine.engine.max_discount(
        line_items,
        sum(i.total_list_price_paise for i in line_items),
        floor_pct=policy.get("margin", {}).get("floor_pct", 18.0),
    )
    offer = select_offer(margin_ceiling.max_discount_pct, rungs)

    # build final order args with offer if available
    if offer:
        # Only attach if this is a real offer_id (not a placeholder)
        if not offer.offer_id.endswith(("AAAA","BBBB","CCCC","DDDD")):
            args["offers"]      = [offer.offer_id]
            args["force_offer"] = True
        # amount is always the margin-safe paid amount
        args["amount"] = paid_paise

    token   = mint_token(entry.id)

    # Issue Action Passport
    passport = issue_passport(
        action_id=entry.id,
        agent_id=req.model or "growth-agent",
        objective_type=req.objective.type,
        allowed_action=req.action.type,
        merchant_id=merchant_id,
        policy_version=str(policy.get("version", "1")),
        max_discount_pct=margin_ceiling.max_discount_pct,
        max_amount_paise=list_total,
        authorized_amount=paid_paise,
        economic_score=eco.score,
        ttl_minutes=5,
    )

    result = execute(entry.id, token, "create_order", args, passport=passport)

    return {
        "action_id":     entry.id,
        "decision":      "ALLOW",
        "exec_status":   result.status,
        "rzp_entity_id": result.rzp_entity_id,
        "margin_pct":    decision.margin_pct,
        "action_passport": passport_to_dict(passport),
        "economic_score": {
            "score":       eco.score,
            "decision":    eco.decision,
            "explanation": eco.explanation,
        },
        "selected_offer": {
            "offer_id":     offer.offer_id if offer else None,
            "discount_pct": offer.discount_pct if offer else req.action.discount_pct,
            "reason":       offer.reason if offer else "no pre-registered offers",
        },
        "error": result.error,
    }


@app.post("/control/margin/ceiling")
def ceiling(req: CeilingRequest):
    """Agent queries the max safe discount before proposing."""
    policy   = _load_policy()
    catalog  = _load_catalog()
    fm       = policy.get("fee_model", {})
    fee_model = FeeModel(
        platform_fee_pct=fm.get("platform_fee_pct", 2.0),
        gst_on_fee_pct=fm.get("gst_on_fee_pct", 18.0),
        mdr_refundable=fm.get("mdr_refundable", False),
    )
    floor_pct = policy.get("margin", {}).get("floor_pct", 18.0)
    engine    = MarginEngine(fee_model)

    items = []
    for it in req.items:
        cat_entry = catalog.get(it.sku)
        cogs = cat_entry.get("cogs_paise") if cat_entry else None
        items.append(LineItem(
            sku=it.sku,
            quantity=it.quantity,
            list_price_paise=it.list_price_paise,
            cogs_paise=cogs,
        ))

    result = engine.max_discount(items, req.list_total_paise,
                                 req.shipping_paise, floor_pct)
    return {
        "max_discount_pct":   result.max_discount_pct,
        "max_discount_paise": result.max_discount_paise,
        "paid_floor_paise":   result.paid_floor_paise,
        "floor_pct":          result.floor_pct,
    }


@app.get("/control/audit")
def audit_list(limit: int = 50):
    return {"entries": ledger.get_all(limit)}


@app.get("/control/audit/verify")
def audit_verify():
    return ledger.verify()


@app.get("/control/audit/{action_id}")
def audit_one(action_id: str):
    row = ledger.get_one(action_id)
    if not row:
        raise HTTPException(status_code=404, detail="action not found")
    return row
