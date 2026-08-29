# Experiment Results

Orders: 50 (23 control, 27 treatment)


## AOV

| Cohort | AOV | Lift |
|---|---|---|
| Control | Rs.987.96 | baseline |
| Treatment | Rs.1,415.76 | +43.3% |

## Contribution Margin (honest metric)

| Cohort | Contribution | Lift |
|---|---|---|
| Control | Rs.365.12 | baseline |
| Treatment | Rs.441.94 | +21.0% |

## Breakdown

- Converted (ALLOW + SUCCESS): 22
- Denied: 5
- Skipped (no companions): 0
- Avg Economic Score: 81.2/100

## By Category

| Category | n | AOV Lift | Note |
|---|---|---|---|
| accessories | 10 | +213.4% |  |
| apparel | 11 | +28.4% |  |
| electronics | 1 | +0.0% |  |
| fitness | 9 | +18.5% |  |
| footwear | 19 | -13.8% | ← loss |

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