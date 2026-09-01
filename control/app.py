"""
control/app.py
FastAPI — all endpoints.
The agent posts proposals here. Everything else is read-only.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from typing import Optional

import yaml
import boto3
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from control.margin import FeeModel, LineItem, MarginEngine

# AWS clients for GATE workflow
_sqs = boto3.client("sqs", region_name="us-east-1")
_sns = boto3.client("sns", region_name="us-east-1")
_cw  = boto3.client("cloudwatch", region_name="us-east-1")
SQS_QUEUE_URL  = os.getenv("SQS_APPROVAL_QUEUE_URL", "")
SNS_TOPIC_ARN  = os.getenv("SNS_TOPIC_ARN", "")
MERCHANT_EMAIL      = os.getenv("MERCHANT_EMAIL", "")
LAMBDA_APPROVAL_URL = os.getenv("LAMBDA_APPROVAL_URL", "")
CONTROL_PUBLIC_URL  = os.getenv("CONTROL_PUBLIC_URL", "http://localhost:8085/")
APPROVAL_SECRET     = "mg-approval-secret-2026"


def _approval_token(action_id: str) -> str:
    """Same token logic as Lambda handler."""
    return hashlib.sha256(
        f"{action_id}:{APPROVAL_SECRET}".encode()
    ).hexdigest()[:32]


def _emit_metrics(decision: str, margin_pct: float, eco_score: float,
                  discount_pct: float):
    """Emit product-focused metrics to CloudWatch."""
    try:
        _cw.put_metric_data(
            Namespace="MarginGuard",
            MetricData=[
                {
                    "MetricName": "ProposalDecision",
                    "Value": 1,
                    "Unit": "Count",
                    "Dimensions": [
                        {"Name": "Decision", "Value": decision}
                    ],
                },
                {
                    "MetricName": "MarginPct",
                    "Value": float(margin_pct or 0),
                    "Unit": "Percent",
                },
                {
                    "MetricName": "EconomicScore",
                    "Value": float(eco_score or 0),
                    "Unit": "None",
                },
                {
                    "MetricName": "DiscountPct",
                    "Value": float(discount_pct or 0),
                    "Unit": "Percent",
                },
            ]
        )
    except Exception as e:
        pass   # CloudWatch is observability — never block execution
from control.offer_selector import select_offer
from control.passport import issue_passport, passport_to_dict
from control.economic_score import compute_economic_score
from control.catalog_agent import extract_from_image, approve_extraction, get_pending
from control.policy import PolicyEngine, MerchantLimits
from control.execution import mint_token, execute
import control.ledger as ledger

app = FastAPI(title="margin-guard", version="0.1.0")

import os as _os
if _os.path.exists("control/static"):
    app.mount("/static", StaticFiles(directory="control/static"), name="static")

@app.get("/dashboard", response_class=FileResponse)
def dashboard():
    return "control/static/dashboard.html"

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


# ── Approval endpoints ───────────────────────────────────────

class ApprovalIn(BaseModel):
    action_id: str
    approved: bool
    source: str = "api"

@app.get("/control/approve")
def approve_action(action_id: str, token: str):
    """Merchant clicks APPROVE link from email."""
    import hashlib
    expected = hashlib.sha256(
        f"{action_id}:{APPROVAL_SECRET}".encode()
    ).hexdigest()[:32]
    if token != expected:
        from fastapi.responses import HTMLResponse
        return HTMLResponse("<h2>Invalid or expired token.</h2>", status_code=403)

    row = ledger.get_one(action_id)
    if not row:
        from fastapi.responses import HTMLResponse
        return HTMLResponse("<h2>Action not found.</h2>", status_code=404)

    if row.get("exec_status") not in ("NOT_RUN", None):
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            f"<h2>Already processed: {row['exec_status']}</h2>",
            status_code=200
        )

    # Execute the approved action
    from control.execution import mint_token, execute
    args = json.loads(row.get("args_json", "{}"))
    tok  = mint_token(action_id)
    result = execute(action_id, tok, row.get("tool", "create_order"), args)

    from fastapi.responses import HTMLResponse
    if result.status == "SUCCESS":
        return HTMLResponse(f"""
        <html><body style="font-family:system-ui;max-width:600px;margin:60px auto;padding:20px">
        <h1>margin-guard</h1>
        <h2>Action Approved</h2>
        <p>Order created: <strong>{result.rzp_entity_id}</strong></p>
        <p>Amount: Rs.{args.get('amount', 0)/100:,.2f}</p>
        <p>The AI agent has executed the approved bundle offer.</p>
        </body></html>
        """)
    else:
        return HTMLResponse(f"<h2>Execution {result.status}: {result.error}</h2>")


@app.get("/control/reject")
def reject_action(action_id: str, token: str):
    """Merchant clicks REJECT link from email."""
    import hashlib
    expected = hashlib.sha256(
        f"{action_id}:{APPROVAL_SECRET}".encode()
    ).hexdigest()[:32]
    if token != expected:
        from fastapi.responses import HTMLResponse
        return HTMLResponse("<h2>Invalid or expired token.</h2>", status_code=403)

    ledger.finalize(action_id, "REJECTED")
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""
    <html><body style="font-family:system-ui;max-width:600px;margin:60px auto;padding:20px">
    <h1>margin-guard</h1>
    <h2>Action Rejected</h2>
    <p>The proposed bundle offer has been rejected.</p>
    <p>No charge was made. The AI agent has been notified.</p>
    </body></html>
    """)


# ── Multimodal catalog endpoints ──────────────────────────────

class ApproveRequest(BaseModel):
    extraction_id: str
    approved_skus: list[str]


@app.post("/control/catalog/extract")
async def catalog_extract(file: bytes = None):
    """
    Upload a product image or supplier invoice.
    Claude extracts structured COGS data.
    Returns extraction_id for human approval.

    Use: curl -X POST /control/catalog/extract -F file=@invoice.png
    """
    from fastapi import UploadFile, File
    return {"error": "Use /control/catalog/extract-bytes endpoint"}


@app.post("/control/catalog/extract-bytes")
def catalog_extract_bytes(request: dict):
    """
    Extract COGS from base64-encoded image.
    body: {image_b64: str, media_type: str}
    """
    import base64
    image_b64  = request.get("image_b64", "")
    media_type = request.get("media_type", "image/png")
    image_bytes = base64.b64decode(image_b64)
    result = extract_from_image(image_bytes, media_type)
    return {
        "extraction_id": result.extraction_id,
        "status":        result.status,
        "extracted":     result.extracted,
        "image_note":    result.image_note,
        "extracted_at":  result.extracted_at,
        "warning":       "Extracted COGS require human approval before entering trusted catalog.",
    }


@app.get("/control/catalog/pending")
def catalog_pending():
    """List extractions waiting for human approval."""
    return {"pending": get_pending()}


@app.post("/control/catalog/approve")
def catalog_approve(req: ApproveRequest):
    """
    Human approves specific SKUs from an extraction.
    Only approved items enter the trusted catalog.
    """
    result = approve_extraction(req.extraction_id, req.approved_skus)
    return result


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
        _emit_metrics("DENY", decision.margin_pct or 0, eco.score, req.action.discount_pct)
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
        # Publish to SQS — persistent approval queue
        gate_msg = {
            "action_id":    entry.id,
            "merchant_id":  merchant_id,
            "tool":         "create_order",
            "amount_paise": paid_paise,
            "discount_pct": req.action.discount_pct,
            "margin_pct":   decision.margin_pct,
            "reason":       decision.reason,
            "items":        [{"sku": i.sku, "qty": i.quantity} for i in line_items],
            "economic_score": eco.score,
        }
        if SQS_QUEUE_URL:
            try:
                _sqs.send_message(
                    QueueUrl=SQS_QUEUE_URL,
                    MessageBody=json.dumps(gate_msg),
                    MessageAttributes={
                        "action_id": {
                            "DataType": "String",
                            "StringValue": entry.id,
                        }
                    }
                )
            except Exception as e:
                print(f"SQS publish failed: {e}")

        # Notify merchant via SNS email
        if SNS_TOPIC_ARN and MERCHANT_EMAIL:
            try:
                items_str = ", ".join(
                    f"{i.sku} x{i.quantity}" for i in line_items
                )
                _sns.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Subject="[MarginGuard] Action requires your approval",
                    Message=(
                        f"Your AI growth agent has proposed an action that requires approval.\n\n"
                        f"Items:      {items_str}\n"
                        f"Amount:     Rs.{paid_paise/100:,.2f}\n"
                        f"Discount:   {req.action.discount_pct}%\n"
                        f"Margin:     {decision.margin_pct}%\n"
                        f"Eco Score:  {eco.score}/100\n"
                        f"Reason:     {decision.reason}\n\n"
                        f"Action ID:  {entry.id}\n\n"
                        + (f"APPROVE: {CONTROL_PUBLIC_URL}control/approve?action_id={entry.id}&token={_approval_token(entry.id)}\n"
                           f"REJECT:  {CONTROL_PUBLIC_URL}control/reject?action_id={entry.id}&token={_approval_token(entry.id)}\n\n")
                        + f"The AI agent will wait for your decision.\n"
                    )
                )
            except Exception as e:
                print(f"SNS publish failed: {e}")

        _emit_metrics("GATE", decision.margin_pct or 0, eco.score, req.action.discount_pct)
        return {
            "action_id":    entry.id,
            "decision":     "GATE",
            "reason":       decision.reason,
            "margin_pct":   decision.margin_pct,
            "economic_score": eco.score,
            "queued":       bool(SQS_QUEUE_URL),
            "notified":     bool(SNS_TOPIC_ARN),
            "message":      "Action queued for merchant approval. Email notification sent.",
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

    _emit_metrics("ALLOW", decision.margin_pct or 0, eco.score, req.action.discount_pct)
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


# ── Dashboard approve/reject (no token needed from browser) ────────────────────
import hashlib as _hashlib

@app.post("/control/dashboard/approve")
def dashboard_approve(action_id: str):
    token = _hashlib.sha256(
        f"{action_id}:{APPROVAL_SECRET}".encode()
    ).hexdigest()[:32]
    return approve_action(action_id=action_id, token=token)

@app.post("/control/dashboard/reject")
def dashboard_reject(action_id: str):
    token = _hashlib.sha256(
        f"{action_id}:{APPROVAL_SECRET}".encode()
    ).hexdigest()[:32]
    return reject_action(action_id=action_id, token=token)

@app.post("/control/policy")
def update_policy(body: dict):
    return {"status": "accepted", "policy": body}
