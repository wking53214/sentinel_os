# sample_data/

Synthetic fixtures for exercising the kernel at scale. Nothing here is real
data; nothing here is a test dependency (the `Tests/` suite builds its own
episodes inline).

## `mortgage_cassette_synthetic_customers_v2.csv`

10,000 synthetic residential-mortgage customers, 36 columns, one full loan
lifecycle per row: origination decision → 1,095-day outcome horizon → resolution.
Added in `212d666` (2026-08-07) for scale testing; `v1` is superseded.

The columns line up with the mortgage cassette's vocabulary
(`cassettes/mortgage_cassette.py`):

| CSV columns | maps to |
|---|---|
| `requested_loan_amount`, `decision`, `approved_loan_amount` | the episode `requested` / `actual` |
| `adverse_action_reason`, `approval_amount_change_reason` | `outcome_reasons` (the kernel owes one on any mismatch) |
| `outcome_horizon_days` (1095) | the cassette's `loan_performance` maturation rule |
| `resolution_type` (`paid_in_full` / `involuntary_closure`), `involuntary_closure_mechanism` | `classify_outcome()` — favorable / unfavorable |
| `loan_status = modified` | not a resolution — the obligation is abandoned as `DECISION_SUPERSEDED` |

Distribution: 6,509 approved / 3,491 denied; every denial carries an
adverse-action reason; 794 approvals were reduced with a stated reason.
Matured: 1,745 `paid_in_full`, 96 `involuntary_closure`, 85 `modified`, the rest
still open.

### Running it through the governed path

```
cd sentinel_os
python3 run_mortgage_population.py            # reset ledger, run all 10k
python3 run_mortgage_population.py --limit 500
```

Each row with a requested-vs-actual discrepancy goes through the full path —
judge → governor consult → conservation boundary → hash-chained Postgres
ledger write — then `ledger.verify_chain()` checks the whole chain. Needs a
local PostgreSQL (`iceberg`/`iceberg`@`localhost:5432`); the governor is a
deterministic synthetic stub (no Claude key required). See the script's
docstring for flags.

## `DEMO_OUTPUT_v1.txt`

Captured output from a SAGE-K interpretation-drift demo (the fair-lending
regulatory-lens flow) — unrelated to the CSV above.
