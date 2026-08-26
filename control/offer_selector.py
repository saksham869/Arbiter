"""
control/offer_selector.py
Selects the highest economically safe offer rung.

Logic:
  eligible = authorized_rungs ∩ economically_safe_rungs
  selected = highest discount in eligible

This is the correct Razorpay pattern:
  offers:[offer_id], force_offer:true

The agent never picks the offer. MarginGuard does.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class OfferRung:
    pct: float
    offer_id: str


@dataclass
class SelectedOffer:
    offer_id: str
    discount_pct: float
    reason: str


def select_offer(
    ceiling_pct: float,
    rungs: list[dict],
) -> Optional[SelectedOffer]:
    """
    Pick the highest rung that fits under the economic ceiling.

    ceiling_pct: max safe discount from MarginEngine.max_discount()
    rungs: list of {pct, offer_id} from policy YAML

    Returns None if no rung is safe.
    """
    if not rungs:
        return None

    safe = [
        r for r in rungs
        if r["pct"] <= ceiling_pct
    ]

    if not safe:
        return None

    best = max(safe, key=lambda r: r["pct"])

    return SelectedOffer(
        offer_id=best["offer_id"],
        discount_pct=best["pct"],
        reason=f"highest authorized rung ({best['pct']}%) within economic ceiling ({ceiling_pct:.2f}%)",
    )
