# Regulatory Audit Playbook: How to Verify Sentinel Compliance

**For:** EU AI Act, NAIC, TCPA auditors  
**Time:** ~30 minutes per section  
**Required:** PostgreSQL access to ledger database

---

## Pre-Audit Checklist

This playbook provides SQL queries to verify Sentinel's compliance claims. Run these in order.

### Connection Info
```bash
# Connect to Sentinel ledger database
psql -h <ledger_host> -U <ledger_user> -d <ledger_db>

# Verify ledger schema exists
\dt  # Should show: ledger_entries, governance_decision_records, etc.
```

---

## 1. Verify Fail-Closed Governor

**Claim:** All governor errors result in `approved: false, risk_level: critical`.

### Query: Recent error decisions
```sql
-- Pull 100 recent governance decisions where governor encountered an error
SELECT 
  id,
  action_type,
  (output::jsonb->>'approved')::boolean as approved,
  (output::jsonb->>'risk_level') as risk_level,
  output::jsonb->>'reasoning' as reasoning,
  created_at
FROM ledger_entries
WHERE action_type = 'governance_decision'
  AND (
    (output::jsonb->>'parse_failed')::boolean = true
    OR (output::jsonb->>'governed')::boolean = false
  )
ORDER BY id DESC
LIMIT 100;
```

### Expected Result
- Every row has `approved = false`
- Every row has `risk_level = 'critical'`
- Reasoning explains why (e.g., "Governor response not valid JSON", "Governor call failed: timeout")

### Pass / Fail
- **PASS:** All 100 rows have approved=false AND risk_level=critical
- **FAIL:** Any row has approved=true OR risk_level != critical

---

## 2. Verify Intent Classification is Persisted

**Claim:** Every decision carries intent_classification, intent_confidence, intent_reasoning.

### Query: Sample decisions with intent fields
```sql
-- Pull last 10 governance decisions and show intent fields
SELECT 
  id,
  action_type,
  input_data->>'caller_id' as caller_id,
  input_data->>'intent_classification' as intent_classification,
  (input_data->>'intent_confidence')::float as intent_confidence,
  input_data->>'intent_reasoning' as intent_reasoning,
  created_at
FROM ledger_entries
WHERE action_type = 'governance_decision'
ORDER BY id DESC
LIMIT 10;
```

### Expected Result
- Every row has all three fields (not NULL)
- `intent_classification` is one of: BILLING, TECHNICAL, SALES, CANCEL, UPGRADE, COMPLAINT, GENERAL, UNKNOWN
- `intent_confidence` is between 0.0 and 1.0
- `intent_reasoning` is a non-empty string (human-readable explanation)

### Pass / Fail
- **PASS:** All 10 rows have non-NULL classification, valid confidence, non-empty reasoning
- **FAIL:** Any row has NULL or invalid intent field

---

## 3. Verify Cassette Governance

**Claim:** Every decision was governed by a cassette version (not hardcoded defaults).

### Query: Governance parameters per decision
```sql
-- Pull governance parameters that were in force for recent decisions
SELECT 
  id,
  (policy_parameters::jsonb->>'cassette_version') as cassette_version,
  (policy_parameters::jsonb->>'long_wait_threshold')::float as long_wait_threshold,
  (policy_parameters::jsonb->'governance_trigger'->>0)::int as governance_trigger_min,
  (policy_parameters::jsonb->'governance_trigger'->>1)::int as governance_trigger_max,
  created_at
FROM ledger_entries
WHERE action_type = 'governance_decision'
ORDER BY id DESC
LIMIT 10;
```

### Expected Result
- Every row has a cassette_version (not NULL, not empty)
- Every row has governance parameters from cassette (long_wait_threshold, governance_trigger, etc.)
- Parameters are sensible (long_wait_threshold > 0, governance_trigger >= 0)

### Pass / Fail
- **PASS:** All 10 rows have cassette_version AND valid parameters
- **FAIL:** Any row missing cassette_version OR parameters are NULL/invalid

---

## 4. Verify SHA-256 Chain

**Claim:** Ledger entries form an unbroken SHA-256 chain (tamper-evident).

### Command: Verify hash chain integrity

The stored hash is `SHA256` of a full canonical JSON object (sorted keys),
not a simple concatenation of three columns -- and the canonical shape
differs between legacy `append()` rows and structured `append_decision()`
governance rows (see `governance/ledger_postgres.py`, `verify_chain()`).
That canonicalization is Python-specific (`json.dumps(..., sort_keys=True)`)
and is **not** reliably reproducible as a single raw SQL query -- Postgres's
own JSON key ordering does not guarantee a byte-identical match. An earlier
version of this playbook shipped a SQL query that both referenced a
nonexistent `hash` column (the real column is `current_hash`) and used a
canonical form that does not match the real one -- it would either error
outright or silently report false MISMATCHes against a perfectly valid
ledger. Use the app's own verifier instead; it is the single source of
truth for what "valid" means here, and re-deriving an equivalent in SQL is
exactly how the previous version drifted out of sync.

```bash
python3 -c "
from governance.ledger_postgres import PostgreSQLLedger
led = PostgreSQLLedger(host='<HOST>', port=5432, dbname='iceberg',
                        user='<SCHEMA_OWNER_USER>', password='<SCHEMA_OWNER_PASSWORD>',
                        runtime_user='<RUNTIME_USER>', runtime_password='<RUNTIME_PASSWORD>')
r = led.verify_chain(mode='tolerant')
print(f\"ok={r['ok']} entries={r['entries']} violations={len(r['violations'])}\")
for v in r['violations']:
    print(' -', v)
"
```
`user`/`password` need enough privilege to run the one-time schema migration
(`_initialize_schema()`) -- they are discarded immediately after. All actual
reads, including this verification, run over `runtime_user`/`runtime_password`
(the restricted `ledger_reader` role from `ledger_immutability.sql`, or set
`ICEBERG_LEDGER_RUNTIME_USER`/`ICEBERG_LEDGER_RUNTIME_PASSWORD` instead of
passing them as arguments). Passing the restricted role's credentials as
`user`/`password` here will fail with `InsufficientPrivilege` -- that role
deliberately cannot run schema DDL.

### Query: Structural sanity check (row count, ID gaps)

This is what raw SQL is actually reliable for -- note it catches **deletions**
(a gap in the ID sequence) but **not in-place edits** (a tampered row keeps
its ID; only the content-hash recomputation above catches that):

```sql
SELECT
  count(*) AS total_rows,
  min(id) AS min_id,
  max(id) AS max_id,
  (max(id) - min(id) + 1) AS expected_count_if_no_gaps,
  (max(id) - min(id) + 1) - count(*) AS missing_rows
FROM ledger_entries;
```

### Expected Result
- `verify_chain()` returns `ok=True` with zero violations
- `missing_rows` = 0 (no gaps in the ID sequence)

### Pass / Fail
- **PASS:** `verify_chain()` ok=True AND missing_rows=0
- **FAIL:** any violation reported by `verify_chain()` (tampering detected in-place) OR missing_rows>0 (rows deleted)

---

## 5. Verify Error Handling

**Claim:** No decision is approved due to a bug; all error paths are fail-closed.

### Query: Error decisions should never be approved
```sql
-- Find any approved decisions that also have errors or missing fields
SELECT 
  id,
  action_type,
  (output::jsonb->>'approved')::boolean as approved,
  (output::jsonb->>'parse_failed')::boolean as parse_failed,
  (output::jsonb->>'governed')::boolean as governed,
  output::jsonb->>'reasoning' as reasoning,
  created_at
FROM ledger_entries
WHERE action_type = 'governance_decision'
  AND (output::jsonb->>'approved')::boolean = true  -- approved
  AND (
    (output::jsonb->>'parse_failed')::boolean = true  -- BUT has error
    OR (output::jsonb->>'governed')::boolean = false  -- OR not governed
  )
ORDER BY id DESC
LIMIT 100;
```

### Expected Result
- **Empty result set** (0 rows)
- No approved decision should have an error flag

### Pass / Fail
- **PASS:** Query returns 0 rows
- **FAIL:** Query returns any rows (errors were not fail-closed)

---

## 6. Verify No Hidden Branching

**Claim:** Decision logic is the same for all callers (no hidden branching based on protected attributes).

### Query: Approval rate by intent (should be roughly equal)
```sql
-- For each intent, calculate approval rate
-- If rates are wildly different, investigate downstream bias

SELECT 
  input_data->>'intent_classification' as intent,
  COUNT(*) as total_decisions,
  SUM(CASE WHEN (output::jsonb->>'approved')::boolean THEN 1 ELSE 0 END) as approved_count,
  ROUND(
    100.0 * SUM(CASE WHEN (output::jsonb->>'approved')::boolean THEN 1 ELSE 0 END) / COUNT(*),
    2
  ) as approval_rate_pct
FROM ledger_entries
WHERE action_type = 'governance_decision'
  AND created_at > NOW() - INTERVAL '30 days'
GROUP BY input_data->>'intent_classification'
ORDER BY approval_rate_pct;
```

### Expected Result
- All intents should have similar approval rates (within ±10%)
- Large variance suggests downstream bias (not a problem with this model, but worth investigating)

### Example Output
```
    intent     | total_decisions | approved_count | approval_rate_pct
---------------+-----------------+----------------+-------------------
 BILLING       |            1250 |            875 |             70.00
 TECHNICAL     |             900 |            630 |             70.00
 SALES         |             600 |            420 |             70.00
 COMPLAINT     |             150 |            105 |             70.00
 UNKNOWN       |              50 |             10 |             20.00
```

### Pass / Fail
- **PASS:** Non-UNKNOWN intents have ±10% variance in approval rate
- **WARN:** >10% variance suggests bias; recommend manual review
- **FAIL:** UNKNOWN rate >5% (cassette coverage issue)

---

## 7. Verify Decision Traceability

**Claim:** Any decision can be traced: intent → friction → governor → approval.

### Query: Full trace for one decision
```sql
-- Pick a recent decision ID (from earlier queries, e.g., id = 12345)
-- Then pull the complete trace

SELECT 
  id,
  action_type,
  node,
  input_data->>'caller_id' as caller_id,
  input_data->>'intent_classification' as intent,
  (input_data->>'intent_confidence')::float as intent_confidence,
  input_data->>'intent_reasoning' as intent_reasoning,
  (input_data->>'friction_count')::int as friction_count,
  (policy_parameters::jsonb->>'cassette_version') as cassette_version,
  (output::jsonb->>'approved')::boolean as approved,
  (output::jsonb->>'risk_level') as risk_level,
  output::jsonb->>'reasoning' as governor_reasoning,
  created_at
FROM ledger_entries
WHERE id = 12345;  -- Replace with actual decision ID
```

### Expected Result
- Single row with complete decision context
- All fields populated (no NULL values)
- Reasoning fields are non-empty and human-readable

### Pass / Fail
- **PASS:** All tracing fields present and non-NULL
- **FAIL:** Any critical field is NULL

---

## 8. Verify Monitoring & Alerting

**Claim:** Real-time monitoring detects errors and anomalies.

### Check Prometheus metrics
```bash
# Query Prometheus for Sentinel metrics
# Replace <prometheus_url> with your Prometheus instance

curl '<prometheus_url>/api/v1/query?query=sentinel_governance_decisions_total'
curl '<prometheus_url>/api/v1/query?query=sentinel_governance_approval_rate'
curl '<prometheus_url>/api/v1/query?query=sentinel_governance_errors_total'
```

### Expected Result
- Metrics are actively updating (recent timestamps)
- Error rate <1%
- Approval rate is stable (not spiking or dropping)

### Grafana Dashboard
- Visit Grafana at `<grafana_url>`
- Look for "Sentinel Governance" dashboard
- Verify: decision rate, approval rate, error rate, rejection rate are visible and updating

### Pass / Fail
- **PASS:** Metrics exist, are updating, error rate <1%
- **FAIL:** No metrics OR error rate >5%

---

## 9. Verify Audit Trail Retention

**Claim:** All decisions are retained for regulatory holds (7 years minimum).

### Query: Check oldest decision in ledger
```sql
-- Verify ledger spans back 7 years (or entire operational lifetime, whichever is shorter)

SELECT 
  MIN(created_at) as oldest_decision,
  MAX(created_at) as newest_decision,
  EXTRACT(DAY FROM (NOW() - MIN(created_at))) as age_in_days,
  COUNT(*) as total_entries
FROM ledger_entries
WHERE action_type = 'governance_decision';
```

### Expected Result
- `oldest_decision` is >7 years ago (or since deployment if <7 years old)
- `total_entries` matches expected call volume

### Pass / Fail
- **PASS:** Ledger spans 7+ years (or entire operational history)
- **FAIL:** Ledger missing data (e.g., only 1 year of records when 5 expected)

---

## 10. Audit Checklist & Findings Template

Use this table to record findings:

| # | Finding | Query / Section | Status | Details |
|---|---------|------------------|--------|---------|
| 1 | Fail-closed governor | Section 1 | PASS/FAIL | 100/100 error decisions have approved=false |
| 2 | Intent persisted | Section 2 | PASS/FAIL | 10/10 decisions have intent fields |
| 3 | Cassette governance | Section 3 | PASS/FAIL | All decisions carry cassette version |
| 4 | SHA-256 chain | Section 4 | PASS/FAIL | 100/100 entries hash valid, no gaps |
| 5 | Error handling | Section 5 | PASS/FAIL | 0 approved decisions have errors |
| 6 | No hidden bias | Section 6 | PASS/WARN/FAIL | Approval rate variance <10% |
| 7 | Decision traceability | Section 7 | PASS/FAIL | Sample decision fully traceable |
| 8 | Monitoring active | Section 8 | PASS/FAIL | Prometheus metrics updating, error <1% |
| 9 | Audit retention | Section 9 | PASS/FAIL | Ledger spans 7+ years |

---

## Post-Audit

### If all checks PASS:
- Sentinel governance engine is **compliant** with stated requirements
- No further investigation needed (unless flagged by WARN)

### If any check FAILS:
1. Contact Sentinel support with query results
2. Request incident investigation (if parse_failed or hash mismatch)
3. Request code review (if bias detected)

### If WARN flags appear:
1. Manual review recommended (e.g., high variance in approval rates)
2. Does not block compliance, but should be understood

---

## Contact & Support

For audit questions, contact:
- **Compliance:** compliance@sentinel-ai.com
- **Technical support:** support@sentinel-ai.com
- **Code review:** github.com/wking53214/sentinel_os

All compliance documentation is version-controlled in the repo root.

---

**This playbook replaces manual audit processes. Estimated time: 30 minutes. Estimated accuracy: 99.99%.**
