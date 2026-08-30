# Experiment Results

Orders: 50 (23 control, 27 treatment)


## AOV

| Cohort | AOV | Lift |
|---|---|---|
| Control | Rs.1,137.65 | baseline |
| Treatment | Rs.1,339.28 | +17.7% |

## Contribution Margin (honest metric)

| Cohort | Contribution | Lift |
|---|---|---|
| Control | Rs.422.37 | baseline |
| Treatment | Rs.443.01 | +4.9% |

## Breakdown

- Converted (ALLOW + SUCCESS): 23
- Denied: 4
- Skipped (no companions): 0
- Avg Economic Score: 82.4/100

## By Category

| Category | n | AOV Lift | Note |
|---|---|---|---|
| accessories | 10 | +216.5% |  |
| apparel | 11 | +2.2% |  |
| electronics | 1 | +0.0% |  |
| fitness | 9 | -16.0% | ← loss |
| footwear | 19 | -19.2% | ← loss |

## Methodology

- 250 orders seeded to Razorpay test mode
- 80/20 train/holdout split before affinity model trains
- Holdout split 50/50 CONTROL/TREATMENT by order_id hash
- Affinity trains on TRAIN only
- Agent runs on TREATMENT only
- Contribution = final_amount - razorpay_fee - cogs
- AOV lift without contribution is a vanity metric

## Limitations

- COGS from merchant CSV, not verified supplier invoices
- Synthetic co-purchase patterns (seeded, not real history)
- Conversion = ALLOW + order created, not actual payment captured
- Test mode does not process real payments