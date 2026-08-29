"""
control/economic_score.py

Economic Authorization Score — multi-factor evaluation.
Replaces the binary margin floor with a scored authorization.

Score = 100
  - margin_penalty        (how far below ideal margin)
  - return_risk_penalty   (likelihood of non-refundable loss)
  - discount_cost         (cost of the discount itself)
  + inventory_bonus       (reward for clearing excess stock)

Score < 40  → DENY
Score 40-70 → GATE (human review)
Score > 70  → ALLOW

Why this matters:
  "Why was this allowed?" now has a real answer.
  Not "Claude thought it was a good idea."
  But: "Economic score 87.4 — margin 22.6%, return risk low,
        discount reasonable, inventory pressure moderate."
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class EconomicScoreResult:
    score:              float          # 0-100
    margin_pct:         float
    margin_penalty:     float
    return_risk_penalty: float
    discount_penalty:   float
    inventory_bonus:    float
    decision:           str            # ALLOW / GATE / DENY
    explanation:        str            # human-readable reason

    def is_allow(self) -> bool:
        return self.score > 70

    def is_gate(self) -> bool:
        return 40 <= self.score <= 70

    def is_deny(self) -> bool:
        return self.score < 40


def compute_economic_score(
    margin_pct:       float,
    return_rate:      float,
    discount_pct:     float,
    stock_units:      int   = 100,      # default if not supplied
    ideal_margin_pct: float = 25.0,
    floor_pct:        float = 18.0,
) -> EconomicScoreResult:
    """
    Compute a multi-factor Economic Authorization Score.

    Args:
        margin_pct:       computed gross margin after fees and COGS
        return_rate:      historical return rate for this SKU/category (0-1)
        discount_pct:     proposed discount percentage
        stock_units:      current inventory level
        ideal_margin_pct: target margin (default 25%)
        floor_pct:        hard minimum margin (default 18%)

    Returns:
        EconomicScoreResult with score, breakdown, and decision
    """
    score = 100.0

    # ── Factor 1: Margin penalty ──────────────────────────────
    # How far below ideal margin are we?
    # At ideal (25%): no penalty
    # At floor (18%): -35 points
    # Below floor: heavy penalty
    if margin_pct >= ideal_margin_pct:
        margin_penalty = 0.0
    elif margin_pct >= floor_pct:
        margin_penalty = (ideal_margin_pct - margin_pct) * 5.0
    else:
        # Below floor — severe penalty
        margin_penalty = (ideal_margin_pct - floor_pct) * 5.0 + \
                         (floor_pct - margin_pct) * 10.0

    score -= margin_penalty

    # ── Factor 2: Return risk penalty ────────────────────────
    # High return rate → non-refundable MDR loss on returned items
    # return_rate 0.0  → 0 penalty
    # return_rate 0.25 → -10 points
    # return_rate 0.40 → -16 points
    return_risk_penalty = return_rate * 40.0
    score -= return_risk_penalty

    # ── Factor 3: Discount cost penalty ──────────────────────
    # Larger discounts cost more — penalise proportionally
    # 5% discount  → -7.5 points
    # 15% discount → -22.5 points
    # 22% discount → -33 points
    discount_penalty = discount_pct * 0.8
    score -= discount_penalty

    # ── Factor 4: Inventory pressure bonus ───────────────────
    # High stock → more pressure to sell → reward bundle proposals
    # stock > 500: +10 points
    # stock > 200: +5 points
    # stock > 50:  +2 points
    # stock <= 50: no bonus (not excess inventory)
    if stock_units > 500:
        inventory_bonus = 10.0
    elif stock_units > 200:
        inventory_bonus = 5.0
    elif stock_units > 50:
        inventory_bonus = 2.0
    else:
        inventory_bonus = 0.0

    score += inventory_bonus
    score = max(0.0, min(100.0, score))  # clamp to 0-100

    # ── Decision ──────────────────────────────────────────────
    if score > 65:
        decision = "ALLOW"
    elif score >= 35:
        decision = "GATE"
    else:
        decision = "DENY"

    # ── Explanation ───────────────────────────────────────────
    parts = [f"margin {margin_pct:.1f}%"]
    if margin_penalty > 0:
        parts.append(f"margin penalty -{margin_penalty:.1f}")
    if return_risk_penalty > 5:
        parts.append(f"return risk penalty -{return_risk_penalty:.1f}")
    if inventory_bonus > 0:
        parts.append(f"inventory bonus +{inventory_bonus:.1f}")
    explanation = (
        f"Economic score {score:.1f}/100 → {decision}. "
        + ", ".join(parts)
    )

    return EconomicScoreResult(
        score=round(score, 2),
        margin_pct=margin_pct,
        margin_penalty=round(margin_penalty, 2),
        return_risk_penalty=round(return_risk_penalty, 2),
        discount_penalty=round(discount_penalty, 2),
        inventory_bonus=round(inventory_bonus, 2),
        decision=decision,
        explanation=explanation,
    )
