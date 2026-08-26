"""
control/policy.py
Deterministic policy engine.
No LLM. No network. No database.
Any exception inside → DENY (fail-closed).
"""
from __future__ import annotations
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from control.margin import MarginEngine, MarginResult, UnknownCogsError, LineItem, FeeModel


@dataclass
class Decision:
    result: str           # ALLOW | GATE | DENY
    reason: str
    constraint: dict = field(default_factory=dict)
    margin_pct: Optional[float] = None


@dataclass
class MerchantLimits:
    floor_pct: float              = 18.0
    auto_approve_below_paise: int = 50000
    owner_approve_above_paise: int = 500000
    max_discount_pct: float       = 20.0
    daily_discount_budget_paise: int = 2500000
    return_rate_threshold: float  = 0.25
    max_actions_per_60s: int      = 10
    unknown_cogs: str             = "deny"


class VelocityTracker:
    """Sliding window counter. Thread-safe enough for single-process use."""
    def __init__(self, window_seconds: int = 60):
        self.window  = window_seconds
        self._events: deque = deque()

    def record(self):
        now = time.time()
        self._events.append(now)
        self._prune(now)

    def count(self) -> int:
        self._prune(time.time())
        return len(self._events)

    def _prune(self, now: float):
        cutoff = now - self.window
        while self._events and self._events[0] < cutoff:
            self._events.popleft()


class PolicyEngine:
    """
    Evaluates proposals against merchant limits.
    Rules run in order. First DENY wins. First GATE wins if no DENY.
    Default: DENY.
    Fail-closed: any exception → DENY.
    """

    def __init__(
        self,
        limits: MerchantLimits,
        fee_model: FeeModel,
        catalog: dict,           # sku -> {"cogs_paise": int, "return_rate": float}
    ):
        self.limits   = limits
        self.engine   = MarginEngine(fee_model)
        self.catalog  = catalog
        self.velocity = VelocityTracker(window_seconds=60)

    def check(
        self,
        items: list,
        paid_paise: int,
        list_total_paise: int,
        shipping_paise: int = 0,
    ) -> Decision:
        try:
            return self._evaluate(items, paid_paise, list_total_paise, shipping_paise)
        except UnknownCogsError as e:
            return Decision(
                result="DENY",
                reason="unknown_cogs",
                constraint={"sku": e.sku},
            )
        except Exception as e:
            return Decision(
                result="DENY",
                reason=f"policy_engine_error: {type(e).__name__}: {e}",
            )

    def _evaluate(
        self,
        items: list,
        paid_paise: int,
        list_total_paise: int,
        shipping_paise: int,
    ) -> Decision:

        # ── Rule 1: velocity ──────────────────────────────────
        if self.velocity.count() >= self.limits.max_actions_per_60s:
            return Decision(result="DENY", reason="velocity_limit")

        # ── Rule 2: unknown COGS ──────────────────────────────
        for item in items:
            if item.cogs_paise is None:
                if self.limits.unknown_cogs == "deny":
                    return Decision(
                        result="DENY",
                        reason="unknown_cogs",
                        constraint={"sku": item.sku},
                    )

        # ── Rule 3: return risk ───────────────────────────────
        for item in items:
            cat = self.catalog.get(item.sku, {})
            rr  = cat.get("return_rate", 0.0)
            if rr > self.limits.return_rate_threshold:
                return Decision(
                    result="DENY",
                    reason="return_risk",
                    constraint={"sku": item.sku, "return_rate": rr},
                )

        # ── Rule 4: margin floor ──────────────────────────────
        margin = self.engine.compute(items, paid_paise, shipping_paise)

        if not margin.is_above_floor(self.limits.floor_pct):
            ceiling = self.engine.max_discount(
                items, list_total_paise, shipping_paise, self.limits.floor_pct
            )
            return Decision(
                result="DENY",
                reason="margin_floor",
                constraint={"max_discount_pct": ceiling.max_discount_pct},
                margin_pct=round(margin.margin_pct, 2),
            )

        # ── Rule 5: discount gate ─────────────────────────────
        if list_total_paise > 0:
            discount_pct = (1.0 - paid_paise / list_total_paise) * 100.0
            if discount_pct > self.limits.max_discount_pct:
                return Decision(
                    result="GATE",
                    reason="discount_exceeds_auto_limit",
                    constraint={"discount_pct": round(discount_pct, 2)},
                    margin_pct=round(margin.margin_pct, 2),
                )

        # ── Rule 6: amount gate ───────────────────────────────
        if paid_paise > self.limits.owner_approve_above_paise:
            return Decision(
                result="GATE",
                reason="amount_exceeds_auto_limit",
                constraint={"paid_paise": paid_paise},
                margin_pct=round(margin.margin_pct, 2),
            )

        # ── ALLOW ─────────────────────────────────────────────
        self.velocity.record()
        return Decision(
            result="ALLOW",
            reason="all_checks_passed",
            margin_pct=round(margin.margin_pct, 2),
        )
