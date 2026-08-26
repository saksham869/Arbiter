# margin-guard

**Razorpay AI Buildathon 2026 — Track 01: AI Growth & Agentic Commerce**

An AI agent that grows merchant revenue through upsell bundles
with a deterministic control plane that ensures every discount is
economically safe before it executes.

---

## The insight

Razorpay's order payload carries price for every product.
It has no field for cost.

Without cost -> no margin. Without margin -> no safe discount floor.
Without a floor -> an AI agent silently destroys merchant money.

A merchant CSV supplies the cost. margin-guard joins them.
That join does not exist in Razorpay's stack today.

---

## The DENY loop

    Agent proposes:  bundle shoes + socks at 30% off
    Control plane:   DENY - margin 9.75% < 18% floor
                     ceiling is 22.74%
    Agent replans:   bundle at 18% off
    Control plane:   ALLOW - margin 22.61%
    Razorpay:        create_order -> real order created
    Ledger:          both decisions recorded, hash-chained

---

## Three truths that never collapse

    AI belief        "30% off will sell"
         not equal to
    Authorization    "floor allows max 22.74%"
         not equal to
    Payment outcome  "503 -> UNKNOWN -> quarantine"

---

## Results (holdout data, n=50)

| Metric | Value |
|---|---|
| Orders processed | 50 |
| Converted | 44 (88%) |
| Denied | 6 (return risk - SHIRT-1 bundles) |
| Avg margin on converted | 25.21% |
| Avg discount | 15.52% |
| Adversary scenarios | 8/8 DENY with named rule |
| Chain integrity | intact (167 entries) |

---

## Quickstart

    pip3 install -r requirements.txt
    docker compose up -d
    cp .env.example .env
    python3 -m uvicorn control.app:app --port 8085
    python3 -m agent.agent holdout
    python3 -m pytest tests/ -v

---

## Architecture

    MERCHANT catalog.csv (products + costs)
        +
    RAZORPAY order history
        |
        v
    AGENT (agent/agent.py) - LLM lives here only
        | POST /control/propose
        v
    CONTROL PLANE (control/) - no LLM inside
      MarginEngine -> PolicyEngine -> Decision
        |
      DENY -> agent replans
      ALLOW -> ExecutionService -> Razorpay API
        |
      EvidenceLedger (hash-chained, tamper-evident)

The governing rule: agent.py has no Razorpay import.
Its only outbound call is POST /control/propose.

---

## API

    POST /control/propose          agent submits proposals
    POST /control/margin/ceiling   agent queries max safe discount
    GET  /control/audit            list all decisions
    GET  /control/audit/verify     chain integrity check
    GET  /control/health           status

---

## What I did not build

- Angular console (JSON endpoints only)
- Webhook listener (polling only)
- Multi-model buyer scorer
- Natural language policy editor

None of these are in the bar sentence. The bar is fully satisfied.

---

## Four constraints found on Day 1

| Constraint | Response |
|---|---|
| line_items needs Magic Checkout | notes channel is primary |
| offers param not in core API ref | discount via amount directly |
| UPI Reserve Pay unavailable | create_order covers all paths |
| MDR refundability ambiguous | config flag, default false |

---

## Track 01 bar

Every money action explainable, bounded and gated.
Show the audit trail and one failure handled gracefully.

- Explainable: model, reason, constraint logged on every action
- Bounded: margin floor from real fee math (2% + 18% GST)
- Gated: return risk deny, amount gate, velocity limit
- Audit trail: hash-chained, /verify detects tampering
- Failure: 5xx -> UNKNOWN -> quarantine, never assumed SUCCESS
