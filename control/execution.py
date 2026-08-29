"""
control/execution.py
Safe execution with DynamoDB-backed idempotency and UNKNOWN quarantine.

DynamoDB replaces the in-memory _executed dict.
Idempotency now survives process restarts.

Key: "{tool}:{args_hash}"
Value: serialized ExecResult + TTL (24 hours)

On UNKNOWN: key is NOT written to DynamoDB.
The next restart will retry — human must resolve the quarantine first.
"""
from __future__ import annotations
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import boto3
import httpx
from dotenv import load_dotenv

import control.ledger as ledger
from control.passport import ActionPassport, validate_passport

load_dotenv()

KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
BASE_URL   = "https://api.razorpay.com/v1"

# Single-use token store (in-memory is fine — tokens are short-lived)
_used_tokens: dict[str, str] = {}

# DynamoDB client
_dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
_idem_table = _dynamodb.Table("mg-idempotency")

TTL_SECONDS = 86400   # 24 hours


@dataclass
class ExecResult:
    status: str
    rzp_entity_id: Optional[str] = None
    http_status:   Optional[int] = None
    error:         Optional[str] = None


# ── DynamoDB idempotency ──────────────────────────────────────

def _idem_get(idem_key: str) -> Optional[ExecResult]:
    """Check if this action was already executed."""
    try:
        resp = _idem_table.get_item(Key={"idem_key": idem_key})
        item = resp.get("Item")
        if not item:
            return None
        return ExecResult(
            status=item["status"],
            rzp_entity_id=item.get("rzp_entity_id"),
            http_status=int(item["http_status"]) if item.get("http_status") else None,
            error=item.get("error"),
        )
    except Exception:
        return None   # fail open on DynamoDB read error


def _idem_put(idem_key: str, result: ExecResult):
    """Store execution result. Never called for UNKNOWN outcomes."""
    try:
        _idem_table.put_item(
            Item={
                "idem_key":     idem_key,
                "status":       result.status,
                "rzp_entity_id": result.rzp_entity_id or "",
                "http_status":  str(result.http_status or ""),
                "error":        result.error or "",
                "ttl":          int(time.time()) + TTL_SECONDS,
            },
            ConditionExpression="attribute_not_exists(idem_key)",
        )
    except _dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        pass   # already written by another process — that's fine


# ── Token management ──────────────────────────────────────────

def mint_token(action_id: str) -> str:
    token = str(uuid.uuid4())
    _used_tokens[token] = action_id
    return token


# ── Execution ─────────────────────────────────────────────────

def execute(
    action_id: str,
    token:     str,
    tool:      str,
    args:      dict,
    passport:  "ActionPassport | None" = None,
) -> ExecResult:
    """
    Execute one Razorpay action safely.

    1. Validate Action Passport (if provided)
    2. Verify single-use token
    3. Check DynamoDB idempotency (survives restarts)
    4. Call Razorpay
    5. 2xx→SUCCESS  4xx→FAILED  5xx→UNKNOWN→quarantine
    6. Write to DynamoDB (SUCCESS/FAILED only — not UNKNOWN)
    7. Finalize ledger row
    """

    # ── Step 1: Passport validation ───────────────────────────
    if passport is not None:
        valid, reason = validate_passport(passport)
        if not valid:
            return ExecResult(status="FAILED", error=f"passport_{reason}")
        args_amount = args.get("amount", 0)
        if args_amount != passport.authorized_amount:
            return ExecResult(
                status="FAILED",
                error=f"passport_amount_mismatch: passport={passport.authorized_amount} args={args_amount}"
            )

    # ── Step 2: Token check ───────────────────────────────────
    if token not in _used_tokens:
        return ExecResult(status="FAILED", error="invalid_or_expired_token")
    if _used_tokens[token] != action_id:
        return ExecResult(status="FAILED", error="token_action_mismatch")
    del _used_tokens[token]

    # ── Step 3: DynamoDB idempotency ──────────────────────────
    args_hash = hashlib.sha256(
        json.dumps(args, sort_keys=True).encode()
    ).hexdigest()
    idem_key  = f"{tool}:{args_hash}"

    cached = _idem_get(idem_key)
    if cached:
        # Already executed — return original result
        # This handles restarts correctly
        ledger.finalize(action_id, cached.status, cached.rzp_entity_id)
        return cached

    # ── Step 4: Call Razorpay ─────────────────────────────────
    try:
        result = _call_razorpay(tool, args)
    except httpx.TimeoutException:
        ledger.finalize(action_id, "UNKNOWN")
        ledger.quarantine(action_id, http_status=None, error_body="timeout")
        # Do NOT write UNKNOWN to DynamoDB — retry should be possible after human review
        return ExecResult(status="UNKNOWN", error="timeout")
    except Exception as e:
        ledger.finalize(action_id, "UNKNOWN")
        ledger.quarantine(action_id, http_status=None, error_body=str(e))
        return ExecResult(status="UNKNOWN", error=str(e))

    # ── Step 5: Map outcome ───────────────────────────────────
    if 200 <= result.status_code < 300:
        body          = result.json()
        rzp_entity_id = body.get("id")
        exec_result   = ExecResult(
            status="SUCCESS",
            rzp_entity_id=rzp_entity_id,
            http_status=result.status_code,
        )
        _idem_put(idem_key, exec_result)
        ledger.finalize(action_id, "SUCCESS", rzp_entity_id)
        return exec_result

    elif 400 <= result.status_code < 500:
        exec_result = ExecResult(
            status="FAILED",
            http_status=result.status_code,
            error=result.text[:500],
        )
        _idem_put(idem_key, exec_result)
        ledger.finalize(action_id, "FAILED")
        return exec_result

    else:
        # 5xx — outcome unknown
        # Do NOT write to DynamoDB — human must resolve
        ledger.finalize(action_id, "UNKNOWN")
        ledger.quarantine(
            action_id,
            http_status=result.status_code,
            error_body=result.text[:500],
        )
        return ExecResult(
            status="UNKNOWN",
            http_status=result.status_code,
            error=result.text[:200],
        )


def _call_razorpay(tool: str, args: dict) -> httpx.Response:
    auth = (KEY_ID, KEY_SECRET)
    if tool == "create_order":
        return httpx.post(
            f"{BASE_URL}/orders",
            auth=auth, json=args, timeout=10.0,
        )
    elif tool == "create_payment_link":
        return httpx.post(
            f"{BASE_URL}/payment_links",
            auth=auth, json=args, timeout=10.0,
        )
    elif tool == "fetch_order":
        return httpx.get(
            f"{BASE_URL}/orders/{args.get('order_id', '')}",
            auth=auth, timeout=10.0,
        )
    else:
        raise ValueError(f"Unknown tool: {tool}")
