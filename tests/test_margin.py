"""
tests/test_margin.py
20 tests for MarginEngine.
Run: python3 -m pytest tests/test_margin.py -v
"""
import pytest
from control.margin import (
    FeeModel, LineItem, MarginEngine,
    MarginResult, MaxDiscount, UnknownCogsError,
)


# ── Fixtures ──────────────────────────────────────────────────
@pytest.fixture
def fee():
    return FeeModel.standard()

@pytest.fixture
def engine(fee):
    return MarginEngine(fee)

@pytest.fixture
def shoe():
    return LineItem(sku="SHOE-001", quantity=1,
                    list_price_paise=100000, cogs_paise=62000)

@pytest.fixture
def sock():
    return LineItem(sku="SOCK-3PK", quantity=1,
                    list_price_paise=34900, cogs_paise=21000)


# ── FeeModel ──────────────────────────────────────────────────
def test_effective_pct(fee):
    assert abs(fee.effective_pct - 2.36) < 0.001

def test_retention_factor(fee):
    assert abs(fee.retention_factor - 0.9764) < 0.0001

def test_invalid_platform_fee():
    with pytest.raises(ValueError):
        FeeModel(platform_fee_pct=101, gst_on_fee_pct=18, mdr_refundable=False)

def test_invalid_gst():
    with pytest.raises(ValueError):
        FeeModel(platform_fee_pct=2, gst_on_fee_pct=-1, mdr_refundable=False)


# ── LineItem ──────────────────────────────────────────────────
def test_total_list_price(shoe):
    assert shoe.total_list_price_paise == 100000

def test_total_cogs(shoe):
    assert shoe.total_cogs_paise == 62000

def test_unknown_cogs_raises():
    item = LineItem(sku="X", quantity=1, list_price_paise=1000, cogs_paise=None)
    with pytest.raises(UnknownCogsError) as exc:
        _ = item.total_cogs_paise
    assert exc.value.sku == "X"

def test_blank_sku_raises():
    with pytest.raises(ValueError):
        LineItem(sku="", quantity=1, list_price_paise=1000, cogs_paise=500)

def test_zero_quantity_raises():
    with pytest.raises(ValueError):
        LineItem(sku="X", quantity=0, list_price_paise=1000, cogs_paise=500)


# ── MarginEngine.compute ──────────────────────────────────────
def test_single_item_fee_math(engine, shoe):
    result = engine.compute([shoe], paid_paise=100000)
    assert result.fee_paise   == 2000
    assert result.gst_paise   == 360
    assert result.cogs_paise  == 62000
    assert result.margin_paise == 35640

def test_single_item_above_floor(engine, shoe):
    result = engine.compute([shoe], paid_paise=100000)
    assert result.is_above_floor(18.0)

def test_deny_loop_30pct_off(engine, shoe, sock):
    """30% off bundle must breach the 18% floor — this triggers DENY."""
    paid   = int(134900 * 0.70)   # 94430
    result = engine.compute([shoe, sock], paid_paise=paid)
    assert result.margin_pct < 18.0, \
        f"Expected below floor, got {result.margin_pct:.2f}%"

def test_allow_loop_18pct_off(engine, shoe, sock):
    """18% off bundle must clear the floor — this triggers ALLOW."""
    paid   = int(134900 * 0.82)   # 110618
    result = engine.compute([shoe, sock], paid_paise=paid)
    assert result.margin_pct >= 18.0, \
        f"Expected above floor, got {result.margin_pct:.2f}%"

def test_negative_margin(engine, shoe):
    result = engine.compute([shoe], paid_paise=10000)
    assert result.is_negative()

def test_zero_paid_raises(engine, shoe):
    with pytest.raises(ValueError):
        engine.compute([shoe], paid_paise=0)

def test_multi_item_cogs_summed(engine, shoe, sock):
    result = engine.compute([shoe, sock], paid_paise=134900)
    assert result.cogs_paise == 83000

def test_shipping_reduces_margin(engine, shoe):
    without  = engine.compute([shoe], paid_paise=100000, shipping_paise=0)
    with_    = engine.compute([shoe], paid_paise=100000, shipping_paise=5000)
    assert with_.margin_paise == without.margin_paise - 5000

def test_unknown_cogs_raises_in_compute(engine):
    item = LineItem(sku="X", quantity=1, list_price_paise=10000, cogs_paise=None)
    with pytest.raises(UnknownCogsError):
        engine.compute([item], paid_paise=10000)


# ── MarginEngine.max_discount ─────────────────────────────────
def test_ceiling_bundle(engine, shoe, sock):
    """Ceiling for shoe+sock bundle at 18% floor must be ~22.74%."""
    result = engine.max_discount([shoe, sock], list_total_paise=134900, floor_pct=18.0)
    assert 22.0 < result.max_discount_pct < 23.5, \
        f"Expected ~22.74%, got {result.max_discount_pct}%"

def test_ceiling_paid_floor_positive(engine, shoe, sock):
    result = engine.max_discount([shoe, sock], list_total_paise=134900, floor_pct=18.0)
    assert result.paid_floor_paise > 0

def test_ceiling_unknown_cogs_raises(engine):
    item = LineItem(sku="X", quantity=1, list_price_paise=10000, cogs_paise=None)
    with pytest.raises(UnknownCogsError):
        engine.max_discount([item], list_total_paise=10000, floor_pct=18.0)
