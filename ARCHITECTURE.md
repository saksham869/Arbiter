# Arbiter — Architecture

> Economic authorization layer for autonomous AI commerce agents.
> **"If the AI is compromised, the money still isn't."**

Full interactive version: [`docs/ARCHITECTURE.html`](docs/ARCHITECTURE.html)

---

## Table of Contents

1. [The Problem](#1-the-problem)
2. [The Solution](#2-the-solution)
3. [System Architecture](#3-system-architecture)
4. [Authorization Flow](#4-authorization-flow)
5. [Core Components](#5-core-components)
6. [The DENY Loop](#6-the-deny--replan--allow-loop)
7. [Decision Outcomes](#7-decision-outcomes)
8. [Action Passport](#8-action-passport)
9. [Failure Handling](#9-failure-handling)
10. [Security Boundary](#10-security-boundary)
11. [Economic Invariants](#11-economic-invariants)
12. [Audit Trail](#12-audit-trail)
13. [AWS Services](#13-aws-services)
14. [Governance Console](#14-governance-console)

---

## 1. The Problem

AI commerce agents optimize for what they can measure. They cannot measure cost of goods.

Razorpay's Orders API shows what a product **sells** for. It does not show:
- Cost of goods (COGS)
- Razorpay MDR fees
- Return rate exposure
- Merchant margin

**The math behind a "successful" AI action:**

```
AI proposes: 30% discount on running shoes

Customer pays                    Rs. 944.30
Razorpay fee (2% + 18% GST)    - Rs.  22.29
Cost of goods (COGS)            - Rs. 830.00
------------------------------------------
Merchant profit                   Rs.  92.01  <- 9.74% margin
Required floor                        18.00%
Loss per sale                   - Rs.  70.19
```

The agent created an order. Razorpay shows a sale. The merchant lost Rs. 70.19 per unit.

At 100 proposals/day, that is **Rs. 7,000/day in hidden losses** — entirely invisible to the AI.

---

## 2. The Solution

```
+--------------------------------------------------+
|                                                  |
|  The AI decides what it wants to accomplish.    |
|  It cannot decide what it is authorized to do.  |
|                                                  |
+--------------------------------------------------+
```

Arbiter enforces the gap between those two statements.

- The AI proposes financial actions
- Arbiter decides if they are economically safe
- Razorpay executes — only when Arbiter says so

The AI is **bounded** by Arbiter, not empowered by it.

---

## 3. System Architecture

```
+----------------------------------------------------------+
|  UNTRUSTED ZONE                                          |
|                                                          |
|  AI Growth Agent  (Claude Haiku 4.5 via AWS Bedrock)     |
|                                                          |
|  - Observes Razorpay order history                       |
|  - Discovers co-purchase affinity patterns               |
|  - Plans bundle and discount strategy                    |
|  - Submits proposals to Arbiter                          |
|                                                          |
|  Has ZERO Razorpay credentials.                          |
|  $ grep -rn "razorpay" agent/  ->  0 results            |
|                                                          |
+----------------------+-----------------------------------+
                       |
                       |  POST /control/propose
                       |  Structured JSON proposal only.
                       |  No credentials. No execution.
                       |
                       v
+----------------------------------------------------------+
|  GUARDRAIL BOUNDARY                                      |
|                                                          |
|  AWS Bedrock Guardrails  (ID: 9cq40q5l324c)              |
|                                                          |
|  - Scans every proposal for prompt injection             |
|  - Blocks adversarial and denied-topic content           |
|  - First line of defense before any financial logic      |
|                                                          |
+----------------------+-----------------------------------+
                       |
                       |  Screened proposal passes through
                       |
                       v
+----------------------------------------------------------+
|  TRUSTED CONTROL PLANE  <-- Arbiter Core                |
|                                                          |
|  MarginEngine                                            |
|    Pure arithmetic. Zero LLM. Zero network calls.        |
|    Computes margin %, discount ceiling from COGS.        |
|                                                          |
|  PolicyEngine                                            |
|    7 deterministic rules. Fail-closed. DENY wins.        |
|    No LLM in the authorization path.                     |
|                                                          |
|  EconomicScore                                           |
|    100-point heuristic authorization score.              |
|                                                          |
|  ActionPassport                                          |
|    Scoped, TTL, policy-versioned, SHA-256 signed.        |
|                                                          |
|  Cannot be overridden by the agent.                      |
|                                                          |
+----------------------+-----------------------------------+
                       |
                       |  Action Passport issued
                       |  (scoped token -- NOT credentials)
                       |
                       v
+----------------------------------------------------------+
|  EXECUTION BOUNDARY                                      |
|                                                          |
|  ExecutionService                                        |
|                                                          |
|  - Razorpay credentials live HERE ONLY                   |
|  - Loaded from AWS Secrets Manager at runtime            |
|  - DynamoDB idempotency check before every call          |
|  - 2xx -> SUCCESS                                        |
|  - 4xx -> FAILED                                         |
|  - 5xx -> UNKNOWN -> QUARANTINE -> SAFE_HALT             |
|                                                          |
+----------------------+-----------------------------------+
                       |
                       |  Razorpay API call
                       |
                       v
+----------------------------------------------------------+
|  RAZORPAY  (Test Mode)                                   |
|  Real orders. Real order IDs. Real amounts.              |
+----------------------+-----------------------------------+
                       |
                       v
+----------------------------------------------------------+
|  EVIDENCE LEDGER                                         |
|                                                          |
|  SHA-256 hash chain in MariaDB                           |
|  S3 Object Lock -- COMPLIANCE mode -- 7-year retention   |
|                                                          |
|  Every ALLOW, DENY, GATE, and UNKNOWN recorded.          |
|  DENY rows as prominent as ALLOW rows.                   |
|                                                          |
+----------------------------------------------------------+
```

---

## 4. Authorization Flow

Every financial action follows this exact sequence. No shortcuts. No exceptions.

```
STEP 1   Agent proposes
         |
         +-- POST /control/propose
         |   {objective, action, items, discount_pct, rationale}
         |   No credentials. No Razorpay access. Proposal only.
         |
         v
STEP 2   Bedrock Guardrails screens
         |
         +-- Prompt injection detection
         +-- Denied topic check
         |   If blocked: HALT. No financial processing.
         |
         v
STEP 3   MarginEngine calculates
         |
         +-- fee     = paid x 2%
         +-- gst     = fee x 18%
         +-- cogs    = sum(catalog[sku] x qty)
         +-- margin  = paid - fee - gst - cogs
         +-- ceiling = cogs / (k - floor)   <- closed-form
         |
         v
STEP 4   PolicyEngine evaluates 7 rules
         |
         +-- Rule 1: unknown_cogs      -> DENY
         +-- Rule 2: return_rate > 25% -> DENY
         +-- Rule 3: margin < floor    -> DENY + {max_discount_pct}
         +-- Rule 4: discount > 20%   -> GATE
         +-- Rule 5: amount > Rs.5000  -> GATE
         +-- Rule 6: velocity > 10/min -> DENY
         +-- Rule 7: default           -> ALLOW
         |
         v
STEP 5   Decision issued
         |
         +-- DENY  -> constraint returned -> agent replans
         +-- GATE  -> SQS queue + SNS email -> human decides
         +-- ALLOW -> Action Passport issued
         |
         v  (ALLOW path only)
STEP 6   Action Passport issued
         |
         +-- allowed_action:    DISCOUNT_OFFER
         +-- max_discount_pct:  22.74
         +-- authorized_amount: Rs. 1106.18
         +-- policy_version:    1
         +-- valid_until:       +5 min TTL
         +-- passport_hash:     SHA-256 signed
         |
         v
STEP 7   TOCTOU revalidation at execution time
         |
         +-- Reload catalog from disk
         +-- Reload policy from disk
         +-- Re-enrich all COGS
         +-- Re-run PolicyEngine
         |   If economics changed: DENY
         |
         v
STEP 8   ExecutionService calls Razorpay
         |
         +-- Credentials from Secrets Manager
         +-- DynamoDB idempotency check
         +-- razorpay.orders.create(amount, offers=[offer_id])
         |
         v
STEP 9   Outcome
         |
         +-- 2xx -> SUCCESS
         +-- 4xx -> FAILED
         +-- 5xx -> UNKNOWN -> QUARANTINE -> SAFE_HALT (no retry)
         |
         v
STEP 10  Ledger written
         |
         +-- SHA-256 hash chain entry appended
         +-- S3 Object Lock (COMPLIANCE, 7yr)
         +-- Every decision recorded
```

---

## 5. Core Components

| File | Job | LLM? | Network? |
|---|---|---|---|
| `control/margin.py` | Fee math, COGS lookup, margin %, ceiling formula | No | No |
| `control/policy.py` | 7 rules, fail-closed, DENY wins, constraint return | No | No |
| `control/passport.py` | Issue, validate, expire, policy-version-check | No | No |
| `control/execution.py` | Idempotency, Razorpay call, UNKNOWN handling | No | Razorpay only |
| `control/ledger.py` | SHA-256 hash chain, append-only, S3 replication | No | S3 only |
| `control/economic_score.py` | 100-point authorization heuristic | No | No |
| `control/catalog_agent.py` | Multimodal COGS extraction from invoice images | Yes — extract only | Bedrock only |
| `agent/agent.py` | Observe, plan, propose, receive DENY, replan | Yes — propose only | Bedrock + /propose |

**The authorization path (MarginEngine + PolicyEngine) contains zero LLM calls and zero network calls.**

---

## 6. The DENY → Replan → ALLOW Loop

The agent is **constrained, not blocked.**

When Arbiter denies a proposal, it returns the exact maximum safe discount. The agent reads this constraint and replans within it. The objective stays constant. The strategy changes.

```
Attempt 1                              Attempt 2
-----------------------------------    -----------------------------------
Agent proposes: 28% discount           Agent reads constraint.
                                       Replans to 18% discount.
MarginEngine:                          
  paid    = Rs.97128 paise             MarginEngine:
  cogs    = Rs.83000 paise               margin = 22.56%  <- above floor
  margin  = 12.19%                     
                                       PolicyEngine:
PolicyEngine:                            Rule 1-6: all pass
  Rule 3 fires: margin < floor           Rule 7: ALLOW
  
Decision: DENY                         Decision: ALLOW
Reason:   margin_floor                 
Constraint: {                          Action Passport: issued
  max_discount_pct: 22.74              Razorpay: order_TVhBe2pu2TkAiI
}                                      S3 Object Lock: written
replan: required: true
objective_preserved: true

The objective stayed constant. The strategy changed.
This is agent reasoning -- not trial and error.
```

---

## 7. Decision Outcomes

### DENY

```
Triggered by:  unknown_cogs, return_risk, margin_floor, velocity_limit

Returns:
  decision:   DENY
  reason:     margin_floor  (or other rule name)
  constraint: {max_discount_pct: 22.74}
  replan:     {required: true, objective_preserved: true}

Effect:
  - Exact ceiling returned so agent can replan intelligently
  - No Razorpay call
  - Ledger entry written
```

### GATE

```
Triggered by:  discount > 20%,  or  amount > Rs.5000

Returns:
  decision: GATE
  reason:   discount_exceeds_auto_limit
  queued:   true

Effect:
  - Action stored in SQS persistent queue
  - SNS email sent to merchant with APPROVE / REJECT links
  - Dashboard card created in Approval queue page
  - On human APPROVE: GATE re-authorization runs before execution
  - On human REJECT: action closed, ledger updated
```

### ALLOW

```
Triggered by:  all 7 rules pass

Returns:
  decision:     ALLOW
  action_id:    uuid
  passport:     {scoped token}
  rzp_entity_id: order_XXXXXXXX

Effect:
  - Action Passport issued
  - TOCTOU revalidation at execution time
  - DynamoDB idempotency check
  - Razorpay create_order call
  - S3 Object Lock evidence written
  - Ledger hash chain updated
```

---

## 8. Action Passport

Every execution requires an Action Passport. The agent receives this — never credentials.

```json
{
  "action_id":        "uuid-v4",
  "allowed_action":   "DISCOUNT_OFFER",
  "max_discount_pct": 22.74,
  "authorized_amount": 110618,
  "policy_version":   "1",
  "valid_until":      "2026-09-03T14:25:00Z",
  "passport_hash":    "sha256-of-all-fields"
}
```

**Passport validation checks:**
- `passport_hash` recomputed and matched — forgery detection
- `valid_until` checked — expiry enforcement
- `policy_version` matched against current policy — stale passport detection
- `exec_status` checked — replay prevention
- DynamoDB idempotency key — concurrent execution prevention

If any check fails, execution is denied with the specific reason:
`passport_expired`, `passport_hash_mismatch`, `passport_stale_policy:v1→v2`, `already_processed`

---

## 9. Failure Handling

### The UNKNOWN state

When Razorpay returns a 5xx response, Arbiter cannot determine if a charge occurred.

**We do not guess. We do not retry. We do not assume.**

```
Razorpay returns 5xx
        |
        v
exec_status = UNKNOWN
        |
        v
Quarantine row inserted
  action_id, ts, original_proposal, context
        |
        v
SAFE_HALT
  No retry.
  No assumption of success or failure.
  SHA-256 chain records the UNKNOWN state.
  S3 Object Lock preserves the evidence.
        |
        v
Human resolution required
  Merchant checks Razorpay Dashboard
  If order exists: POST /control/quarantine/resolve {CONFIRMED_SUCCESS}
  If not found:    POST /control/quarantine/resolve {CONFIRMED_NOT_FOUND}
  Both write audit events to the ledger.
```

**We never say: "No charge occurred."**

We say: "Execution state unknown. Autonomous action halted. No retry performed. Manual resolution required."

### Graceful degradation

| Dependency fails | Behavior |
|---|---|
| Bedrock (agent) | No new proposals. Existing passports unaffected. |
| Bedrock Guardrails | Proposals blocked. SAFE_HALT. |
| MariaDB | No authorization. No ledger writes. System halts. |
| DynamoDB | No Razorpay calls without idempotency guarantee. |
| SQS | GATE logged. SNS notification only. |
| SNS | Action queued. Merchant not notified. |
| S3 | MariaDB chain intact. S3 failure surfaced in logs. |
| Razorpay | UNKNOWN. QUARANTINE. No retry. |
| Secrets Manager | ExecutionService halts. No Razorpay calls. |
| Kill switch store | Fail-closed — treat as active. No execution. |
| Policy store | Fail-closed — DENY. Never ALLOW. |

---

## 10. Security Boundary

### Credential isolation — provable

```bash
$ grep -rn "razorpay" agent/
0 results

$ grep -rn "import razorpay" agent/
0 results
```

The agent is architecturally incapable of calling Razorpay directly. Not because we asked it not to — because it does not have the credentials.

### Attack surface proof

| Attack | Answer | Test |
|---|---|---|
| AI executes Razorpay directly | NO | `grep -rn "razorpay" agent/` → 0 results |
| AI modifies merchant policy | NO | No `/policy` endpoint in agent scope |
| Expired passport executes | NO | `validate_passport()` → `passport_expired` |
| Passport replayed | NO | `exec_status` + DynamoDB idempotency |
| Stale policy executes | NO | `passport_stale_policy:v1→v2` |
| Concurrent double-execute | NO | `test_concurrent_approval` → 1 order from 2 threads |
| Unknown COGS authorized | NO | `test_unknown_cogs_never_allow` |
| 5xx becomes SUCCESS | NO | UNKNOWN → QUARANTINE → SAFE_HALT |
| Ledger tampered silently | NO | `test_tamper_detected` |
| Kill switch bypassed | NO | `test_kill_switch_blocks_execution` |
| LLM social engineering | NO | 9/9 live attacks blocked |
| Higher COGS raises ceiling | NO | `test_margin_ceiling_invariant_higher_cogs` |

### Live attack demonstration

The Security page in the governance console includes a **⚡ Run attack scenarios** button.

It fires 9 real attacks against the live control plane and shows BLOCKED / DENIED in real time:

```
1. Prompt injection in rationale           -> BLOCKED
2. Discount above ceiling (30%)            -> DENIED
3. Unknown SKU (no COGS in catalog)        -> DENIED
4. High return rate SKU (SHIRT-1, 28%)     -> DENIED
5. Margin floor violation (WATCH-1, 15%)   -> DENIED
6. Velocity abuse (11 rapid calls)         -> DENIED
7. LLM claims merchant authorized 40%      -> BLOCKED
8. LLM claims emergency override           -> BLOCKED
9. LLM says policy floor is 0%             -> BLOCKED

9/9 scenarios blocked. Authorization boundary held.
```

---

## 11. Economic Invariants

MarginEngine has 9 mathematical invariants proven by tests:

```
INV-1  Higher COGS never raises the discount ceiling
       test_margin_ceiling_invariant_higher_cogs

INV-2  Higher margin floor never raises the ceiling
       test_margin_ceiling_invariant_higher_floor

INV-3  Higher discount never improves margin percentage
       test_margin_invariant_higher_discount_lower_margin

INV-4  Unknown COGS never produces ALLOW
       test_unknown_cogs_never_allow

INV-5  Zero COGS computes correctly — no divide by zero
       test_zero_cogs_allowed_but_margin_correct

INV-6  100% discount (paid=0) handled without crash
       test_hundred_percent_discount_zero_paid

INV-7  Huge quantity (10,000 units) — no integer overflow
       test_huge_quantity_no_overflow

INV-8  Duplicate SKU — handled without error
       test_duplicate_sku_handled

INV-9  Negative discount (paid > list) — handled gracefully
       test_negative_discount_raises_or_clamps
```

These are not examples — they are proven properties of MarginEngine.

---

## 12. Audit Trail

### Hash chain

Every ledger entry computes:

```
row_hash = SHA256(
  prev_hash
  + action_id
  + ts
  + decision
  + reason
  + margin_pct
  + args_hash
)
```

Each entry binds to the previous entry's hash. Any modification to any past record breaks the chain from that point forward.

```bash
$ curl http://localhost:8085/control/audit/verify
{"intact": true, "broken_at": null, "checked": N}
```

If any record is modified:

```bash
{"intact": false, "broken_at": "action-id-of-tampered-row", "checked": N}
```

### S3 Object Lock

Every decision is also written to S3 in COMPLIANCE mode with 7-year retention.

In COMPLIANCE mode, **not even the AWS root account** can delete or modify an object during the retention period.

The hash chain detects tampering. S3 Object Lock prevents evidence deletion.

---

## 13. AWS Services

| Service | Resource | Purpose |
|---|---|---|
| Bedrock Claude Haiku 4.5 | us-east-1 | Agent reasoning + multimodal COGS extraction |
| Bedrock Guardrails | 9cq40q5l324c | Prompt injection screening |
| DynamoDB | mg-idempotency (PAY_PER_REQUEST, TTL 24h) | Execution idempotency across restarts |
| SQS | mg-approval-queue + mg-approval-dlq | GATE action persistence |
| SNS | mg-merchant-notifications | APPROVE/REJECT email to merchant |
| S3 Object Lock | mg-audit-trail (COMPLIANCE, 7yr) | Immutable audit evidence |
| CloudWatch | Arbiter namespace | Decision metrics, margin distribution |
| Secrets Manager | mg-razorpay-credentials | Razorpay API key isolation |
| Lambda | mg-approval-handler | Email approval link handler |

---

## 14. Governance Console

10-page dashboard at `http://localhost:8085/dashboard`

| Page | Key capability |
|---|---|
| **Overview** | Real-time decisions, control rate, economic score, hash chain status |
| **Proposals** | Every proposal. Click any DENY row → exact margin math with floor, shortfall, ceiling |
| **Authorization replay** | 13-step animated pipeline for any ALLOW decision |
| **Approval queue** | Real GATE cards from API. Approve → real Razorpay order |
| **Experiments** | Honest A/B results. -12.4% AOV shown. Integrity panel. Statistical power: LOW |
| **Policies** | Editable margin floor, discount limits, velocity. Policy impact simulator |
| **Catalog** | Upload invoice → Claude extracts COGS → pending human approval |
| **Audit trail** | Hash chain entries. Click "Run /audit/verify" → intact:true live |
| **Quarantine** | UNKNOWN actions. Confirm/resolve writes audit events |
| **Agent runtime** | Kill switch (live backend-enforced). L0-L4 autonomy levels |
| **Security** | Trust boundary diagram. AI intent vs authority. Live attack mode |

---

## Further Reading

| Document | Contents |
|---|---|
| [`docs/ARCHITECTURE.html`](docs/ARCHITECTURE.html) | Interactive version of this document with visual diagrams and charts |
| [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) | Assets, trust boundaries, 8 attack surfaces, 21 controls, residual risks |
| [`README.md`](README.md) | Quick start, demo commands, test suite overview |

---

<div align="center">

**Arbiter** · Razorpay AI Buildathon 2026 · Track 01: AI Growth & Agentic Commerce

[github.com/saksham869/Arbiter](https://github.com/saksham869/Arbiter)

</div>
