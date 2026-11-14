# Roadster breakers and limiter: verification report v2 (November 13, 2026)

Follows roadster_breakers_verification_report_v1.md (July 18, 2026). Repo base: origin/main at 5a0c48c. Environment: real Redis and real Postgres 16, Python 3.12, each test file run in its own process.

## 1. What changed since v1, and why

- **The breakers protected nothing on main.** They were wired into production_harness.py. That file was removed when the IVR code was extracted, and the live worker now runs through GovernanceHarness, which had no breaker. circuit_breaker.py and its tests were intact, which hid the gap. Fixed: GovernanceHarness now holds one claude_governor breaker and one postgres_ledger breaker, never shared (thresholds 5 failures in 30s and 3 in 15s, as in v1).
- **Time limits added (policy chosen Nov 13):** Claude call 20s with no hidden retries (was about 10 minutes with 2 retries), database connect 5s, query 10s. Environment overrides: GOVERNOR_TIMEOUT_S, LEDGER_CONNECT_TIMEOUT_S, LEDGER_STATEMENT_TIMEOUT_S. The limits are opt-in on the ledger class, so audit scripts such as verify_ledger keep no limit.
- **Small gaps closed:** setup_logging no longer stacks a handler per call (duplicate log lines). The worker adapter's duplicate pre-check, if it raises, now returns the same retryable ledger_write_failed result a failed write does. The v1 sid_exists gap no longer applies as written: the live path uses episode_decision_exists, now guarded at its call site.
- **Warning for a stranded queue:** /ready and /queue/stats now include a warnings list. "no_worker_has_drained" appears when work has waited longer than NO_WORKER_WARN_AFTER_S (default 60s) and nothing has ever been claimed in that namespace. Readiness stays true: it is a warning, not an outage.

## 2. Proven live (each test could have failed)

- Real failures open the Claude breaker (bad key against the real API); the sixth call is refused in under 0.5s and still fails closed; the ledger breaker stays untouched. Reverse for the ledger: a real database role made read-only plus killed sessions opens only the ledger breaker, Claude breaker untouched.
- Time limit: a real TCP server that accepts and never answers; the governor call gives up at the configured limit and fails closed. With the old code this test hangs (the original defect).
- Database query limit: a real slow query is cancelled; connection parameters carry the 5s connect limit.
- Two real worker processes, one outage: each worker's own breaker opened from its own failures, nothing dead-lettered during the outage, both probed after the reset wait, a breaker closed, and all 12 waiting jobs completed.
- Rate limiter at PRODUCTION numbers (100 per minute, burst 20) with three real ingress processes and one Redis: 90 concurrent requests for one key admitted 21 (not 60, so the bucket is shared); a second key was untouched; a 12s sustained test admitted 20 against an expected 20.
- SENTINEL_QUEUE_ID set on the ingress only, and on the worker only: the job strands, and the new warning appears. A healthy pair raises no warning.
- Bad input through a real worker (21 one-fault payloads plus controls): worker never died, nothing stuck, 13 dead-lettered as data_corruption, good jobs before and after were recorded.
- Full regression, each file in its own process: 1,126 passed. The only non-passing file is test_twin_live.py (needs three OS user accounts not present here); it is unrelated to this change and was not run. ruff (CI rule set) and bandit are clean on the changed files.

## 3. Findings that need a decision (not changed)

1. **A second, older rate limiter caps the whole ingress.** require_api_key (api_key_auth.py) counts every request, including valid ones, per connecting IP, in memory, at 60 per minute. Behind nginx every caller shares one IP, so the whole product is limited to 60 requests per minute, below rate_limiter_v2's per-caller 100, and each ingress process counts separately. This is the F-F pattern again, in a different component. The live tests set ICEBERG_RATE_LIMIT_MAX high to measure rate_limiter_v2 alone. The fix is to count only failed key checks toward the IP limit.
2. **Type-lax payloads are recorded as valid.** Eight of 21 broken payloads reached the ledger: amount as a string, amount null, outcome_reasons as a string or containing a number, a 200 KB reason, control and bidirectional characters, deep nesting, unknown extra keys. Structurally broken payloads (missing or mistyped identity, events or requested) were all rejected. Whether the type-lax ones should be rejected is a policy call.

## 4. NOT proven

- **Claude breaker recovery with a real working key.** This sandbox has no key. scripts/prove_claude_breaker_recovery.py runs it on your machine; its flow was dry-run only against a local stand-in, which proves the script, not your key.
- The 5s connect limit is proven by its parameter on a live connection, not by connecting to a silent database.
- Breakers are per worker process by design; there is no fleet-wide view of breaker state.
- Rate numbers (100 per minute, burst 20) are verified to be enforced as configured, not tuned. Tune once real traffic exists.
- The twin's 27 live tests were not re-run here.
