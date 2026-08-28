"""
control/ledger.py
Tamper-evident evidence ledger.
Append-only. Hash-chained. Never UPDATE or DELETE.

Key design decision: margin_pct stored as VARCHAR(16) not DECIMAL.
Reason: DECIMAL columns come back from MariaDB as Python Decimal('9.750'),
not float 9.75. str(Decimal('9.750')) != str(9.75). Hash mismatch.
VARCHAR stores and returns the exact string we put in. No type conversion.
"""
from __future__ import annotations
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv(
    "DB_URL",
    "mysql+pymysql://marginguard:marginguard@localhost:3307/marginguard"
)
_engine = create_engine(DB_URL, pool_pre_ping=True)


# ── Schema ────────────────────────────────────────────────────

def _init_db():
    with _engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS action_log (
                id              VARCHAR(36)  NOT NULL PRIMARY KEY,
                ts              DATETIME(6)  NOT NULL,
                merchant_id     VARCHAR(64)  NOT NULL,
                attempt_no      INT          NOT NULL DEFAULT 1,
                parent_id       VARCHAR(36)  NULL,
                tool            VARCHAR(64)  NOT NULL,
                args_json       TEXT         NOT NULL,
                args_hash       CHAR(64)     NOT NULL,
                amount_paise    BIGINT       NULL,
                model           VARCHAR(64)  NULL,
                prompt_hash     CHAR(64)     NULL,
                decision        VARCHAR(8)   NOT NULL,
                reason          VARCHAR(255) NULL,
                constraint_json TEXT         NULL,
                margin_pct      VARCHAR(16)  NULL,
                token           VARCHAR(36)  NULL,
                exec_status     VARCHAR(12)  NOT NULL DEFAULT 'NOT_RUN',
                rzp_entity_id   VARCHAR(64)  NULL,
                prev_hash       CHAR(64)     NULL,
                row_hash        CHAR(64)     NOT NULL,
                UNIQUE KEY uq_idem (tool, args_hash, token)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS quarantine (
                id          VARCHAR(36)  NOT NULL PRIMARY KEY,
                action_id   VARCHAR(36)  NOT NULL,
                http_status INT          NULL,
                error_body  TEXT         NULL,
                resolved    TINYINT(1)   NOT NULL DEFAULT 0
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))


# ── Hash chain ────────────────────────────────────────────────

def _margin_str(v) -> str:
    """
    Canonical string for margin_pct.
    Called on WRITE only. The result is stored as VARCHAR and read back
    as-is — no conversion on read.

    float 9.75        -> '9.75'
    float 22.614...   -> '22.61'
    None              -> ''
    """
    if v is None:
        return ""
    try:
        return str(round(float(v), 2))
    except (TypeError, ValueError):
        return ""


def _compute_hash(
    prev_hash: Optional[str],
    row_id: str,
    ts: str,
    tool: str,
    args_hash: str,
    decision: str,
    reason: str,
    margin_pct_str: str,
    constraint_json: str,
) -> str:
    """
    Deterministic hash over all tamper-sensitive fields.

    margin_pct_str is the VARCHAR string stored in the DB.
    On write: _margin_str(float) -> string -> stored -> hashed.
    On read:  string read from VARCHAR -> hashed directly.
    No conversion on read = no type mismatch.
    """
    payload = (
        f"{prev_hash or ''}"
        f"|{row_id}"
        f"|{ts}"
        f"|{tool}"
        f"|{args_hash}"
        f"|{decision}"
        f"|{reason or ''}"
        f"|{margin_pct_str}"
        f"|{constraint_json or ''}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _get_last_hash() -> Optional[str]:
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT row_hash FROM action_log "
            "ORDER BY ts DESC, id DESC LIMIT 1"
        )).fetchone()
        return row[0] if row else None


# ── Public API ────────────────────────────────────────────────

@dataclass
class LedgerEntry:
    id: str
    ts: str
    merchant_id: str
    tool: str
    args_hash: str
    decision: str
    reason: str
    row_hash: str
    margin_pct: Optional[float] = None
    constraint_json: Optional[str] = None
    exec_status: str = "NOT_RUN"
    rzp_entity_id: Optional[str] = None
    attempt_no: int = 1
    parent_id: Optional[str] = None


def append(
    merchant_id: str,
    tool: str,
    args: dict,
    decision: str,
    reason: str,
    constraint: Optional[dict] = None,
    margin_pct: Optional[float] = None,
    model: Optional[str] = None,
    prompt_hash: Optional[str] = None,
    amount_paise: Optional[int] = None,
    attempt_no: int = 1,
    parent_id: Optional[str] = None,
) -> LedgerEntry:
    _init_db()

    row_id    = str(uuid.uuid4())
    ts        = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
    args_json = json.dumps(args, sort_keys=True)
    args_hash = hashlib.sha256(args_json.encode()).hexdigest()
    cj        = json.dumps(constraint) if constraint else None
    prev_hash = _get_last_hash()

    # Convert margin to string ONCE here. This is the only place.
    margin_str = _margin_str(margin_pct)

    row_hash = _compute_hash(
        prev_hash=prev_hash,
        row_id=row_id,
        ts=ts,
        tool=tool,
        args_hash=args_hash,
        decision=decision,
        reason=reason or "",
        margin_pct_str=margin_str,
        constraint_json=cj or "",
    )

    with _engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO action_log
              (id, ts, merchant_id, attempt_no, parent_id, tool,
               args_json, args_hash, amount_paise, model, prompt_hash,
               decision, reason, constraint_json, margin_pct,
               exec_status, prev_hash, row_hash)
            VALUES
              (:id, :ts, :merchant_id, :attempt_no, :parent_id, :tool,
               :args_json, :args_hash, :amount_paise, :model, :prompt_hash,
               :decision, :reason, :constraint_json, :margin_pct,
               'NOT_RUN', :prev_hash, :row_hash)
        """), {
            "id":             row_id,
            "ts":             ts,
            "merchant_id":    merchant_id,
            "attempt_no":     attempt_no,
            "parent_id":      parent_id,
            "tool":           tool,
            "args_json":      args_json,
            "args_hash":      args_hash,
            "amount_paise":   amount_paise,
            "model":          model,
            "prompt_hash":    prompt_hash,
            "decision":       decision,
            "reason":         reason,
            "constraint_json": cj,
            "margin_pct":     margin_str,   # VARCHAR string, not float
            "prev_hash":      prev_hash,
            "row_hash":       row_hash,
        })

    return LedgerEntry(
        id=row_id,
        ts=ts,
        merchant_id=merchant_id,
        tool=tool,
        args_hash=args_hash,
        decision=decision,
        reason=reason,
        margin_pct=margin_pct,
        constraint_json=cj,
        row_hash=row_hash,
        attempt_no=attempt_no,
        parent_id=parent_id,
    )


def finalize(
    action_id: str,
    exec_status: str,
    rzp_entity_id: Optional[str] = None,
):
    """Update execution result after Razorpay call completes."""
    with _engine.begin() as conn:
        conn.execute(text("""
            UPDATE action_log
               SET exec_status  = :status,
                   rzp_entity_id = :rzp_id
             WHERE id = :id
        """), {
            "status": exec_status,
            "rzp_id": rzp_entity_id,
            "id":     action_id,
        })


def quarantine(
    action_id: str,
    http_status: Optional[int],
    error_body: str,
):
    """Record an UNKNOWN outcome for human resolution."""
    with _engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO quarantine (id, action_id, http_status, error_body)
            VALUES (:id, :action_id, :http_status, :error_body)
        """), {
            "id":          str(uuid.uuid4()),
            "action_id":   action_id,
            "http_status": http_status,
            "error_body":  error_body[:2000],
        })


def verify() -> dict:
    """
    Walk the entire chain. Recompute every hash.
    Returns {intact: bool, broken_at: id|None, checked: int}

    margin_pct is read as VARCHAR string — passed directly to _compute_hash.
    No float conversion. No Decimal conversion. Exact match guaranteed.
    """
    _init_db()

    with _engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, ts, tool, args_hash, decision, reason,
                   margin_pct, constraint_json, prev_hash, row_hash
              FROM action_log
             ORDER BY ts ASC, id ASC
        """)).fetchall()

    if not rows:
        return {"intact": True, "broken_at": None, "checked": 0}

    prev_hash = None
    for row in rows:
        (row_id, ts, tool, args_hash, decision, reason,
         margin_pct_str, constraint_json,
         stored_prev, stored_hash) = row

        # margin_pct_str is VARCHAR — comes back as str or None
        # pass it directly, no conversion
        expected = _compute_hash(
            prev_hash=prev_hash,
            row_id=row_id,
            ts=str(ts),
            tool=tool,
            args_hash=args_hash,
            decision=decision,
            reason=reason or "",
            margin_pct_str=margin_pct_str or "",
            constraint_json=constraint_json or "",
        )

        if expected != stored_hash:
            return {
                "intact":    False,
                "broken_at": row_id,
                "checked":   len(rows),
            }
        prev_hash = stored_hash

    return {"intact": True, "broken_at": None, "checked": len(rows)}


def get_all(limit: int = 50) -> list[dict]:
    _init_db()
    with _engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, ts, tool, decision, reason, margin_pct,
                   exec_status, rzp_entity_id, attempt_no, constraint_json
              FROM action_log
             ORDER BY ts DESC
             LIMIT :limit
        """), {"limit": limit}).fetchall()
    return [dict(r._mapping) for r in rows]


def get_one(action_id: str) -> Optional[dict]:
    _init_db()
    with _engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM action_log WHERE id = :id"
        ), {"id": action_id}).fetchone()
    return dict(row._mapping) if row else None
