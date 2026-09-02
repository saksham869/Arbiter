# Experiment Results

Orders: 50 (27 control, 23 treatment)


## AOV

| Cohort | AOV | Lift |
|---|---|---|
| Control | Rs.1,377.33 | baseline |
| Treatment | Rs.1,207.21 | -12.4% |

## Contribution Margin (honest metric)

| Cohort | Contribution | Lift |
|---|---|---|
| Control | Rs.494.43 | baseline |
| Treatment | Rs.473.55 | -4.2% |

## Breakdown

- Converted (ALLOW + SUCCESS): 21
- Denied: 2
- Skipped (no companions): 0
- Avg Economic Score: 83.9/100

## By Category

| Category | n | AOV Lift | Note |
|---|---|---|---|
| accessories | 10 | +213.1% |  |
| apparel | 11 | -3.9% | ← loss |
| electronics | 1 | -100.0% | ← loss |
| fitness | 9 | -14.9% | ← loss |
| footwear | 19 | -18.4% | ← loss |

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