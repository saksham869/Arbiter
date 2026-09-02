# margin-guard

**Economic authorization layer for autonomous AI commerce agents.**

---

## The problem with AI agents and money

Every AI model running on Razorpay today knows one thing the platform doesn't: **what the merchant wants to accomplish**.

Razorpay knows what products sell for.
It doesn't know what they cost.

```
Razorpay Orders API
─────────────────────────────────────────
amount:     ₹1,349   ✓  (the selling price)
cost:       ???      ✗  (the cost of goods)
margin:     ???      ✗  (what the merchant actually keeps)
```

An AI agent that proposes discounts without knowing cost will destroy money while technically "growing sales."

```
Agent proposes: 30% off running shoes
Merchant receives:
  Paid by customer:  ₹944.30
  Razorpay fees:     ₹22.29  (2% + 18% GST)
  Cost of goods:     ₹830.00
  ─────────────────────────────
  Merchant profit:   ₹92.01   ← 9.74% margin
  Required margin:   18%      ← policy floor
  Shortfall:         ₹70.19 per sale
```

The agent "succeeded." The merchant bled.

---

## The thesis

> **The AI decides what it wants to accomplish.**
> **It cannot decide what financial actions it is authorized to take.**

MarginGuard is the layer between those two statements.

---

## What happens when you run it

```
Agent:   "I want to bundle SHOE-001 + SOCK-3PK at 30% off."

MarginGuard:
  margin    = 9.74%
  floor     = 18.00%
  ceiling   = 22.74%   ← closed-form, not guessed
  decision  = DENY
  constraint= { max_discount_pct: 22.74 }
  replan    = { required: true, objective_preserved: true }

Agent reads constraint. Changes strategy.

Agent:   "Switching to 18% — within the 22.74% ceiling.
          SOCK-3PK: affinity 0.579, medium inventory pressure."

MarginGuard:
  margin         = 22.61%
  economic_score = 70.44/100
  decision       = ALLOW

Action Passport issued:
  allowed_action:    DISCOUNT_OFFER
  max_discount_pct:  22.74
  authorized_amount: ₹1,106.18
  valid_until:       +5 min TTL
  passport_hash:     9ed913f4...   ← tamper-evident

Razorpay: create_order → order_TVhBe2pu2TkAiI
S3 Object Lock: actions/2026/08/29/{id}.json  ← immutable, 7 years
```

The objective stayed constant. The strategy changed. That is agent reasoning — not binary search.

---

## Architecture

```
MERCHANT
  catalog.csv (sku, price, COGS, return_rate, stock_units)
  supplier invoices (PNG/JPG/PDF)
       │
       ▼
MULTIMODAL COGS EXTRACTION
  Bedrock Claude (vision) reads invoice image
  Extracts: sku, product, cogs_paise, confidence
  Human approves before entering trusted catalog
  Financial truth is never delegated to a model
       │
       ▼
AI GROWTH AGENT  (agent/agent.py)
  Observe order history
  Discover co-purchase patterns (affinity model)
  Plan bundle strategy with inventory context
  Propose to control plane
  Read DENY + constraint
  Replan with same objective, different strategy
  LLM: Claude Haiku 4.5 via AWS Bedrock
       │
       │ POST /control/propose
       ▼
BEDROCK GUARDRAILS                     Layer 1 AI safety
  Prompt injection detection
  Denied topic: bypass_authorization
  Evaluated outside agent code
  Blocks before reaching control plane
       │
       ▼
MARGINEGUARD ECONOMIC CONTROL PLANE
  MarginEngine (pure arithmetic, zero LLM, zero network)
    fee    = paid × 2%
    gst    = fee × 18%
    cogs   = sum(catalog[sku] × qty)    catalog is authoritative
    margin = paid - fee - gst - cogs
    ceil   = cogs / (k - floor)         closed-form

  PolicyEngine (7 rules, fail-closed, DENY wins, zero LLM)
    1. unknown_cogs    DENY
    2. return_rate>25% DENY  (non-refundable MDR risk)
    3. margin<floor    DENY  + constraint{max_discount_pct}
    4. discount>20%    GATE
    5. amount>500k     GATE
    6. velocity>10/min DENY
    7. default         ALLOW

  Economic Authorization Score
    score = 100
          − margin_penalty
          − return_risk_penalty
          − discount_depth_penalty
          + inventory_pressure_bonus
    >65 ALLOW   35-65 GATE   <35 DENY

  OfferSelector
    authorized_rungs AND safe_rungs = eligible
    selected = highest eligible rung
       │
  DENY       GATE             ALLOW
    │          │                │
    │      SQS queue      Action Passport
    │      SNS email      scoped, time-bounded,
    │      Merchant       hash-verified
    │      approves            │
    │          │               ▼
    │          │         ExecutionService
    │          │         DynamoDB idempotency  ← survives restarts
    │          │         Single-use token
    │          │         Razorpay API
    │          │         2xx → SUCCESS
    │          │         5xx → UNKNOWN → quarantine → SAFE_HALT
    │          │               │
    └──────────┴───────────────┘
                               │
                         EvidenceLedger
                         SHA-256 hash chain   tamper-evident
                         S3 Object Lock       truly immutable
                         CloudWatch metrics   live observability
```

---

## Experiment results — honest reporting

Real A/B experiment. 50 holdout orders. Split by ORDER_ID hash.
Control: no intervention. Treatment: agent proposes within MarginGuard bounds.

```
                    CONTROL      TREATMENT    LIFT
AOV                 ₹1,377.33    ₹1,207.21    -12.4%
Contribution margin ₹494.43      ₹473.55      -4.2%
Avg economic score: 83.9 / 100
```

By category (losses shown — not hidden):

```
accessories     +213.1%   high affinity, excess inventory cleared
apparel           -3.9%   ← loss
fitness          -14.9%   ← loss
footwear         -18.4%   ← loss
electronics     -100.0%   n=1, not meaningful
```

The agent underperformed the control group in aggregate. We report this rather than hiding it.

> "A control plane that cannot be bribed by vanity metrics is the point."

Statistical power: LOW (n=50). Results are directional, not causal.

---

## AWS production stack

Every service solves a real failure mode. None are decorative.

| Service | What breaks without it |
|---|---|
| Bedrock Claude Haiku 4.5 | Agent cannot reason or replan |
| Bedrock Guardrails (9cq40q5l324c) | Prompt injection reaches the control plane |
| DynamoDB (mg-idempotency) | Process restart loses idempotency, Razorpay duplicate possible |
| SQS (mg-approval-queue) | GATE returns 202 and nothing happens |
| SNS (mg-merchant-notifications) | Merchant never knows an action is waiting |
| S3 Object Lock (COMPLIANCE, 7yr) | Hash chain detects tampering but cannot prevent it |
| CloudWatch | No visibility into agent governance |
| Secrets Manager (mg-razorpay-credentials) | Credentials live in .env and code history |
| Lambda (mg-approval-handler) | Approval callback not deployed |

---

## Security — provable in 10 seconds

```bash
grep -rn "razorpay" agent/
# Returns: nothing
```

The agent has no Razorpay credentials. It cannot call `razorpay.orders.create()` even if its prompt is compromised. The agent receives an Action Passport — not credentials.

```json
{
  "allowed_action":   "DISCOUNT_OFFER",
  "max_discount_pct": 22.74,
  "authorized_amount": 110618,
  "valid_until": "2026-08-29T15:47:21Z",
  "passport_hash": "9ed913f4b4e236d8..."
}
```

Two independent safety layers. Neither can be fooled by the other failing.

- **Layer 1 — Bedrock Guardrails**: blocks at the AI perimeter
- **Layer 2 — PolicyEngine**: deterministic, fail-closed, zero LLM

Adversary tests:

```bash
python3 -m pytest tests/adversary/ -v
# 8/8 passed: prompt injection, return risk, velocity abuse,
#             corrupt policy, 5xx handling, tampered ledger,
#             replay attack, unknown COGS
```

---

## Kill switch — agent governance

Autonomy is configurable, not binary.

```bash
POST /control/kill-switch/activate
# All ALLOW decisions become GATE
# Agent can still propose, cannot auto-execute
# Human approval required for everything

POST /control/kill-switch/deactivate
# Autonomous execution resumed
```

Governance console shows L0–L4 autonomy levels. Current: L3 (auto-execute bounded actions within policy).

---

## The failure path

```
ExecutionService calls create_order
Razorpay returns 503
exec_status = UNKNOWN        not SUCCESS, not FAILED
quarantine row inserted      human resolution required
SAFE_HALT                    no retry, no assumption
MariaDB: /verify returns intact: true
S3: permanent record of the unknown outcome
```

We never say "no charge occurred." We say "execution state unknown. Autonomous action halted."

---

## Multimodal COGS extraction

Merchants don't have spreadsheets. They have invoices.

```
POST /control/catalog/extract-bytes
  body: { image_b64: "...", media_type: "image/png" }

Claude reads the invoice image:
  SHOE-001   Rs. 620.00   cogs_paise: 62000   confidence: 0.95
  SOCK-3PK   Rs. 210.00   cogs_paise: 21000   confidence: 0.95
  Status: pending_review

POST /control/catalog/approve
  body: { extraction_id: "..." }
  Catalog updated. COGS trusted.
```

Extracted COGS never auto-trust. Financial truth requires human approval.

---

## Governance console

`http://localhost:8085/dashboard`

10-page operations console:

- **Overview** — decision flow, real-time metrics, authorization pipeline
- **Proposals** — every decision with economic score, policy evaluation, Action Passport
- **Authorization replay** — watch 13-step pipeline animate: propose → evaluate → DENY → constraint → replan → ALLOW → execute → evidence
- **Approval queue** — GATE actions with one-click approve/reject (calls real backend)
- **Experiments** — honest A/B results with integrity panel (randomization method, statistical power, interpretation)
- **Policies** — editable business controls with impact simulator
- **Catalog** — COGS extraction workflow (upload invoice → Claude extracts → human approves)
- **Audit trail** — hash chain with live `/verify` button
- **Quarantine** — UNKNOWN state exposure and resolution
- **Agent runtime** — kill switch, autonomy levels, permission boundary
- **Security** — trust boundary diagram, AI intent vs authority visualization, adversary results

---

## Tests

```
tests/test_margin.py     21   fee math, ceiling formula, edge cases
tests/test_policy.py     14   7 rules, fail-closed, DENY-wins
tests/test_ledger.py     12   hash chain, tamper detection
tests/adversary/          8   prompt injection, return risk, velocity,
                              corrupt policy, 5xx, tampered hash,
                              replay, unknown COGS
─────────────────────────────────────────────────
                         55   passing
```

---

## API

```
POST  /control/propose                  agent submits proposals
POST  /control/margin/ceiling           query max safe discount for SKU
POST  /control/catalog/extract-bytes    multimodal COGS from image
POST  /control/catalog/approve          human approves extracted COGS
GET   /control/catalog/pending          pending extractions
GET   /control/audit                    list all decisions
GET   /control/audit/verify             hash chain integrity check
GET   /control/kill-switch              autonomy status
POST  /control/kill-switch/activate     pause autonomous execution
POST  /control/kill-switch/deactivate   resume autonomous execution
POST  /control/dashboard/approve        approve GATE action from dashboard
POST  /control/dashboard/reject         reject GATE action from dashboard
POST  /control/policy                   update policy parameters
GET   /control/health                   service health (DB, Bedrock, AWS, Razorpay)
GET   /dashboard                        governance console
```

---

## Quickstart

```bash
git clone https://github.com/saksham869/margin-guard
cd margin-guard
pip3 install -r requirements.txt
docker compose up -d
cp .env.example .env
# RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET
# AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (Bedrock + DynamoDB + S3)
python3 data/seed.py                          # seed 250 orders
python3 -m uvicorn control.app:app --port 8085
open http://localhost:8085/dashboard          # governance console
python3 -m agent.agent holdout               # run A/B experiment
python3 -m pytest tests/ -v                  # 55 tests
```

---

## What this is not

- Not an AI chatbot with a Razorpay integration
- Not a revenue recovery tool
- Not a recommendation engine
- Not a dashboard that calls Razorpay directly

It is an economic authorization and governance control plane. The AI is bounded by it, not empowered by it.

---

---

## Graceful degradation

What happens when each dependency fails:

| Dependency fails | System behavior |
|---|---|
| Bedrock (agent) | Agent unavailable. Policy engine and existing passports unaffected. No new proposals. |
| Bedrock Guardrails | Proposals blocked at perimeter. No financial authorization attempted. |
| MariaDB | No authorization decisions. No ledger writes. System halts. |
| DynamoDB | Execution blocked. No Razorpay calls without idempotency guarantee. |
| SQS | GATE action still created and logged. Merchant notification via SNS only. Queue backlog visible. |
| SNS | GATE action queued in SQS. Merchant not notified. Action remains pending. |
| S3 Object Lock | Decision recorded in MariaDB hash chain. S3 evidence write fails — surfaced in logs. |
| Razorpay | Execution returns UNKNOWN. Action quarantined. No retry. No assumption of success or failure. |
| AWS Secrets Manager | Credentials unavailable. Execution service halts. No Razorpay calls. |
| Kill switch store | In current prototype: in-process state resets on restart. Production: fail-closed (assume active). |
| Policy store | PolicyEngine fails closed — DENY on corrupt or missing policy. |


---

## Limitations

- **Experiment is underpowered.** n=50 with 27/23 split is below threshold for statistical significance. Results are directional only — not causal evidence of growth.
- **Authorization score is heuristic.** The economic score (0-100) is a weighted penalty function, not a calibrated probability of financial risk.
- **Razorpay integration runs in test mode.** Orders are created against Razorpay test API. No real payments are captured.
- **COGS catalog is static.** Real-time supplier price feeds are not implemented. COGS changes require manual catalog update or invoice re-extraction.
- **Kill switch is in-process.** `_kill_switch_active` is a Python global — it resets on server restart. Production would require persistent state (Redis/DynamoDB).
- **Policy version check is advisory.** Stale passport detection is implemented but execution falls back gracefully rather than hard-rejecting.


*github.com/saksham869/margin-guard · Razorpay AI Buildathon 2026 · Track 01*
