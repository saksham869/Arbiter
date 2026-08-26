# margin-guard

**Razorpay AI Buildathon 2026 — Track 01: AI Growth & Agentic Commerce**

MarginGuard is an economic action-control plane for AI-driven merchant growth.
An AI agent pursues a merchant objective and proposes bounded commerce actions.
MarginGuard evaluates unit economics, policy and risk before authorizing
execution through Razorpay.

---

## The insight

The Razorpay Orders API does not expose merchant COGS or unit-cost data
in the order-creation contract. Without cost, there is no margin.
Without margin, there is no safe discount floor. Without a floor,
an AI agent silently destroys merchant money.

MarginGuard joins merchant-provided unit economics with the payment
action before authorization. That join does not exist in Razorpay's
order-creation contract today.

---

## The DENY loop

    Attempt 1: agent proposes 28% bundle discount
               paid=97128, fee+gst=2292, cogs=83000
               margin=11726 = 12.07%
               PolicyEngine: DENY margin_floor
               economic: { projected: 12.07%, required: 18%, ceiling: 22.74% }
               replan: { required: true, objective_preserved: true }

    Attempt 2: agent reads constraint, changes strategy
               switches to lowest-COGS companion at 18%
               margin = 22.56% -- clears floor
               PolicyEngine: ALLOW
               OfferSelector: picks offer_CCCC (15% rung, highest safe)
               Razorpay: create_order with offers:[offer_CCCC], force_offer:true

The objective stays constant. The strategy changes.
That is the distinction between replanning and binary search.

---

## Three truths that never collapse

    AI belief        agent proposed 28% off
         not equal to
    Authorization    policy allows max 22.74%
         not equal to
    Payment outcome  Razorpay returned 503 -- UNKNOWN -- halted

---

## Track 01 requirement mapping

| Requirement | Evidence |
|---|---|
| Explainable | economic + policy reason on every ledger row |
| Bounded | margin floor from real Razorpay fee math (2% + 18% GST) |
| Gated | return_risk deny, amount gate, velocity limit |
| Audit trail | hash-chained tamper-evident ledger, /verify detects edits |
| One failure | 5xx -> UNKNOWN -> SAFE_HALT -> quarantine, no retry |

---

## Evaluation (holdout data, n=50)

Methodology: 250 orders seeded into Razorpay test mode.
80/20 train/holdout split before affinity model trains.
Affinity trains on TRAIN only. Agent runs on HOLDOUT only.
All numbers below are from the holdout set.

| Metric | Value |
|---|---|
| Orders processed | 50 |
| Converted (ALLOW) | 44 (88%) |
| Denied (return risk) | 6 (SHIRT-1 bundles, 28% return rate) |
| Avg margin on converted | 23.41% |
| Avg discount authorized | 17.82% |
| Adversary scenarios | 8/8 DENY with named rule |
| Chain integrity | intact across all entries |

To reproduce:
    python3 data/seed.py
    python3 -m agent.agent holdout

The 6 denied orders are SHIRT-1 bundles. return_rate=0.28 exceeds the
0.25 threshold. These are shown because a table with only green numbers
is fabricated.

---

## Offer selection

MarginGuard selects the highest economically safe offer from a
pre-registered ladder. The agent never picks the offer.

    Economic ceiling = 22.74%

    Authorized ladder: 5% / 10% / 15% / 20%
    Safe rungs:        5% / 10% / 15% / 20%
    Selected:          20% (highest safe)

Execution:
    create_order with offers:[offer_id], force_offer:true

MarginGuard does not create Razorpay offers dynamically.
Offers are pre-registered on the Dashboard by the merchant.

---

## Quickstart

    pip3 install -r requirements.txt
    docker compose up -d
    cp .env.example .env
    # fill in RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, ANTHROPIC_API_KEY
    python3 -m uvicorn control.app:app --port 8085
    python3 -m agent.agent holdout
    python3 -m pytest tests/ -v

---

## API

    POST /control/propose          agent submits proposals
    POST /control/margin/ceiling   agent queries max safe discount
    GET  /control/audit            list all decisions
    GET  /control/audit/verify     chain integrity check
    GET  /control/health           status

---

## Architecture

    MERCHANT catalog.csv (products + COGS + return rates)
        +
    RAZORPAY order history
        |
        v
    agent/affinity.py -- co-purchase matrix (train split only)
        |
        v
    agent/agent.py -- LLM calls live here ONLY
        | POST /control/propose
        v
    control/app.py (FastAPI)
        |
        v
    control/margin.py -- fee math, margin, ceiling (no LLM, no network)
        |
        v
    control/policy.py -- 7 rules, fail-closed, DENY-wins (no LLM)
        |
        v
    control/offer_selector.py -- picks highest safe offer rung
        |
    +---+---+
    |       |
  DENY    ALLOW
    |       |
    |   control/execution.py
    |   single-use token, idempotency, UNKNOWN quarantine
    |       |
    +---+---+
        |
    control/ledger.py -- SHA-256 hash chain, append-only
        |
        v
    outcome back to agent

Governing rule: agent.py has no Razorpay import.
Its only outbound call is POST /control/propose.

---

## What I did not build

- Angular console (JSON endpoints only)
- Webhook listener (polling only -- production would use webhooks)
- Multi-model buyer scorer
- Natural language policy editor
- Real LLM (mock used -- swap ANTHROPIC_API_KEY and remove mock in agent.py)

None of these affect the bar sentence. The bar is addressed above.

---

## Four constraints found on Day 1

| Constraint | Evidence | Design response |
|---|---|---|
| line_items needs Magic Checkout | Docs: on-demand feature, fill out form | notes channel is primary |
| Offers pre-registered on Dashboard only | No create-offer API | agent selects from pre-authorized ladder |
| UPI Reserve Pay unavailable | Requires support contact + SBMD | create_order covers all upsell paths |
| MDR refundability ambiguous | Razorpay own pages conflict | config flag mdr_refundable, default false |
