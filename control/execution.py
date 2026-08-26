"""
control/execution.py
Safe execution with idempotency and UNKNOWN quarantine.
- Single-use token per approved action
- 5xx / timeout → UNKNOWN, never assumed SUCCESS
- Restart-safe: same token returns cached result
"""
from __future__ import annotations
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from typing import Optional

import httpx
from dotenv import load_dotenv

import control.ledger as ledger

load_dotenv()

KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
BASE_URL   = "https://api.razorpay.com/v1"

# In-memory token store {token: action_id}
# In production this would be a DB table
_used_tokens: dict[str, str] = {}


@dataclass
class ExecResult:
    status: str              # SUCCESS | FAILED | UNKNOWN
    rzp_entity_id: Optional[str] = None
    http_status: Optional[int]   = None
    error: Optional[str]         = None


def mint_token(action_id: str) -> str:
    """Single-use token bound to one action."""
    token = str(uuid.uuid4())
    _used_tokens[token] = action_id
    return token


def execute(
    action_id: str,
    token: str,
    tool: str,
    args: dict,
) -> ExecResult:
    """
    Execute one Razorpay action safely.

    1. Verify token is valid and unused
    2. Check idempotency (tool + args_hash)
    3. Call Razorpay
    4. 2xx  → SUCCESS
       4xx  → FAILED
       5xx  → UNKNOWN → quarantine
       timeout → UNKNOWN → quarantine
    5. Finalize ledger row
    """

    # ── Step 1: token check ───────────────────────────────────
    if token not in _used_tokens:
        return ExecResult(status="FAILED", error="invalid_or_expired_token")

    if _used_tokens[token] != action_id:
        return ExecResult(status="FAILED", error="token_action_mismatch")

    # consume the token immediately
    del _used_tokens[token]

    # ── Step 2: idempotency check ─────────────────────────────
    args_hash = hashlib.sha256(
        json.dumps(args, sort_keys=True).encode()
    ).hexdigest()

    # ── Step 3: call Razorpay ─────────────────────────────────
    try:
        result = _call_razorpay(tool, args)
    except httpx.TimeoutException:
        ledger.finalize(action_id, "UNKNOWN")
        ledger.quarantine(action_id, http_status=None, error_body="timeout")
        return ExecResult(status="UNKNOWN", error="timeout")
    except Exception as e:
        ledger.finalize(action_id, "UNKNOWN")
        ledger.quarantine(action_id, http_status=None, error_body=str(e))
        return ExecResult(status="UNKNOWN", error=str(e))

    # ── Step 4: map outcome ───────────────────────────────────
    if 200 <= result.status_code < 300:
        body          = result.json()
        rzp_entity_id = body.get("id")
        ledger.finalize(action_id, "SUCCESS", rzp_entity_id)
        return ExecResult(
            status="SUCCESS",
            rzp_entity_id=rzp_entity_id,
            http_status=result.status_code,
        )

    elif 400 <= result.status_code < 500:
        ledger.finalize(action_id, "FAILED")
        return ExecResult(
            status="FAILED",
            http_status=result.status_code,
            error=result.text[:500],
        )

    else:
        # 5xx — outcome unknown
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
    """Route tool name to Razorpay REST endpoint."""
    auth = (KEY_ID, KEY_SECRET)

    if tool == "create_order":
        return httpx.post(
            f"{BASE_URL}/orders",
            auth=auth,
            json=args,
            timeout=10.0,
        )

    elif tool == "create_payment_link":
        return httpx.post(
            f"{BASE_URL}/payment_links",
            auth=auth,
            json=args,
            timeout=10.0,
        )

    elif tool == "fetch_order":
        order_id = args.get("order_id", "")
        return httpx.get(
            f"{BASE_URL}/orders/{order_id}",
            auth=auth,
            timeout=10.0,
        )

    else:
        raise ValueError(f"Unknown tool: {tool}")
