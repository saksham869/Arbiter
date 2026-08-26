"""
control/razorpay_client.py
Read-only Razorpay API calls.
Used by the agent to fetch order history and measure conversions.
Write actions go through execution.py only.
"""
from __future__ import annotations
import os
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
BASE_URL   = "https://api.razorpay.com/v1"
AUTH       = (KEY_ID, KEY_SECRET)


def fetch_all_orders(count: int = 100) -> list[dict]:
    """Fetch recent orders for building the affinity matrix."""
    resp = httpx.get(
        f"{BASE_URL}/orders",
        auth=AUTH,
        params={"count": count, "expand[]": "payments"},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])


def fetch_order(order_id: str) -> Optional[dict]:
    """Fetch a single order by ID."""
    resp = httpx.get(
        f"{BASE_URL}/orders/{order_id}",
        auth=AUTH,
        timeout=10.0,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def fetch_order_payments(order_id: str) -> list[dict]:
    """Fetch payments for an order — used to measure conversion."""
    resp = httpx.get(
        f"{BASE_URL}/orders/{order_id}/payments",
        auth=AUTH,
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])


def fetch_all_payments(count: int = 100) -> list[dict]:
    """Fetch recent payments."""
    resp = httpx.get(
        f"{BASE_URL}/payments",
        auth=AUTH,
        params={"count": count},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])


def create_order(amount: int, currency: str = "INR",
                 receipt: str = "", notes: Optional[dict] = None,
                 line_items: Optional[list] = None) -> dict:
    """
    Create an order. Used by execution.py via the execute() path.
    amount in paise.
    """
    payload: dict = {
        "amount": amount,
        "currency": currency,
    }
    if receipt:
        payload["receipt"] = receipt
    if notes:
        payload["notes"] = notes
    if line_items:
        payload["line_items"] = line_items
        payload["line_items_total"] = str(amount)

    resp = httpx.post(
        f"{BASE_URL}/orders",
        auth=AUTH,
        json=payload,
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()
