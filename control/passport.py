"""
control/passport.py

Action Passport — a structured, time-bounded authorization document.

The agent receives a Passport, not raw Razorpay credentials.
The Passport defines exactly what is authorized, for how long,
and under which policy version.

This makes the thesis visible as a first-class concept:
"The AI decides what to pursue.
 The Passport defines what it is authorized to execute."
"""
from __future__ import annotations
import hashlib
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class ActionPassport:
    """
    A narrowly scoped, time-bounded authorization for one money action.

    The agent presents this Passport to ExecutionService.
    ExecutionService validates it before calling Razorpay.
    A Passport is single-use: consumed on first valid execution.
    """
    action_id:        str
    jti:              str           # single-use token
    agent_id:         str
    objective_type:   str           # e.g. "INCREASE_AOV"
    allowed_action:   str           # e.g. "DISCOUNT_OFFER"
    merchant_id:      str
    policy_version:   str

    # Economic authorization — what the Passport permits
    max_discount_pct:   float
    max_amount_paise:   int
    authorized_amount:  int         # the specific amount approved
    economic_score:     float       # 0-100, higher is better

    # Validity
    issued_at:    str               # ISO timestamp
    valid_until:  str               # ISO timestamp (5 min TTL)

    # Evidence
    passport_hash: str              # tamper-evident


def _compute_passport_hash(data: dict) -> str:
    """Hash covers all authorization fields — any edit is detectable."""
    fields = (
        f"{data['action_id']}|{data['jti']}|{data['agent_id']}"
        f"|{data['objective_type']}|{data['allowed_action']}"
        f"|{data['merchant_id']}|{data['policy_version']}"
        f"|{data['max_discount_pct']}|{data['max_amount_paise']}"
        f"|{data['authorized_amount']}|{data['economic_score']}"
        f"|{data['issued_at']}|{data['valid_until']}"
    )
    return hashlib.sha256(fields.encode()).hexdigest()


def issue_passport(
    action_id:       str,
    agent_id:        str,
    objective_type:  str,
    allowed_action:  str,
    merchant_id:     str,
    policy_version:  str,
    max_discount_pct:  float,
    max_amount_paise:  int,
    authorized_amount: int,
    economic_score:    float,
    ttl_minutes:       int = 5,
) -> ActionPassport:
    """
    Issue a new Action Passport.
    Called by the control plane after PolicyEngine returns ALLOW.
    """
    now        = datetime.utcnow()
    issued_at  = now.isoformat() + "Z"
    valid_until = (now + timedelta(minutes=ttl_minutes)).isoformat() + "Z"
    jti        = str(uuid.uuid4())

    data = {
        "action_id":        action_id,
        "jti":              jti,
        "agent_id":         agent_id,
        "objective_type":   objective_type,
        "allowed_action":   allowed_action,
        "merchant_id":      merchant_id,
        "policy_version":   policy_version,
        "max_discount_pct": max_discount_pct,
        "max_amount_paise": max_amount_paise,
        "authorized_amount": authorized_amount,
        "economic_score":   economic_score,
        "issued_at":        issued_at,
        "valid_until":      valid_until,
    }

    return ActionPassport(
        **data,
        passport_hash=_compute_passport_hash(data),
    )


def validate_passport(passport: ActionPassport) -> tuple[bool, str]:
    """
    Validate a Passport before execution.
    Returns (valid: bool, reason: str).
    """
    # 1. Check expiry
    try:
        valid_until = datetime.fromisoformat(
            passport.valid_until.replace("Z", "+00:00")
        ).replace(tzinfo=None)
        if datetime.utcnow() > valid_until:
            return False, "passport_expired"
    except ValueError:
        return False, "passport_invalid_timestamp"

    # 2. Check hash integrity
    data = {
        "action_id":        passport.action_id,
        "jti":              passport.jti,
        "agent_id":         passport.agent_id,
        "objective_type":   passport.objective_type,
        "allowed_action":   passport.allowed_action,
        "merchant_id":      passport.merchant_id,
        "policy_version":   passport.policy_version,
        "max_discount_pct": passport.max_discount_pct,
        "max_amount_paise": passport.max_amount_paise,
        "authorized_amount": passport.authorized_amount,
        "economic_score":   passport.economic_score,
        "issued_at":        passport.issued_at,
        "valid_until":      passport.valid_until,
    }
    expected_hash = _compute_passport_hash(data)
    if passport.passport_hash != expected_hash:
        return False, "passport_tampered"

    return True, "valid"


def passport_to_dict(passport: ActionPassport) -> dict:
    return asdict(passport)


def passport_from_dict(data: dict) -> ActionPassport:
    return ActionPassport(**data)
