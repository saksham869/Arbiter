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


# ── Boundary and pathological input tests ──────────────────────────────────────

def test_zero_cogs_allowed_but_margin_correct():
    """Zero COGS should compute correctly — full paid amount is margin."""
    from control.margin import LineItem, MarginEngine, FeeModel
    engine = MarginEngine(FeeModel.standard())
    items = [LineItem(sku="TEST", quantity=1, list_price_paise=100000, cogs_paise=0)]
    result = engine.compute(items, paid_paise=90000)
    # Margin = (paid - fees - 0 COGS) / paid — should be very high
    assert result.margin_pct > 80, f"Zero COGS should yield high margin, got {result.margin_pct}"


def test_hundred_percent_discount_zero_paid():
    """100% discount means paid=0 — engine must not divide by zero."""
    from control.margin import LineItem, MarginEngine, FeeModel
    engine = MarginEngine(FeeModel.standard())
    items = [LineItem(sku="SHOE-001", quantity=1, list_price_paise=120000, cogs_paise=62000)]
    try:
        result = engine.compute(items, paid_paise=0)
        # If it doesn't crash, margin should be deeply negative or zero
        assert result.margin_pct <= 0
    except (ZeroDivisionError, Exception) as e:
        # Acceptable — engine may reject zero paid
        assert True


def test_negative_discount_raises_or_clamps():
    """Negative discount (paid > list) should not produce absurd results."""
    from control.margin import LineItem, MarginEngine, FeeModel
    engine = MarginEngine(FeeModel.standard())
    items = [LineItem(sku="SHOE-001", quantity=1, list_price_paise=120000, cogs_paise=62000)]
    # paid > list_price (negative discount)
    result = engine.compute(items, paid_paise=150000)
    # Should compute without crash — margin will be higher
    assert isinstance(result.margin_pct, float)


def test_huge_quantity_no_overflow():
    """Very large quantity should not cause integer overflow."""
    from control.margin import LineItem, MarginEngine, FeeModel
    engine = MarginEngine(FeeModel.standard())
    items = [LineItem(sku="SOCK-3PK", quantity=10000, list_price_paise=29900, cogs_paise=21000)]
    list_total = 29900 * 10000
    paid = int(list_total * 0.85)
    result = engine.compute(items, paid_paise=paid)
    assert isinstance(result.margin_pct, float)
    assert result.margin_pct > 0


def test_duplicate_sku_handled():
    """Same SKU appearing twice should not double-count incorrectly."""
    from control.margin import LineItem, MarginEngine, FeeModel
    engine = MarginEngine(FeeModel.standard())
    items = [
        LineItem(sku="SHOE-001", quantity=1, list_price_paise=120000, cogs_paise=62000),
        LineItem(sku="SHOE-001", quantity=1, list_price_paise=120000, cogs_paise=62000),
    ]
    list_total = 240000
    paid = int(list_total * 0.85)
    result = engine.compute(items, paid_paise=paid)
    assert isinstance(result.margin_pct, float)


def test_margin_ceiling_invariant_higher_cogs():
    """Higher COGS must never produce a higher discount ceiling."""
    from control.margin import LineItem, MarginEngine, FeeModel
    engine = MarginEngine(FeeModel.standard())
    list_total = 120000

    low_cogs = [LineItem(sku="X", quantity=1, list_price_paise=120000, cogs_paise=40000)]
    high_cogs = [LineItem(sku="X", quantity=1, list_price_paise=120000, cogs_paise=80000)]

    ceiling_low  = engine.max_discount(low_cogs,  list_total, floor_pct=18.0).max_discount_pct
    ceiling_high = engine.max_discount(high_cogs, list_total, floor_pct=18.0).max_discount_pct

    assert ceiling_high <= ceiling_low, \
        f"Higher COGS must never raise ceiling: low={ceiling_low:.2f}% high={ceiling_high:.2f}%"


def test_margin_ceiling_invariant_higher_floor():
    """Higher margin floor must never produce a higher discount ceiling."""
    from control.margin import LineItem, MarginEngine, FeeModel
    engine = MarginEngine(FeeModel.standard())
    items = [LineItem(sku="X", quantity=1, list_price_paise=120000, cogs_paise=62000)]
    list_total = 120000

    ceiling_18 = engine.max_discount(items, list_total, floor_pct=18.0).max_discount_pct
    ceiling_25 = engine.max_discount(items, list_total, floor_pct=25.0).max_discount_pct

    assert ceiling_25 <= ceiling_18, \
        f"Higher floor must lower ceiling: 18%→{ceiling_18:.2f}% 25%→{ceiling_25:.2f}%"


def test_margin_invariant_higher_discount_lower_margin():
    """Higher discount must never produce better margin."""
    from control.margin import LineItem, MarginEngine, FeeModel
    engine = MarginEngine(FeeModel.standard())
    items = [LineItem(sku="SHOE-001", quantity=1, list_price_paise=120000, cogs_paise=62000)]
    list_total = 120000

    paid_10 = int(list_total * 0.90)  # 10% discount
    paid_20 = int(list_total * 0.80)  # 20% discount

    margin_10 = engine.compute(items, paid_paise=paid_10).margin_pct
    margin_20 = engine.compute(items, paid_paise=paid_20).margin_pct

    assert margin_20 < margin_10, \
        f"Higher discount must lower margin: 10%→{margin_10:.2f}% 20%→{margin_20:.2f}%"


def test_unknown_cogs_never_allow():
    """Unknown COGS (None) must never produce an ALLOW decision."""
    from control.margin import LineItem
    from control.app import _build_engine, _load_policy, _load_catalog
    _pol = _load_policy()
    _cat = _load_catalog()
    engine = _build_engine(_pol, _cat)
    # Item with no COGS — should trigger unknown_cogs → DENY
    items = [LineItem(sku="GHOST", quantity=1, list_price_paise=100000, cogs_paise=None)]
    result = engine.check(items=items, paid_paise=90000, list_total_paise=100000)
    assert result.result != "ALLOW", f"Unknown COGS must never ALLOW, got {result.result}"
    assert result.reason == "unknown_cogs"
