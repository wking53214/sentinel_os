# rate_limiter_v2.py + circuit_breaker.py — Verification Report v1

**Components:** Roadster phase-1, final two pieces — F-F (rate limiting) and F-A's application-layer piece (per-resource circuit breaking)
**Date:** July 18, 2026
**Environment:** Redis 7.0.15, Postgres 16.14, redis-py 8.0.1, psycopg2 2.9.x, Python 3.12.3, fastapi 0.139.0, anthropic 0.116.0; repo @ `328605c` + this session's diff (not yet committed at time of writing)
**Method:** 96 tests total (67 pre-existing baseline + 29 new), every new test against **real Redis, real Postgres, and — for the Claude governor path — the real `api.anthropic.com` over a real HTTPS connection with a deliberately invalid key**. No mocks of Redis, Postgres, the breaker state machine, or the Anthropic transport anywhere in this delivery. **96/96 passed on three consecutive clean runs, each suite in its own process** (matching this project's established convention — see caveat below on why that separation matters).

## Scope note: two planning documents referenced in the brief don't exist

`roadster-work-breakdown-v1.md` was never found in the repo (checked the full tree, not just root) or in the docs the user separately supplied. `queue_contract_v1.md` and `cage_match_report_v1.md` **were** supplied and read, but neither specifies rate-limit thresholds or breaker timing — that's consistent with prior sessions' own scope notes, which explicitly left both components unbuilt with "clean seam only." All threshold values in this report were proposed by Claude and explicitly approved by William before implementation, not sourced from a document that turned out not to exist.

## rate_limiter_v2.py

**What it is.** A Redis-backed token bucket, one bucket per validated API key, atomic via `lua/rate_limit.lua` (same clock-authority convention as `queue_schema.py`'s own Lua: Redis server `TIME`, never a host clock). Attaches at `api_server_v2.py`'s documented `INGRESS_GUARDS` seam as `Depends(rate_limit_v2)`.

**The keying decision (F-F).** Bucketed on the validated `X-API-Key` header — the same header `require_api_key` already checked earlier in the same guard chain, so an invalid key 401/403s before ever reaching a bucket. This directly targets the defect: generation 1 keyed on the attacker-supplied key pre-validation (fresh quota per guess, confirmed live at 0/500 throttled); generation 2 fixed that by keying on connecting IP, which is a product-wide DoS behind nginx (one shared IP for every caller). Neither mistake is available here because there's nothing to key on until a real key has already passed auth.

**A regression I introduced and then fixed, worth stating plainly.** My first wiring attached `rate_limit_v2` to `INGRESS_GUARDS` unconditionally. When no `ICEBERG_API_KEYS` is configured, the guard's only fallback is connecting IP — which is generation 2's exact defect, just reintroduced for the unauthenticated case. This broke 7 of the pre-existing L-series load tests (`test_L06`...`test_L14`), which submit hundreds of concurrent requests from one test-runner IP and got 429'd into oblivion. Fixed by attaching `rate_limit_v2` **only** inside the same `if ICEBERG_API_KEYS or _REQUIRE_KEYS:` block that attaches `require_api_key` — no auth configured means no rate limiting at all, not IP-based rate limiting. Re-ran the full L-series afterward: 31/31 clean. The IP-fallback code path still exists in `rate_limit_v2()` itself, defensively, for a caller outside the documented seam, but it is dead code through the seam api_server_v2.py actually wires — flagged, not hidden.

**Defaults (William: "you pick a sensible default, I'll adjust").** Token bucket, not fixed window — a fixed window lets a caller burst 2x the configured rate for free around the window boundary; a token bucket has no boundary to exploit. 100 requests/minute steady rate, burst capacity 20, both env-configurable (`RATE_LIMIT_REQUESTS_PER_MINUTE`, `RATE_LIMIT_BURST_CAPACITY`). Stated plainly: these are a starting policy, not a proven-correct number — no traffic data exists yet to tune against.

**Verified live (`test_rate_limiter_v2.py`, 7 tests, real Redis + real uvicorn subprocess + real concurrent HTTP, small fast config — capacity 5, 1 token/sec — so the suite runs in ~13s instead of minutes):**
- Burst exactly at capacity (5 concurrent requests) — all 5 succeed.
- **The core claim, proven live, not counted from logs:** 15 real concurrent requests against a capacity-5 bucket — exactly 5×202, exactly 10×429, each 429 carrying a `Retry-After` header and `{"error": "rate_limit_exceeded"}`.
- Two principals (API keys) have fully independent buckets: exhausting one to 429 leaves the other's fresh 5-request burst completely unaffected.
- No key at all → 401 before the bucket is ever touched.
- `/health` stays open (no guards at all) even with the caller's bucket fully exhausted.
- Real wall-clock refill: exhausted → immediately 429 → sleep 2.2s real time → succeeds again.
- **Fail-open, proven in isolation from the queue's own (different, correct) fail-closed behavior.** First attempt froze the ingress's main Redis and got a 503 — but that 503 was the *queue* correctly reporting itself unavailable, not the rate limiter; the two share infrastructure by default and freezing it can't isolate either one's behavior from the other's. Fixed by giving the rate limiter its own configurable `RATE_LIMIT_REDIS_URL` (falls back to the queue's `TRANSMISSION_REDIS_URL` if unset — no behavior change in the common case) and pointing it at a dedicated second Redis instance for this one test. With only the rate limiter's Redis frozen, submissions still return 202, and the fail-open event logs at ERROR — proving the deliberate fail-open design choice.

**NOT verified:**
- Behavior under `INGRESS_WORKERS` > 1 (multiple ingress processes sharing one Redis) — the design should be correct (Redis is the one shared source of truth, no in-process state), but wasn't run under multiple concurrent server processes specifically.
- Sustained throughput at the production default (100/min, burst 20) — the live suite uses a small fast config to keep runtime reasonable; the Lua script's arithmetic was spot-checked directly against the default numbers (see the ad-hoc smoke test in this session's transcript) but not load-tested at that scale.
- Redis Cluster / Sentinel failover — same caveat `queue_schema.py`'s own docs already carry; this file makes no different claim.
- Whether 100/min · burst 20 is actually the right number for real traffic. No production traffic data exists to validate against.

## circuit_breaker.py

**What it is.** A plain, constructor-per-resource circuit breaker — CLOSED → OPEN after N consecutive failures → HALF_OPEN after a timeout → CLOSED after M consecutive probe successes. No module-level shared instance, no registry two unrelated call sites could collide on (this is asserted structurally by `test_no_module_level_shared_breaker_exists`, an AST-style check in the same spirit as `test_api_server_v2.py`'s own `test_T10`).

**Where it attaches — and a finding that changed the design.** `sentinel_worker.py` never calls the Claude API or Postgres directly; both live inside `production_harness.py`'s `process_call()`, at two exact call sites (the `safety_check()` call, the `append_decision()` call). Per-resource isolation is only achievable by wrapping those two call sites, which William explicitly approved after reviewing an exact diff. The diff is small: 4 lines constructing the two breaker instances, plus each existing call wrapped in `.call(...)` — no new `except` branches, since a tripped breaker raises inside the *same* `try/except Exception` blocks that already existed, landing in the exact fallback shapes `sentinel_worker.py` already handled correctly.

**A dead-breaker bug found before it shipped.** `claude_governance_api.py`'s `safety_check()` catches every exception internally, including real transport/auth failures, and always **returns** a fail-closed dict — it never raises. A breaker wrapped around it via exception-only detection would never trip no matter how down the API is, because the exception never reaches the wrapper. Fixed by adding an optional `is_failure(result)` predicate to `CircuitBreaker.call()` — the Claude call site passes one that recognizes the `"transport_error:"`-prefixed reasoning `safety_check()` uses specifically for real API/network failures (as opposed to a live-but-malformed response, which is not an outage and correctly does not trip the breaker). The ledger path needed no such change — `append_decision()` does raise on real failure, so the default exception-only behavior is correct there.

**Thresholds (William: "you propose separate defaults per resource").**

| | Claude governor | Postgres ledger |
|---|---|---|
| failure_threshold | 5 | 3 |
| reset_timeout | 30s | 15s |
| half-open successes to close | 2 | 2 |

Claude API calls are noisier (rate limits, brief network blips) so more consecutive failures are needed before concluding it's actually down, and outages there tend to resolve over tens of seconds. Postgres failures are usually connection-pool exhaustion or a short blip — fewer failures needed to trip, since the ledger path is more critical and the queue already retries a failed job later, so failing fast is cheap; it also tends to recover faster once it does.

**Stated caveat, not fixed here.** The breaker only reacts to what the wrapped call actually raises or returns. If a call hangs without either, the breaker does nothing — that's an HTTP-client/DB-driver timeout problem, one layer below what a breaker can address from outside.

**Verified live:**
- `test_circuit_breaker.py`, 16 tests, the state machine in isolation: exact-threshold tripping (not one early, not one late), OPEN rejects without ever calling through, real `time.sleep()`-based HALF_OPEN transition and timeout-restart-on-reprobe-failure, N-consecutive-successes-to-close, 20 real concurrent threads hammering a HALF_OPEN breaker with exactly 1 let through as the probe (verified by call count, not just by outcome), two independent instances proven not to share state (the direct F-A regression check), the `is_failure` predicate path (trips on a return value that never raises, does not trip on an ordinary non-matching return value, default behavior unchanged when no predicate is given).
- `test_production_harness_breakers.py`, 6 tests, against the real, diffed `production_harness.py`:
  - **Claude path, real API:** a sanity test confirms the live API genuinely rejects a bad key with the `transport_error:` shape the predicate keys on (not assumed — checked directly first). 5 real consecutive auth failures against `api.anthropic.com` trip the breaker exactly at threshold; the ledger breaker is confirmed completely unaffected (real F-A proof, not synthetic).
  - **Postgres path, real permission-based failure, not a killed pool.** First attempt closed the *entire* connection pool to force failures — but that also broke `sid_exists()`, an existing, **unprotected** SELECT at the very top of `process_call` with zero exception handling around it, a real pre-existing gap this build did not introduce and is not fixing (flagged below). A full pool outage crashes there before ever reaching the breaker-wrapped write, which made pool-killing the wrong tool for isolating "does the write breaker trip on write failures." Fixed by creating a real, restricted Postgres role (`ledger_write_test`, SELECT-only on `ledger_entries`) and toggling its INSERT grant with real `REVOKE`/`GRANT` statements mid-test — `sid_exists()` keeps working throughout, `append_decision()` fails for real with a real `permission denied` error, exactly the failure mode of a narrowed or revoked runtime credential. 3 consecutive real failures trip the breaker exactly at threshold; the Claude breaker is confirmed unaffected. Real recovery: real `GRANT INSERT` mid-test, real 15.1s wall-clock wait, real HALF_OPEN transition, two real successful writes close it — both writes confirmed present via `sid_exists`.

**A pre-existing gap found, not fixed — in scope for a future session, not this one.** `sid_exists()` (the sid-dedup check at the top of `process_call`) has no exception handling at all. Any Postgres failure there — not just a sustained outage, a single blip — crashes `process_call` entirely, for every call (governed or not), regardless of these two breakers. This predates this build and sits outside the two named call sites William approved touching; noted here rather than silently absorbed into this diff or silently left for someone to discover in production.

**A logging bug found and worked around, not fixed at the root.** `operational_resilience.py`'s `setup_logging()` adds a new handler on every call with no dedup guard. Multiple `CircuitBreaker` instances sharing a logger name (e.g. several `IcebergProductionHarness` instances in one process, as happens across this test session) stack handlers on the same global Python logger object and multiply every log line. Doesn't affect breaker correctness — trimmed locally in `circuit_breaker.py`'s constructor (`self.logger.handlers = self.logger.handlers[:1]`) rather than patching a third file out of scope for this build.

**NOT verified:**
- Claude breaker recovery (HALF_OPEN → CLOSED) against a real *good* key — only the trip side was tested against the real API; recovery timing was verified thoroughly on the ledger side and unit-tested generically, but not re-proven against a real successful Anthropic response, since that would require a real usable API key in this environment.
- Breaker behavior under genuinely concurrent `process_call` invocations (multiple threads/workers hitting the same harness instance's breakers at once) — the state-machine-level concurrency test (20 threads, one HALF_OPEN probe slot) covers the mechanism, but wasn't re-run specifically through `process_call` under load.
- Long-running / sustained-open behavior — every test here trips, waits out one real timeout, and closes within seconds; no soak test.

## Regression: the pre-existing 67/67 baseline

Run three times, each suite in its own process (this project's own established convention — `test_T10`'s live-module-table check is itself process-scoped, and running unrelated suites in one shared pytest process cross-contaminates `sys.modules` between them; confirmed this by accident, then fixed the run method, not the code). **67/67 held every time**, plus the 29 new tests, for **96/96 across three consecutive clean runs.**

## Scope confirmation

Delivered: `circuit_breaker.py`, `rate_limiter_v2.py`, `lua/rate_limit.lua`, `test_circuit_breaker.py`, `test_production_harness_breakers.py`, `test_rate_limiter_v2.py`, this report. Also modified, with explicit approval after reviewing an exact diff: `production_harness.py` (breaker instantiation + two call sites wrapped) and `api_server_v2.py` (one guard-seam attachment, made conditional on auth being configured after the regression above). No new dependency added — `requirements.txt` had no unused circuit-breaker or rate-limit library sitting there, so both are hand-rolled, consistent with everything else already in `governance/` and `queue_schema.py`. Not fixed, flagged for a future session: `sid_exists()`'s missing exception handling, and `operational_resilience.py`'s missing log-handler dedup guard.

## Addendum: F-J, found while linking the transmission to the real worker/V12 harness for the first time

Every prior session tested `api_server_v2.py` and `sentinel_worker.py` separately — the ingress suite drains jobs with a test Drainer standing in for the worker, and the worker suite calls `production_harness` directly with no queue in front. Neither had ever been run together as one connected system before this session.

**The defect (F-J).** They have two independent queue-identity settings: the ingress's `TRANSMISSION_NAMESPACE` (default `"tq"`) and the worker's `--queue-name`/`SENTINEL_QUEUE_NAME` (default `"v12"`, which the queue library's "original" dialect prefixes to `"sq:v12"`). With stock defaults on both sides these resolve to **different Redis key prefixes.** Proven live: a call submitted through the real ingress sat at `pending` for 10 straight seconds while a real worker process logged a clean "Worker starting" and polled its own, completely empty keyspace — no error, no warning, anywhere. A job submitted in this configuration would be lost silently, forever, in production.

**The fix — a converter, not a handshake.** William's framing, and the right call: a handshake only detects a mismatch after both sides are already independently configured; a converter removes the second knob so there's nothing to keep in sync. `SENTINEL_QUEUE_ID` is now the one identifier both processes read by default — the ingress derives `f"sq:{SENTINEL_QUEUE_ID}"` instead of taking an independent raw namespace, matching the worker's existing prefix convention exactly. `TRANSMISSION_NAMESPACE` (ingress) and `SENTINEL_QUEUE_NAME` (worker) remain as explicit escape hatches for anyone who deliberately wants direct dialect-level control (e.g. running two isolated queues) and take precedence when set — but the common case now needs zero coordination.

**Verified live, both before and after the fix:**
- Broken case reproduced first, honestly, before touching anything: stock defaults, job stuck at `pending`, worker never claims it.
- Fix applied, same stock-defaults scenario re-run: submit → `done` in under a second, real ledger row.
- Full real stack under load: 2 real worker processes draining 1 real ingress, 20 concurrent calls (mixed governed/ungoverned) → 20/20 done, 0 dead, 0 stuck, ledger row count (14) exactly matching the 14 governed calls sent.
- `test_queue_identity_converter.py`, 4 tests, locking this in permanently: stock defaults resolve to the same prefix and complete a job; a shared non-default `SENTINEL_QUEUE_ID` keeps both sides aligned; the `TRANSMISSION_NAMESPACE` escape hatch still works when explicitly matched on both sides; and, as an honest negative case, deliberately setting the two escape-hatch knobs to genuinely *different* values still correctly fails to complete — documenting that the escape hatches remain two independent knobs by design, only the default path is now foolproof.

**Regression:** full suite re-run three times after this change, each suite its own process — **100/100** (67 original baseline + 29 breaker/rate-limiter tests + 4 new converter tests).

**NOT verified:** behavior when `SENTINEL_QUEUE_ID` is set on only one side (partial override) — the current design makes this behave as a genuine mismatch (same as the negative test case), which is arguably correct (partial configuration should not silently coerce), but wasn't tested explicitly as its own case. Also not verified: whether an even stronger guarantee (e.g., the ingress refusing to report `ready: true` if no worker has ever been observed draining its queue) would be worth adding — flagged as a possible future defense-in-depth layer, not built.
