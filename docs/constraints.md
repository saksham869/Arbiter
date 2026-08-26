# Constraints — Day 1 Probe Results
Generated: 2026-08-26T13:42:17.807627

## Probe Results

| Probe | Result |
|---|---|
| A: notes channel | PASS |
| B: line_items | PASS — Magic Checkout active on this account |
| C: fetch_all_orders | PASS — 0 orders visible |
| D: fetch_all_payments | PASS |

## Architecture decisions

- Primary catalogue channel: notes (mg_v, mg_items, mg_ship, mg_cat)
- Offer mechanism: Dashboard pre-registration + offer_id on create_order
- Fallback if offers fail: vary amount directly

## Four known constraints

| # | Constraint | Design response |
|---|---|---|
| 1 | line_items needs Magic Checkout | notes channel is primary |
| 2 | offers param not in core API ref | probe with real offer_id after Dashboard setup |
| 3 | UPI Reserve Pay unavailable | create_order covers all upsell paths |
| 4 | MDR refundability ambiguous | config flag mdr_refundable=false, both scenarios reported |
