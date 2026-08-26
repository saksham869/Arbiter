"""
tests/test_ledger.py
Tests for the tamper-evident ledger.
Run: python3 -m pytest tests/test_ledger.py -v
"""
import pytest
from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

load_dotenv()

import control.ledger as ledger

DB_URL = os.getenv("DB_URL", "mysql+pymysql://marginguard:marginguard@localhost:3307/marginguard")


def _clear_tables():
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM quarantine"))
        conn.execute(text("DELETE FROM action_log"))


@pytest.fixture(autouse=True)
def clean_db():
    """Start each test with empty tables."""
    ledger._init_db()
    _clear_tables()
    yield
    _clear_tables()


# ── Append ────────────────────────────────────────────────────
def test_append_returns_entry():
    entry = ledger.append(
        merchant_id="test_merchant",
        tool="create_order",
        args={"amount": 114900},
        decision="ALLOW",
        reason="all_checks_passed",
        margin_pct=22.61,
    )
    assert entry.id is not None
    assert entry.decision == "ALLOW"
    assert entry.row_hash is not None

def test_append_deny_recorded():
    entry = ledger.append(
        merchant_id="test_merchant",
        tool="create_order",
        args={"amount": 94430},
        decision="DENY",
        reason="margin_floor",
        constraint={"max_discount_pct": 22.74},
        margin_pct=9.74,
    )
    assert entry.decision == "DENY"
    assert entry.reason   == "margin_floor"

def test_append_multiple_entries():
    ledger.append("m1", "create_order", {"amount": 1000}, "DENY", "margin_floor")
    ledger.append("m1", "create_order", {"amount": 2000}, "ALLOW", "all_checks_passed")
    rows = ledger.get_all()
    assert len(rows) == 2


# ── Hash chain ────────────────────────────────────────────────
def test_chain_intact_after_appends():
    ledger.append("m1", "create_order", {"amount": 1000}, "DENY",  "margin_floor")
    ledger.append("m1", "create_order", {"amount": 2000}, "ALLOW", "all_checks_passed")
    ledger.append("m1", "create_order", {"amount": 3000}, "ALLOW", "all_checks_passed")
    result = ledger.verify()
    assert result["intact"]   == True
    assert result["broken_at"] is None
    assert result["checked"]  == 3

def test_verify_empty_db():
    result = ledger.verify()
    assert result["intact"] == True
    assert result["checked"] == 0

def test_tamper_detected():
    """Manually edit a row hash — verify() must catch it."""
    entry = ledger.append(
        "m1", "create_order", {"amount": 1000}, "ALLOW", "all_checks_passed"
    )
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE action_log SET row_hash = 'tampered_hash_value' WHERE id = :id"
        ), {"id": entry.id})

    result = ledger.verify()
    assert result["intact"]    == False
    assert result["broken_at"] == entry.id


# ── Finalize ──────────────────────────────────────────────────
def test_finalize_updates_status():
    entry = ledger.append(
        "m1", "create_order", {"amount": 1000}, "ALLOW", "all_checks_passed"
    )
    ledger.finalize(entry.id, "SUCCESS", rzp_entity_id="order_TEST123")
    row = ledger.get_one(entry.id)
    assert row["exec_status"]   == "SUCCESS"
    assert row["rzp_entity_id"] == "order_TEST123"

def test_finalize_unknown_status():
    entry = ledger.append(
        "m1", "create_order", {"amount": 1000}, "ALLOW", "all_checks_passed"
    )
    ledger.finalize(entry.id, "UNKNOWN")
    row = ledger.get_one(entry.id)
    assert row["exec_status"] == "UNKNOWN"


# ── Quarantine ────────────────────────────────────────────────
def test_quarantine_recorded():
    entry = ledger.append(
        "m1", "create_order", {"amount": 1000}, "ALLOW", "all_checks_passed"
    )
    ledger.finalize(entry.id, "UNKNOWN")
    ledger.quarantine(entry.id, http_status=503, error_body="Service Unavailable")

    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM quarantine WHERE action_id = :id"
        ), {"id": entry.id}).fetchone()
    assert row is not None
    assert row._mapping["http_status"] == 503


# ── get_one / get_all ─────────────────────────────────────────
def test_get_one_returns_entry():
    entry = ledger.append(
        "m1", "create_order", {"amount": 1000}, "ALLOW", "all_checks_passed"
    )
    row = ledger.get_one(entry.id)
    assert row is not None
    assert row["id"] == entry.id

def test_get_one_missing_returns_none():
    row = ledger.get_one("nonexistent-id")
    assert row is None

def test_get_all_returns_list():
    ledger.append("m1", "create_order", {"a": 1}, "ALLOW", "all_checks_passed")
    ledger.append("m1", "create_order", {"a": 2}, "DENY",  "margin_floor")
    rows = ledger.get_all()
    assert isinstance(rows, list)
    assert len(rows) == 2
