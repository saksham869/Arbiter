# margin-guard

**Razorpay AI Buildathon 2026 — Track 01: AI Growth & Agentic Commerce**

An AI upsell agent that grows merchant revenue — and the economic control plane
that makes it safe to run autonomously.

The agent observes order history, discovers co-purchase patterns, and proposes
bundle discounts. MarginGuard evaluates unit economics, policy and risk before
authorizing every money action through Razorpay. The agent pursues the objective.
MarginGuard decides what it is authorized to execute.

> **"The AI decides what it wants to accomplish.
> It cannot decide what financial actions it is authorized to take."**

---

## The insight

Razorpay's Orders API does not expose merchant COGS or unit-cost data
in the order-creation contract. Without cost, there is no margin.
Without margin, there is no safe discount floor.

MarginGuard joins merchant-provided unit economics with the payment
action before authorization. A merchant CSV supplies the cost.
Razorpay supplies the transaction data. The margin floor is computed
from their actual fee structure before any discount executes.

---

## The DENY loop

    Attempt 1: agent proposes 28% bundle discount
               MarginEngine: margin 12.19% < 18% floor
               PolicyEngine: DENY
               constraint: { max_discount_pct: 22.74 }
               replan: { required: true, objective_preserved: true }

    Attempt 2: agent reads constraint, changes strategy
               proposes 18% — within ceiling, under 20% gate
               MarginEngine: margin 22.61%
               PolicyEngine: ALLOW
               Razorpay: create_order → real order created

The objective stays constant. The strategy changes.
That is agent reasoning, not binary search.

---

## Three truths that never collapse

    AI belief          "28% off will drive sales"
         not equal to
    Authorization      "ceiling is 22.74%, selected offer is 20%"
         not equal to
    Payment outcome    "Razorpay 503 → UNKNOWN → SAFE_HALT"

---

## Track 01 requirement mapping

| Requirement | Evidence |
|---|---|
| Explainable | model, reason, constraint logged on every ledger row |
| Bounded | margin floor from Razorpay fee math (2% + 18% GST) |
| Gated | return_risk deny, amount gate, velocity limit |
| Audit trail | SHA-256 hash chain, /verify detects tampering |
| One failure | 5xx → UNKNOWN → SAFE_HALT → quarantine, no retry |

---

## Evaluation (holdout data, n=50)

Methodology: 250 orders seeded into Razorpay test mode.
80/20 train/holdout split before affinity model trains.
Agent runs on HOLDOUT only. All numbers below are from holdout.

| Metric | Value |
|---|---|
| Orders processed | 50 |
| Converted (ALLOW) | 41 (82%) |
| Denied | 9 (return risk — SHIRT-1 bundles, return_rate 28%) |
| Avg margin on converted | 25.68% |
| Avg discount | 14.91% |
| Adversary scenarios | 8/8 DENY with named rule |
| Chain integrity | intact |

The 9 denied orders are shown, not hidden. SHIRT-1 has a 28% return rate —
non-refundable MDR makes these conversions a loss. Correct behaviour.

Note: "converted" means ALLOW with a real Razorpay order created.
AOV lift vs a true control group requires a concurrent control cohort
running without the agent, which is outside this prototype's scope.
Results demonstrate mechanism correctness, not a proven revenue lift figure.

---

## LLM

Real Claude via AWS Bedrock: `us.anthropic.claude-haiku-4-5-20251001-v1:0`

The agent uses real inference — not a mock. Prices are always overridden
from the merchant catalog so the LLM cannot inject incorrect paise values.
The LLM chooses which companion SKU to recommend and at what discount.
The control plane decides whether that discount is economically authorized.

---

## Offer selection

MarginGuard selects the highest economically safe offer from a
pre-registered ladder. The agent never picks the offer.

    Economic ceiling = 22.74%
    Authorized: 5% / 10% / 15% / 20%
    Eligible:   all (all under ceiling)
    Selected:   20% (highest safe)

    create_order with offers:[offer_id], force_offer:true

Offers are pre-registered on the Razorpay Dashboard by the merchant.
MarginGuard does not create offers dynamically.

---

## Quickstart

    git clone https://github.com/saksham869/margin-guard
    cd margin-guard
    pip3 install -r requirements.txt
    docker compose up -d
    cp .env.example .env
    # fill RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET
    # fill AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (for Bedrock)
    python3 -m uvicorn control.app:app --port 8085
    python3 -m agent.agent holdout
    python3 -m pytest tests/ -v

---

## Architecture

    MERCHANT catalog.csv (sku, price, COGS, return_rate)
        +
    RAZORPAY order history
        |
        v
    agent/affinity.py — co-purchase matrix (train split only)
        |
        v
    agent/agent.py — Claude via AWS Bedrock
        | POST /control/propose
        v
    control/app.py (FastAPI)
        |
        v
    control/margin.py — fee math, margin, ceiling (no LLM)
        |
        v
    control/policy.py — 7 rules, fail-closed, DENY-wins (no LLM)
        |
        v
    control/offer_selector.py — highest safe offer rung
        |
    DENY → agent replans with same objective
    ALLOW → control/execution.py → Razorpay API
        |
    control/ledger.py — SHA-256 hash chain, append-only

Governing rule: agent/agent.py has no Razorpay import.
Verify: grep -n "razorpay" agent/agent.py returns nothing.

---

## API

    POST /control/propose           agent submits proposals
    POST /control/margin/ceiling    agent queries max safe discount
    GET  /control/audit             list all decisions
    GET  /control/audit/verify      chain integrity check
    GET  /control/health            status UP

---

## Four constraints found on Day 1

| Constraint | Evidence | Response |
|---|---|---|
| line_items needs Magic Checkout | Docs: on-demand feature | notes channel is primary |
| Offers Dashboard-only | No create-offer API | pre-authorized ladder |
| UPI Reserve Pay unavailable | Requires support + eligibility | create_order covers all paths |
| MDR refundability ambiguous | Razorpay docs conflict | config flag, default false |

---

## What I did not build

- Angular console (JSON endpoints only)
- Webhook listener (polling — production would use webhooks)
- Multi-model buyer scorer
- Natural language policy editor

None of these are in the bar sentence.
