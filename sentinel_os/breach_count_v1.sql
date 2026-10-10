-- breach_count_v1.sql
-- Counts governance decisions by epistemic verdict over a date range.
-- Source: ledger_entries, governance_decision rows, reasoning_integrity summary
-- (added by PR #66). Rows written before the PR is deployed have no verdict.
--
-- Run on the host, for example:
--   psql "$DATABASE_URL" -v start_date='2026-10-01' -v end_date='2026-11-01' -f breach_count_v1.sql
--
-- Reading the result:
--   breach_decisions     = decisions whose reasoning hit a terminal gate.
--                          In record mode these were still approved. Terminal
--                          mode would have refused them.
--   flagged_decisions    = decisions with a retryable gate failure (hedging).
--                          Strict mode would have refused these too.
--   decisions_with_verdict = rows that carry a verdict at all. If this is 0
--                          the deployed code has not written any verdicts yet.
--   Breach rate          = breach_decisions / total_decisions.

SELECT
    COUNT(*) AS total_decisions,
    COUNT(*) FILTER (
        WHERE decision_output -> 'reasoning_integrity' ->> 'status' = 'breach'
    ) AS breach_decisions,
    COUNT(*) FILTER (
        WHERE decision_output -> 'reasoning_integrity' ->> 'status' = 'flagged'
    ) AS flagged_decisions,
    COUNT(*) FILTER (
        WHERE decision_output -> 'reasoning_integrity' IS NOT NULL
    ) AS decisions_with_verdict
FROM ledger_entries
WHERE record_kind = 'governance_decision'
  AND "timestamp" >= :'start_date'::timestamptz
  AND "timestamp" <  :'end_date'::timestamptz;
