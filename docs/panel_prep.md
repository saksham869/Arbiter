# Panel Questions — Prep

**Q1. Why can't the LLM simply decide the discount?**
Economic authorization is deterministic. The model proposes an objective and strategy,
but it cannot supply or override COGS, margins, limits, or execution authority.
The catalog is authoritative. The model never sees raw credentials.

**Q2. Why do you need an LLM at all?**
The deterministic layer is excellent at deciding whether an action is safe.
It is not good at discovering alternative strategies when denied.
The LLM handles discovery and strategic replanning.
The control plane handles authorization. They do different jobs.

**Q3. What happens if Claude is compromised?**
It can produce a bad proposal. But it has no Razorpay credentials
and cannot directly execute a money action.
The proposal enters the deterministic control plane, which fails closed.
A compromised prompt cannot bypass the margin floor.

**Q4. Why not hard-code the discount?**
The problem is not calculating one safe discount.
It is allowing an autonomous agent to pursue an objective dynamically —
discovering which bundles work, at what discount, within economic bounds —
and replanning when rejected. A hard-coded rule cannot do that.

**Q5. Why is this better than a normal rules engine?**
A rules engine can reject an unsafe action.
MarginGuard additionally returns a structured constraint to the agent
{max_discount_pct: 22.74}, allowing the agent to change strategy
and retry while preserving the original objective.
The DENY loop is the difference. Order 24 in our run demonstrates it live:
15% off DENY (margin 15.3%) → 11.5% off ALLOW (margin 18.56%).
