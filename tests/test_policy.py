"""
tests/test_policy.py
20 tests for PolicyEngine.
Run: python3 -m pytest tests/test_policy.py -v
"""
import pytest
from control.margin import FeeModel, LineItem
from control.policy import PolicyEngine, MerchantLimits


# ── Fixtures ──────────────────────────────────────────────────
@pytest.fixture
def catalog():
    return {
        "SHOE-001": {"cogs_paise": 62000, "return_rate": 0.22},
        "SOCK-3PK": {"cogs_paise": 21000, "return_rate": 0.04},
        "SHIRT-1":  {"cogs_paise": 47900, "return_rate": 0.28},
    }

@pytest.fixture
def limits():
    return MerchantLimits()

@pytest.fixture
def engine(limits, catalog):
    return PolicyEngine(limits, FeeModel.standard(), catalog)

@pytest.fixture
def shoe():
    return LineItem(sku="SHOE-001", quantity=1,
                    list_price_paise=100000, cogs_paise=62000)

@pytest.fixture
def sock():
    return LineItem(sku="SOCK-3PK", quantity=1,
                    list_price_paise=34900, cogs_paise=21000)

@pytest.fixture
def shirt():
    return LineItem(sku="SHIRT-1", quantity=1,
                    list_price_paise=79900, cogs_paise=47900)


# ── ALLOW cases ───────────────────────────────────────────────
def test_allow_profitable_single(engine, shoe):
    d = engine.check([shoe], paid_paise=100000, list_total_paise=100000)
    assert d.result == "ALLOW"

def test_allow_bundle_18pct_off(engine, shoe, sock):
    paid = int(134900 * 0.82)
    d = engine.check([shoe, sock], paid_paise=paid, list_total_paise=134900)
    assert d.result == "ALLOW"
    assert d.margin_pct >= 18.0

def test_allow_records_margin_pct(engine, shoe):
    d = engine.check([shoe], paid_paise=100000, list_total_paise=100000)
    assert d.margin_pct is not None
    assert d.margin_pct > 0


# ── DENY: margin floor ────────────────────────────────────────
def test_deny_30pct_off_bundle(engine, shoe, sock):
    """Core DENY loop scenario."""
    paid = int(134900 * 0.70)
    d = engine.check([shoe, sock], paid_paise=paid, list_total_paise=134900)
    assert d.result == "DENY"
    assert d.reason == "margin_floor"

def test_deny_provides_ceiling(engine, shoe, sock):
    """DENY must include max_discount_pct so agent can replan."""
    paid = int(134900 * 0.70)
    d = engine.check([shoe, sock], paid_paise=paid, list_total_paise=134900)
    assert "max_discount_pct" in d.constraint
    assert d.constraint["max_discount_pct"] > 0

def test_deny_negative_margin(engine, shoe):
    d = engine.check([shoe], paid_paise=10000, list_total_paise=100000)
    assert d.result == "DENY"
    assert d.reason == "margin_floor"


# ── DENY: return risk ─────────────────────────────────────────
def test_deny_high_return_rate(engine, shirt, sock):
    """SHIRT-1 has 28% return rate — above 25% threshold."""
    paid = int(114800 * 0.85)
    d = engine.check([shirt, sock], paid_paise=paid, list_total_paise=114800)
    assert d.result == "DENY"
    assert d.reason == "return_risk"

def test_deny_return_risk_names_sku(engine, shirt, sock):
    paid = int(114800 * 0.85)
    d = engine.check([shirt, sock], paid_paise=paid, list_total_paise=114800)
    assert d.constraint.get("sku") == "SHIRT-1"


# ── DENY: unknown COGS ────────────────────────────────────────
def test_deny_unknown_cogs(engine):
    item = LineItem(sku="UNKNOWN-SKU", quantity=1,
                    list_price_paise=50000, cogs_paise=None)
    d = engine.check([item], paid_paise=50000, list_total_paise=50000)
    assert d.result == "DENY"
    assert d.reason == "unknown_cogs"


# ── GATE cases ────────────────────────────────────────────────
def test_gate_discount_above_20pct(engine, shoe, sock):
    """22% off is above the 20% auto-approve threshold."""
    paid = int(134900 * 0.78)
    d = engine.check([shoe, sock], paid_paise=paid, list_total_paise=134900)
    assert d.result == "GATE"
    assert d.reason == "discount_exceeds_auto_limit"

def test_gate_large_amount(engine, shoe):
    """Amounts above owner_approve_above_paise go to GATE."""
    limits = MerchantLimits(owner_approve_above_paise=50000)
    eng    = PolicyEngine(limits, FeeModel.standard(),
                          {"SHOE-001": {"cogs_paise": 62000, "return_rate": 0.22}})
    d = eng.check([shoe], paid_paise=100000, list_total_paise=100000)
    assert d.result == "GATE"
    assert d.reason == "amount_exceeds_auto_limit"


# ── Fail-closed ───────────────────────────────────────────────
def test_fail_closed_on_bad_input(engine):
    """Passing garbage must never return ALLOW."""
    d = engine.check([], paid_paise=-1, list_total_paise=0)
    assert d.result == "DENY"
    assert "policy_engine_error" in d.reason or d.reason in (
        "margin_floor", "unknown_cogs", "velocity_limit"
    )


# ── Velocity ──────────────────────────────────────────────────
def test_velocity_blocks_after_limit():
    """After 10 ALLOWs, the 11th must be DENY velocity_limit."""
    limits = MerchantLimits(max_actions_per_60s=3)
    cat    = {"SHOE-001": {"cogs_paise": 62000, "return_rate": 0.22}}
    eng    = PolicyEngine(limits, FeeModel.standard(), cat)
    shoe   = LineItem(sku="SHOE-001", quantity=1,
                      list_price_paise=100000, cogs_paise=62000)

    for _ in range(3):
        d = eng.check([shoe], paid_paise=100000, list_total_paise=100000)
        assert d.result == "ALLOW"

    d = eng.check([shoe], paid_paise=100000, list_total_paise=100000)
    assert d.result == "DENY"
    assert d.reason == "velocity_limit"


# ── Decision ordering ─────────────────────────────────────────
def test_deny_beats_gate(engine, shirt):
    """Return risk (DENY) must win over amount gate (GATE)."""
    paid = int(79900 * 0.78)
    d = engine.check([shirt], paid_paise=paid, list_total_paise=79900)
    assert d.result == "DENY"
    assert d.reason == "return_risk"
