
# margin-guard

**Razorpay AI Buildathon 2026 · Track 01: AI Growth & Agentic Commerce**

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

An AI agent that proposes discounts without knowing cost will destroy money
while technically "growing sales."

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
  allowed_action:   DISCOUNT_OFFER
  max_discount_pct: 22.74
  authorized_amount:₹1,106.18
  valid_until:      2026-08-29T15:47:21Z   ← 5-minute TTL
  passport_hash:    9ed913f4...            ← tamper-evident

Razorpay: create_order → order_TVhBe2pu2TkAiI
S3 Object Lock: actions/2026/08/29/{id}.json  ← immutable, 7 years
```

The objective stayed constant. The strategy changed.
That is agent reasoning — not binary search.

---

## The architecture

```
MERCHANT
  catalog.csv (sku, price, COGS, return_rate, stock_units)
  product images / supplier invoices
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
MARGINGUARD ECONOMIC CONTROL PLANE

  MarginEngine (pure arithmetic, zero LLM, zero network)
    fee    = paid x 2%
    gst    = fee x 18%
    cogs   = sum(catalog[sku] x qty)    catalog is authoritative
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
          - margin_penalty
          - return_risk_penalty
          - discount_depth_penalty
          + inventory_pressure_bonus
    >65 ALLOW   35-65 GATE   <35 DENY

  OfferSelector
    authorized_rungs AND safe_rungs = eligible
    selected = highest eligible rung
       │
  DENY      GATE            ALLOW
    │         │               │
    │     SQS queue     Action Passport
    │     SNS email     scoped, time-bounded,
    │     Merchant      hash-verified
    │     approves           │
    │         │              ▼
    │         │        ExecutionService
    │         │        DynamoDB idempotency  ← survives restarts
    │         │        Single-use token
    │         │        Razorpay API
    │         │        2xx SUCCESS
    │         │        5xx UNKNOWN quarantine SAFE_HALT
    │         │              │
    └─────────┴──────────────┘
                             │
                       EvidenceLedger
                       SHA-256 hash chain   tamper-evident
                       S3 Object Lock       truly immutable
                       CloudWatch metrics   live observability
```

---

## The numbers

Real A/B experiment. 50 holdout orders. 50/50 split by order_id hash.
Control: no intervention. Treatment: agent proposes.

```
                    CONTROL     TREATMENT     LIFT
AOV                 ₹987.96     ₹1,415.76    +43.3%
Contribution margin ₹365.12     ₹441.94      +21.0%

Avg economic score: 81.2 / 100
```

By category (losses shown — not hidden):

```
accessories     +213.4%   SOCK-3PK excess inventory cleared
apparel          +28.4%
fitness          +18.5%
footwear         -13.8%   agent over-bundled, diluted basket
electronics          0%   1 order, no signal
```

The -13.8% footwear loss is the most important number.
A table with only green rows is fabricated.
The loss makes the +43.3% credible.

Policy enforcement:

```
Proposed:   50
Converted:  43   (86%) ALLOW
Denied:      5   return risk — SHIRT-1, return_rate 28%
Skipped:     2   no companion in affinity model
```

---

## AWS production stack

Every service solves a real failure mode. None are decorative.

| Service | What breaks without it |
|---|---|
| Bedrock Claude Haiku 4.5 | Agent cannot reason or replan |
| Bedrock Guardrails | Prompt injection reaches the control plane |
| DynamoDB | Process restart loses idempotency, Razorpay duplicate possible |
| SQS | GATE returns 202 and nothing happens |
| SNS | Merchant never knows an action is waiting |
| S3 Object Lock | Hash chain detects tampering but cannot prevent it |
| CloudWatch | No visibility into agent governance |
| Secrets Manager | Razorpay credentials live in .env and in code history |

---

## Security — provable in 10 seconds

```bash
grep -rn "razorpay" agent/
# Returns: nothing
```

The agent has no Razorpay credentials.
It cannot call razorpay.orders.create() even if its prompt is compromised.
The agent receives an Action Passport — not credentials.

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

Layer 1 — Bedrock Guardrails: blocks at the AI perimeter
Layer 2 — PolicyEngine: deterministic, fail-closed, zero LLM

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

We never say "no charge occurred."
We say "execution state unknown. Autonomous action halted."

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
  body: { extraction_id: "...", approved_skus: ["SHOE-001"] }

Catalog updated. COGS trusted.
```

Extracted COGS never auto-trust. Financial truth requires human approval.

---

## Tests

```
tests/test_margin.py     21   fee math, ceiling formula, edge cases
tests/test_policy.py     14   7 rules, fail-closed, DENY-wins
tests/test_ledger.py     12   hash chain, tamper detection
tests/adversary/          8   prompt injection, return risk,
                              velocity, corrupt policy,
                              5xx handling, tampered hash,
                              replay attack, unknown COGS
─────────────────────────────────────────────────
                         55   passing
```

---

## API

```
POST  /control/propose                 agent submits proposals
POST  /control/margin/ceiling          query max safe discount
POST  /control/catalog/extract-bytes   multimodal COGS from image
POST  /control/catalog/approve         human approves extracted COGS
GET   /control/catalog/pending         review queue
GET   /control/audit                   list all decisions
GET   /control/audit/verify            chain integrity
GET   /control/health                  status UP
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
python3 -m agent.agent holdout               # run A/B experiment
python3 -m pytest tests/ -v                  # 55 tests
```

---

## What I did not build

- Angular console — JSON endpoints, readable by anything
- Webhook listener — polling (production: webhooks)
- Lambda approval callback — GATE queued and notified, approval dashboard not wired
- Multi-agent supervisor — single agent is sufficient for the scope
- Kubernetes — Docker Compose

None of these are in the bar sentence.

The bar: *"Every money action explainable, bounded and gated.*
*Show the audit trail and one failure handled gracefully."*

Every word addressed.

---

## Four constraints found on Day 1

| Constraint | Response |
|---|---|
| line_items needs Magic Checkout | notes channel is primary |
| Offers Dashboard-only, no create API | pre-authorized ladder in policy.yaml |
| UPI Reserve Pay unavailable | create_order covers all upsell paths |
| MDR refundability ambiguous in docs | config flag mdr_refundable, default false |

---

*github.com/saksham869/margin-guard*

