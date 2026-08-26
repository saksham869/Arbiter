"""
control/margin.py
Pure arithmetic. No network. No database. No LLM.
All money in integer paise. Never float for money.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


class UnknownCogsError(Exception):
    def __init__(self, sku: str):
        self.sku = sku
        super().__init__(f"COGS unknown for SKU [{sku}] — no discount can be authorized")


@dataclass(frozen=True)
class FeeModel:
    platform_fee_pct: float
    gst_on_fee_pct: float
    mdr_refundable: bool

    def __post_init__(self):
        if not (0 <= self.platform_fee_pct <= 100):
            raise ValueError(f"platform_fee_pct out of range: {self.platform_fee_pct}")
        if not (0 <= self.gst_on_fee_pct <= 100):
            raise ValueError(f"gst_on_fee_pct out of range: {self.gst_on_fee_pct}")

    @property
    def effective_pct(self) -> float:
        return self.platform_fee_pct * (1.0 + self.gst_on_fee_pct / 100.0)

    @property
    def retention_factor(self) -> float:
        return 1.0 - self.effective_pct / 100.0

    @staticmethod
    def standard() -> "FeeModel":
        return FeeModel(platform_fee_pct=2.0, gst_on_fee_pct=18.0, mdr_refundable=False)


@dataclass(frozen=True)
class LineItem:
    sku: str
    quantity: int
    list_price_paise: int
    cogs_paise: Optional[int]

    def __post_init__(self):
        if not self.sku or not self.sku.strip():
            raise ValueError("sku must not be blank")
        if self.quantity < 1:
            raise ValueError(f"quantity must be >= 1, got {self.quantity}")
        if self.list_price_paise < 0:
            raise ValueError(f"list_price_paise must be >= 0, got {self.list_price_paise}")

    @property
    def total_list_price_paise(self) -> int:
        return self.list_price_paise * self.quantity

    @property
    def total_cogs_paise(self) -> int:
        if self.cogs_paise is None:
            raise UnknownCogsError(self.sku)
        return self.cogs_paise * self.quantity


@dataclass(frozen=True)
class MarginResult:
    paid_paise: int
    fee_paise: int
    gst_paise: int
    cogs_paise: int
    shipping_paise: int
    margin_paise: int
    margin_pct: float
    fee_model: FeeModel

    def is_above_floor(self, floor_pct: float) -> bool:
        return self.margin_pct >= floor_pct

    def is_negative(self) -> bool:
        return self.margin_paise < 0


@dataclass(frozen=True)
class MaxDiscount:
    max_discount_pct: float
    max_discount_paise: int
    paid_floor_paise: int
    floor_pct: float


class MarginEngine:
    def __init__(self, fee_model: FeeModel):
        self.fee_model = fee_model

    def compute(
        self,
        items: list,
        paid_paise: int,
        shipping_paise: int = 0,
    ) -> MarginResult:
        if paid_paise <= 0:
            raise ValueError(f"paid_paise must be > 0, got {paid_paise}")

        fee_paise    = int(paid_paise * self.fee_model.platform_fee_pct / 100)
        gst_paise    = int(fee_paise  * self.fee_model.gst_on_fee_pct   / 100)
        total_cogs   = sum(item.total_cogs_paise for item in items)
        margin_paise = paid_paise - fee_paise - gst_paise - total_cogs - shipping_paise
        margin_pct   = margin_paise * 100.0 / paid_paise

        return MarginResult(
            paid_paise=paid_paise,
            fee_paise=fee_paise,
            gst_paise=gst_paise,
            cogs_paise=total_cogs,
            shipping_paise=shipping_paise,
            margin_paise=margin_paise,
            margin_pct=margin_pct,
            fee_model=self.fee_model,
        )

    def max_discount(
        self,
        items: list,
        list_total_paise: int,
        shipping_paise: int = 0,
        floor_pct: float = 18.0,
    ) -> MaxDiscount:
        total_cogs       = sum(item.total_cogs_paise for item in items)
        k                = self.fee_model.retention_factor
        divisor          = k - floor_pct / 100.0

        if divisor <= 0:
            raise ValueError(f"floor_pct {floor_pct} too high — no discount possible")

        paid_floor_paise   = int((total_cogs + shipping_paise) / divisor) + 1
        max_discount_pct   = max(0.0, (1.0 - paid_floor_paise / list_total_paise) * 100.0)
        max_discount_paise = max(0, list_total_paise - paid_floor_paise)

        return MaxDiscount(
            max_discount_pct=round(max_discount_pct, 2),
            max_discount_paise=max_discount_paise,
            paid_floor_paise=paid_floor_paise,
            floor_pct=floor_pct,
        )
