# margin-guard — Architecture

## Core principle

The agent layer has no direct Razorpay import.
Its only outbound call is POST /control/propose.
Verifiable by reading one file: grep -n "razorpay" agent/agent.py

## System diagram

    MERCHANT
    catalog.csv
    sku, name, category, price_paise, cogs_paise, return_rate
         +
    RAZORPAY order history
    fetch_all_orders -> basket co-purchase data
         |
         v
    agent/affinity.py
    builds co-purchase frequency matrix
    trains on TRAIN split only (80%)
         |
         v
    agent/agent.py  <-----------+
    LLM calls live here only    |
    observe -> discover ->      |
    plan -> propose -> replan   |
         |                      |
         | POST /control/propose |
         v                      |
    control/app.py (FastAPI)    |
         |                      |
         v                      |
    control/margin.py           |
    Pure arithmetic. No LLM.    |
    No network. No database.    |
    fee = paid x 2%             |
    gst = fee x 18%             |
    cogs = sum(catalog[sku])    |
    margin = paid-fee-gst-cogs  |
    ceiling (closed-form):      |
      k = 1 - 0.0236 = 0.9764  |
      paid_min = cogs/(k-floor) |
      max_disc = 1-paid_min/list|
         |                      |
         v                      |
    control/policy.py           |
    7 rules, deterministic.     |
    No LLM. Fail-closed.        |
    DENY always wins.           |
    1. unknown_cogs -> DENY     |
    2. return_rate>25% -> DENY  |
    3. margin<floor -> DENY     |
       +constraint{ceiling}     |
    4. discount>20% -> GATE     |
    5. amount>limit -> GATE     |
    6. velocity>10/min -> DENY  |
    7. default -> ALLOW         |
         |                      |
    +----+----+--------+        |
    |    |    |        |        |
  DENY  GATE ALLOW              |
    |    |    |                 |
    |    |    v                 |
    |    | control/offer_selector.py
    |    | picks highest safe rung:
    |    |   ceiling = 22.74%   |
    |    |   ladder: 5/10/15/20%|
    |    |   eligible: <=22.74% |
    |    |   selected: 20%      |
    |    |    |                 |
    |    |    v                 |
    |    | control/execution.py |
    |    | mint single-use token|
    |    | idempotency check    |
    |    | call Razorpay        |
    |    |   offers:[offer_id]  |
    |    |   force_offer:true   |
    |    | 2xx -> SUCCESS       |
    |    | 4xx -> FAILED        |
    |    | 5xx -> UNKNOWN       |
    |    |        -> quarantine |
    |    |        -> SAFE_HALT  |
    |    |        -> no retry   |
    |    |    |                 |
    +----+----+                 |
         |                      |
         v                      |
    control/ledger.py           |
    SHA-256 hash chain          |
    append-only                 |
    every decision recorded     |
    DENY rows as prominent      |
    as ALLOW rows               |
    /verify detects tampering   |
         |                      |
         +-- outcome -----------+
              back to agent

## The DENY loop (centrepiece)

    Attempt 1
    Agent: "Bundle SHOE-001 + SOCK-3PK at 28% off"
    MarginEngine:
      paid    = 97128 paise
      fee+gst = 2292 paise
      cogs    = 83000 paise
      margin  = 11836 = 12.19%
    PolicyEngine: DENY
      reason: margin_floor
      constraint: { max_discount_pct: 22.74 }
      economic:
        projected_margin_pct: 12.19
        required_margin_pct: 18.0
        maximum_safe_discount: 22.74
      replan: { required: true, objective_preserved: true }

    Attempt 2 (strategic replan)
    Agent reads constraint.
    Switches to lowest-COGS companion.
    Selects 18% -- within ceiling, under 20% gate.
    MarginEngine:
      margin = 22.56% -- clears floor
    PolicyEngine: ALLOW
    OfferSelector:
      ceiling = 22.74%
      ladder  = [5, 10, 15, 20]
      safe    = [5, 10, 15, 20]
      selected: 20% (offer_DDDD)
    Razorpay:
      create_order(offers=[offer_DDDD], force_offer=true)
      -> order_XXXXXXXX

## The failure demo

    ExecutionService calls create_order
    Razorpay returns 503
    exec_status = UNKNOWN (never SUCCESS, never FAILED)
    quarantine row inserted
    SAFE_HALT: no retry, no assumption
    /audit/verify: CHAIN INTACT
    /audit/{id}: exec_status=UNKNOWN

    Language: "Execution state unknown. Autonomous action halted.
    No retry performed. Manual resolution required."

    Never say: "No charge occurred." -- we do not know.

## Three truths

    AI belief        "28% off will sell"
    not equal to
    Authorization    "ceiling is 22.74%, selected offer is 20%"
    not equal to
    Payment outcome  "Razorpay 503 -> UNKNOWN"

## File map

    control/
      margin.py          pure arithmetic, no network, no LLM
      policy.py          7 rules, deterministic, fail-closed
      offer_selector.py  picks highest safe offer rung
      ledger.py          SHA-256 hash chain, append-only
      execution.py       single-use token, UNKNOWN quarantine
      razorpay_client.py read-only Razorpay calls
      app.py             FastAPI, all endpoints

    agent/
      affinity.py        co-purchase matrix, pure pandas
      agent.py           LLM calls, DENY loop, strategic replan
      prompts/
        propose.txt      first-attempt prompt template
        replan.txt       replan-after-DENY prompt template

    tests/
      test_margin.py     21 tests
      test_policy.py     14 tests
      test_ledger.py     12 tests
      adversary/         8 scenarios, each DENY with named rule

    data/
      catalog.csv        15 SKUs with COGS and return rates
      seed.py            creates 250 orders in Razorpay test mode
      orders_train.json  200 orders (affinity trains here)
      orders_holdout.json 50 orders (all measurement here)

## Offer selection

    Merchant pre-registers offers on Razorpay Dashboard.
    MarginGuard selects among them.
    Agent never picks the offer.

    authorized: [5%, 10%, 15%, 20%]
    ceiling:    22.74%
    safe:       [5%, 10%, 15%, 20%] (all under ceiling)
    selected:   20% (highest safe)

    create_order:
      offers: [selected_offer_id]
      force_offer: true

## Security boundary

    agent/ -- no Razorpay credentials
    control/ -- Razorpay credentials

    The agent cannot call razorpay.orders.create()
    even if its prompt is compromised.
    Verifiable: grep -rn "razorpay" agent/

## Adversary scenarios

    1. Prompt injection in rationale -> DENY policy_engine_error
    2. Unknown SKU (no COGS) -> DENY unknown_cogs
    3. High return rate (SHIRT-1 28%) -> DENY return_risk
    4. 30% off bundle -> DENY margin_floor + ceiling in constraint
    5. Velocity: 4 actions when limit=3 -> DENY velocity_limit
    6. Corrupted policy (floor=200%) -> DENY all (fail-closed)
    7. 5xx mid-flight -> UNKNOWN in ledger, quarantine inserted
    8. Tampered row hash -> /verify returns intact=false
