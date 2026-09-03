> **⚠ Point-in-time snapshot — not maintained.** The canonical description of Sentinel OS is the [repository root README](../../README.md).
>
> Major changes since this document was written: keyed `authorized_by` ledger attestation with key rotation (PR #28); the persisted observed-event layer (PR #29); and the extraction of the IVR/Iceberg application to the [GSA-815](https://github.com/wking53214/GSA-815) repo (PR #30, 2026-08-28) — the standalone simulator and its `Domain/` `Sim/` `Engines/` `Model/` `observe/` support tree, Twilio ingestion, the Claude governor client, and the queue/staffing/Bayes layer. The directory maps, dependency graphs, and counts below predate all of that.

---

# Sentinel OS Repository Audit
## Observable Facts Only

**Current HEAD:** `origin/main` at commit `68cadfb` (as of July 29, 2026)  
**Repository:** `github.com/wking53214/sentinel_os`  
**Status:** Production repository, active development

---

## 1. REPOSITORY STRUCTURE

### Root-Level Organization
```
sentinel_os/
├── .github/workflows/
│   └── tests.yml                    (CI workflow definition)
├── .gitignore                       (includes: *.patch, *.bundle, *.diff, *.git-archive, certs/*.pem)
├── sentinel_os/                     (application source)
├── Tests/                           (test suite)
├── cassettes/                       (domain-specific cassette implementations)
├── Engines/                         (decision engines, mostly simulator-only)
├── Domain/                          (domain-agnostic data structures)
├── Model/                           (graph building, system modeling)
├── Sim/                             (standalone simulator)
├── observe/                         (physiological monitoring - observe/perceive platform)
├── governance/                      (governance verification, audit trails)
├── regulatory_cassettes/            (regulatory lenses, compliance frameworks)
├── scripts/                         (deployment and infrastructure scripts)
├── docs/                            (CHANGELOG.md, documentation)
├── conftest.py                      (pytest fixture configuration, root level)
├── requirements.txt                 (Python dependencies)
├── README.md                        (top-level repository documentation)
├── COMPLIANCE.md                    (regulatory/compliance baseline references)
├── MODEL_CARD.md                    (system card/documentation)
├── docker-compose.yml               (local deployment)
├── Dockerfile                       (container image definition)
└── certs/                           (.gitignore'd, generated at runtime)
```

### Test Organization
Tests stored in `Tests/` directory (not mirrored to source tree):
- One conftest.py at root for role/database setup
- One conftest.py in Tests/ for per-test fixtures
- 384–673 tests depending on current branch (see Test Suite section)
- Exclusion in CI: `test_twin_live.py` requires Unix socket peer-auth + Postgres on runner (separate job needed)

### Documentation State
**README files present:**
- `README.md` (root)
- `governance/README.md` 
- `cassettes/README.md`
- `regulatory_cassettes/README.md`
- Five additional module READMEs: `Domain/`, `Engines/`, `Model/`, `Sim/`, `observe/` (added July 23)

**Status markers found in code:**
- Scaffolding/reference implementations: marked as such in module docstrings
- Dead code removed: `Engines/rl_ppo_adaptive.py` (July 25), `CallerIntent` enum cleanup (July 25), Grafana export code (July 25)
- Placeholders: `TwilioStreamAdapter.fetch_recent_calls()` returns `[]` (unimplemented)

---

## 2. MAJOR MODULES (Production Governance Path)

### Core Governance Kernel
| Module | Purpose | Status | Notes |
|--------|---------|--------|-------|
| `sentinel_os/episode.py` | Frozen observation record validation | Implemented | Enforces reason-on-ANY-outcome-mismatch; actor_discrepancies validation |
| `sentinel_os/cassette_interface.py` | Kernel cassette ABC (judge/explain/manifest) | Implemented | Rewritten July 23 as kernel-only; capabilities separate |
| `sentinel_os/cassette_capabilities.py` | Capability registration and parameter ownership | Implemented | Anti-placeholder rule; require_capabilities gates |
| `sentinel_os/cassette_forensics.py` | Code-hash computation for tamper detection | Implemented | Includes `_GOVERNANCE_CODE_MODULES` surface definition |
| `sentinel_os/cassette_schema.py` | Cassette manifest validation | Implemented | Version 2.0+ format with manifest-first validation |
| `sentinel_os/event_v1.py` | Stamped observation records (VERIFIED/ATTESTED/ESTIMATED) | Implemented | Module added July 29 |
| `sentinel_os/outcome_v1.py` | Outcome tracking with maturation rules | Implemented | Module added July 29; carries "The Provenance Rule" |

### Decision Recording & Audit
| Module | Purpose | Status | Notes |
|--------|---------|--------|-------|
| `sentinel_os/governance/` | Ledger, verification, audit trails | Implemented | Dual tables: decision ledger (primary) + obligation ledger (twin) |
| `sentinel_os/governance/ledger_postgres.py` | PostgreSQL ledger implementation | Implemented | Requires: `postgres:16`, `iceberg`/`iceberg` credentials |
| `sentinel_os/governance/ledger_immutability.sql` | Trigger-based append-only guarantee | Implemented | Enforced at DB level (BEFORE INSERT trigger) |
| `sentinel_os/governance/verify_chain.py` | Chain reconstruction and verification | Implemented | Three recompute sites: writer, verify_chain, twin_custody |
| `sentinel_os/governance/twin_custody.py` | Twin replica (independent witness) | Implemented | Sealed envelope (X25519+AES-GCM), separate Postgres role |

### Regulatory Compliance
| Module | Purpose | Status | Notes |
|--------|---------|--------|-------|
| `sentinel_os/regulatory_cassette_interface.py` | Regulatory lens ABC (observer/live modes) | Implemented | Judge never enters actor_report; explain reads only |
| `sentinel_os/regulatory_checks.py` | Dimension checkers (prohibited basis, tier screen, narrative, statistical equity, correlation) | Implemented | Five dimensions + geographic equity (dimension 6, not wired to c2_rollup yet) |
| `sentinel_os/regulatory_deck.py` | Regulatory disclosure and recording | Implemented | disclosure-first design (write event, then effect) |
| `sentinel_os/regulatory_cassettes/cfpb_reg_b.py` | CFPB Reg B reference lens | Implemented | opt-in C2 dimensions 2–5; all params are configuration data |

### Event & Outcome Processing
| Module | Purpose | Status | Notes |
|--------|---------|--------|-------|
| `sentinel_os/obligation_sweep.py` | Cohort assembly, outcome review generation | Implemented | Module added July 29; CLI: `python obligation_sweep.py --ledger-dsn ... --receiver-url ...` |
| `sentinel_os/obligation_supersession.py` | Loan modification orchestration (ABANDON-on-mod) | Implemented | Module added July 30; finds decisions with `replaces_hash` |
| `sentinel_os/production_harness.py` | Live call processing (episode assembly, judgment) | Implemented | Additive path: quality_score still drives behavior; kernel verdict rides alongside |

### Inference & Querying
| Module | Purpose | Status | Notes |
|--------|---------|--------|-------|
| `sentinel_os/cassette_loader.py` | Dynamic cassette module loading + sys.modules registration | Implemented | Fixed July 24: now uses real importlib + fallback to dynamic loading |

---

## 3. CASSETTES (Domain Implementations)

| Cassette | Domain | Status | Capabilities | Outcome Obligation | Code Hash Version |
|----------|--------|--------|--------------|-------------------|--------------------|
| `cassettes/ivr_cassette.py` | IVR/call center (reference implementation) | Implemented | Telephony, routing, RL, self-healing | Not enabled | 2.0.2 |
| `cassettes/banking_cassette.py` | Banking (high-risk lending) | Implemented | Routing, RL, self-healing (no telephony) | Not enabled | 2.0.3 |
| `cassettes/mortgage_cassette.py` | Mortgage lending | Implemented | Outcome obligation only (kernel + governance) | Enabled | N/A (not in CI yet) |

**Note:** All cassettes declare CAPABILITIES manifest at construction. Anti-placeholder rule blocks loading if a cassette declares a parameter owned by a disabled capability.

---

## 4. PACKAGE DEPENDENCIES

### Core Requirements
```
Python 3.9+
pytest                      (test framework, pinned ruff 0.15.22 for CI)
cryptography               (X25519/AES-GCM for twin sealed channel)
httpx<0.28                 (pinned due to Anthropic SDK 0.116.0 interaction)
psycopg2-binary            (PostgreSQL 16 client)
redis                      (cache, belief-state persistence)
anthropic                  (Claude API access)
bandit                     (security scanning, Medium+ gate in CI)
ruff                       (linting, 0 findings gate)
```

### Infrastructure Requirements
- **PostgreSQL 16** (ledger + twin replica, peer-auth for twin security)
  - `iceberg` user (primary ledger owner)
  - `ledger_reader` role (production runtime identity, SELECT+INSERT only)
  - `sentinelsvc`, `twincustodian`, `twincustomer` roles (twin security domains)
- **Redis 7** (in-memory cache, learning loop belief persistence)
- **TLS Certificate** (ephemeral at runtime, generated if missing: `certs/cert.pem`, `certs/key.pem`)

### External Service Integrations
- **Twilio API** (real-time call ingestion, currently unimplemented for live streaming)
- **US Census Geocoder** (BISG race/ethnicity estimation, ZIP/county extraction for geography checks)
- **Anthropic Claude API** (decision explanations, optional GALLM executor — currently stubbed)

---

## 5. BUILD SYSTEM & CI

### Local Development
```bash
# Install dependencies
pip install -r sentinel_os/requirements.txt
pip install "httpx<0.28"  # Pinned for Anthropic SDK

# Run tests (CI-equivalent)
python -m pytest . --ignore=test_twin_live.py -v --tb=short

# Linting
ruff check .               # Exit 0 required
bandit -r . -ll            # Only -ll (Medium+) are gated
```

### CI/CD (GitHub Actions)
**Workflow file:** `.github/workflows/tests.yml`

**Services:**
- PostgreSQL 16 (TCP, `postgres:` Docker image)
- Redis 7 (TCP, `redis:` Docker image)

**Test invocation:**
```bash
cd sentinel_os/
python -m pytest . --ignore=test_twin_live.py -v --tb=short
```

**Exclusions:**
- `test_twin_live.py` requires Unix socket (peer-auth) + natively-installed Postgres (not available in current GitHub Actions job)
- When peer-auth enabled locally, all 384+ tests can run without exclusion

**CI History (observable facts):**
- First fully green CI run: July 24, commit `f0d11fa`
- Last 5 runs (as of July 24 audit): green
- Ruff version pinned: 0.15.22 (prevents drift between local and CI)
- Bandit gate: Medium+ (HIGH + 17 Medium flagged; now 0/0 after nosec justifications)
- F841/B101 pre-existing noise (test assertions, unused variables in test-only code)

### Docker Deployment
```bash
# docker-compose.yml provisions:
docker-compose up
# - PostgreSQL 16 service
# - Redis 7 service
# - Sentinel API service (port configurable)
# - Healthcheck: pg_isready
```

**Known issue (pre-existing):** docker-compose.yml's `depends_on` only waits for container start, not Postgres readiness. Solved via `healthcheck` with `pg_isready`.

---

## 6. ENTRY POINTS

### Production Services
| Entry Point | Module | Purpose | Runtime Identity |
|-------------|--------|---------|-------------------|
| `sentinel_worker.py` | Call ingestion worker | Process Twilio call records | `ledger_reader` role (fail-closed) |
| `api_server_resilient.py` | HTTP API server | Judge decisions, explain reasoning | `ledger_reader` role (fail-closed) |
| `iceberg_complete_simulator.py` | Standalone simulator | Batch processing, load testing | In-memory ledger (no DB required) |

### Maintenance & Deployment Scripts
| Script | Purpose | Status |
|--------|---------|--------|
| `scripts/twin_ensure_services.sh` | Provision twin OS identities and Postgres roles | Implemented |
| `twin_sync_worker.py` | Synchronize primary to twin replica | Implemented |
| `twin_migrate.py` | Schema migration (twin-side) | Implemented |
| `obligation_sweep.py` | CLI: generate cohort equity reviews | Implemented (new July 29, added CLI July 30) |

---

## 7. EXTERNAL INTEGRATIONS

### Confirmed Integrations
| Service | Purpose | Status | Implementation |
|---------|---------|--------|-----------------|
| PostgreSQL | Ledger storage | Implemented | Native psycopg2 + cryptography |
| Redis | Learning loop belief state persistence | Implemented | Redis 7, reusing CircuitBreaker pattern |
| Anthropic Claude API | Decision explanations, GALLM executor | Partial | Explanations implemented; GALLM executor stubbed |

### Placeholder/Unimplemented
| Service | Purpose | Status | Notes |
|---------|---------|--------|-------|
| Twilio Stream API | Real-time call ingestion | Unimplemented | `TwilioStreamAdapter.fetch_recent_calls()` returns `[]` |
| US Census Geocoder | BISG + geography lookup | Referenced | Integration point exists; real live calls not confirmed |

---

## 8. TEST STRUCTURE

### Test Suite Composition (as of July 29/30)
| Category | Count | Organization |
|----------|-------|--------------|
| Production path tests | ~280 | `Tests/test_*.py` (full suite run) |
| Twin replica tests | 18 | `Tests/test_twin_live.py` (excluded from CI, needs peer-auth) |
| Cassette tests | 27+ | Mortgage cassette added July 29 |
| C2 regulatory tests | 34+ | Added July 23 regulatory cassette framework |
| Cohort assembly tests | 10+ | July 29; obligation_sweep and cohort_review endpoints |
| EventV1/OutcomeV1 tests | 102 | Added July 29 |
| Call ingestion tests | 19 | Added July 29 (ivr_events_contract) |
| Lending follow-ons | 40+ | ZIP/county regional equity + ABANDON-on-mod (July 30, not yet applied) |
| **Total (HEAD)** | **~670 passed / 6 skipped** | Multiple runs confirmed 2x back-to-back clean |

### Test Fixtures (conftest.py)
**Root conftest.py:**
- Autouse database role setup (`ledger_reader`, OS identities)
- TLS certificate generation if missing

**Tests/conftest.py:**
- `test_ledger` fixture (drops + recreates `ledger_entries` per test)
- Applies `ledger_immutability.sql` on each test

### Known Flakiness
| Test | Status | Root Cause |
|------|--------|-----------|
| `test_api_server_v2.py::test_L11_frozen_redis_health_stays_alive` | Flaky | Wall-clock latency assertion under load; passes in isolation |
| All tests using persistent external services | Occasional failures | Postgres/Redis daemon reaps mid-container; restart required before suite |

---

## 9. DEPENDENCIES & VERSIONING

### Python Version
- **Tested/Stated:** Python 3.9+
- **CI uses:** System Python version on GitHub runner (unverified PEP 668 interaction)

### Package Pinning
```
httpx < 0.28              (Anthropic SDK 0.116.0 incompatibility)
ruff == 0.15.22           (CI drift prevention)
postgres == 16            (explicit version in CI + docker-compose)
redis == 7                (explicit version in CI + docker-compose)
```

### Cassette Versioning
- Versioning: `domain:name:version` format (e.g., `ivr:banking:2.0.3`)
- Trigger for version bump: cassette code hash moved + binding enforcement active
- **Current versions:**
  - IVR: 2.0.2
  - Banking: 2.0.3
  - Mortgage: TBD (not in released versions yet, based on audit timing)

---

## 10. KNOWN LIMITATIONS & OPEN ITEMS

### Implemented But Incomplete
| Item | Status | Owner Decision | Notes |
|------|--------|----------------|-------|
| C2 Dimension 6 (geographic equity) | Built, not wired | Wm | Added July 30; `zip_county_regional_equity_check_v1.patch` unmerged |
| Per-decision C2 wiring | Built, not enabled | Wm | `fetch_latest_cohort_review()` exists; not called from live judge() |
| Event source ingestion | Contract defined, no real source | Wm | `ivr_events` contract ready; actual Twilio/Studio/TaskRouter source TBD |
| Twilio real-time call stream | Placeholder stub | Wm | `TwilioStreamAdapter.fetch_recent_calls()` returns `[]` |
| Phone-digit call-journey heuristic | Replaced by generic contract | Wm | No longer drives behavior if real events present; gracefully falls back to estimated |
| ABANDON-on-modification orchestration | Built, not applied | Wm | `abandon_on_modification_orchestration_v1.patch` unmerged |

### Placeholder/Deferred
| Item | Status | Owner Decision |
|------|--------|----------------|
| GALLM real executor | Stubbed (returns hardcoded dict) | Deferred |
| Additional loan-type cassettes (auto, personal, BNPL, payday) | Cancelled (July 30) | Mortgage alone sufficient for now |
| Real RL/PPO training | On hold explicitly | Wm: revisit later, not abandoned |
| PostgreSQL constructor lock optimization | Known issue, not fixed | Wm's decision |
| Node-naming coupling (queue/agent detection by substring) | Documented limitation | Out of scope |

### Genuine Gaps (Design-Level)
| Gap | Classification | Status |
|-----|-----------------|--------|
| Variable renaming defeats proxy + tier screens | Disclosed limitation | Same class as latent-proxy gap; requires new approach |
| Bias spread thin across many decisions | Out of scope | Correctly handled by cohort-level dimension 4 |
| Free-text narrative never captured at decision time | Disclosed in audit | Would require schema change; dimension 3 correctly reports INDETERMINATE |

---

## 11. DOCUMENTATION INVENTORY

### Auto-Generated/Committed
| Document | Status | Last Updated |
|----------|--------|--------------|
| README.md (root) | Current | July 23 (staleness pass #2) |
| CHANGELOG.md | Current | July 22 |
| COMPLIANCE.md | Current | July 23 (Grafana references fixed) |
| MODEL_CARD.md | Current | July 23 (Grafana references fixed) |
| `.github/workflows/tests.yml` | Current | July 24 (CI repair) |
| `.gitignore` | Current | July 22 (added *.patch, certs/*.pem) |

### Module Documentation
| Module | README Status | Docstring Status | Notes |
|--------|---------------|------------------|-------|
| governance/ | Yes | Yes | Canonical governance framework |
| cassettes/ | Yes | Partial | Reference implementations documented; schemas version-locked |
| regulatory_cassettes/ | Yes | Yes | Five-dimension framework + tier confidence scale |
| Domain/, Engines/, Model/, Sim/, observe/ | Added July 23 | Yes | Scaffolding/simulator-only notation |
| observable/ | No dedicated README | Yes | Simulator-only, marked in module |

### API Documentation
- HTTP endpoints documented inline in `api_server_resilient.py` and `SentinelCore` class
- CLI documented as argparse help: `python obligation_sweep.py --help`
- No separate OpenAPI/Swagger spec found

---

## 12. DESIGN DECISIONS (Code-Enforced)

### Immutable Axioms (Found in Code)
| Principle | Enforcement |
|-----------|-------------|
| Ledger is append-only | BEFORE INSERT trigger on `ledger_entries` |
| Twin is independent witness | Separate sealed envelope, separate Postgres role |
| Actor self-report is distrusted | `episode.py` cross-checks; `actor_discrepancies` logged |
| Reason required on ANY outcome mismatch | `validate_episode()` enforces; fails on absent reason |
| Judge verdict doesn't drive behavior | Quality score still controls routing; kernel verdict rides alongside |
| No implicit cassette parameters | Anti-placeholder rule; fail-closed at load time |
| Regulatory lenses are read-only | `judge()` makes zero outside calls; `explain()` reports only |
| Disclosure precedes effect | Regulatory records flagged BEFORE action taken |

### Architectural Patterns (Observed)
| Pattern | Usage | Purpose |
|---------|-------|---------|
| Cassette interface ABC | Base for IVR, Banking, Mortgage, CFPB Lens | Domain pluggability |
| Capability registry | Anti-placeholder, require_capabilities gates | Fail-safe capability binding |
| Sealed envelope (X25519+AES-GCM) | Twin replica custody | Tamper detection |
| Four-fifths rule | Statistical cohort equity | Disparate impact detection |
| Confidence scale (5-tier) | Tier claims, unresolved outcomes | Provenance transparency |
| Maturation rule (hashed declaration) | Obligation lifecycle | Outcome durable binding |

---

## 13. REPOSITORY HEALTH METRICS

### Code Quality (as of HEAD commit 68cadfb)
| Metric | Status | Notes |
|--------|--------|-------|
| Test suite | 670/6 passed/skipped | Verified 2x back-to-back clean |
| Type checking (static) | Not enforced | No mypy/pydantic gate in CI |
| Linting (ruff) | 0 findings | Pinned 0.15.22, no deviations |
| Security (bandit) | 0 findings (Medium+) | nosec'd with per-line justification |
| Dead code (manual sweep) | Minimal (4 items cleaned July 25) | Grafana export, _fallback_bounds, patch artifact, CallerIntent |

### CI/CD Health
| Metric | Status |
|--------|--------|
| Last 5 runs | All green |
| First full green run | July 24, commit f0d11fa |
| Critical failures | None outstanding |
| Flaky tests | 1 (L11 latency assertion, passes in isolation) |

### Outstanding Patches (Awaiting Wm Application)
| Patch | Status | Dependencies | Lines Changed |
|-------|--------|--------------|----------------|
| `zip_county_regional_equity_check_v1.patch` | Ready | Cohort assembly merged | +200 est. |
| `abandon_on_modification_orchestration_v1.patch` | Ready | Cohort assembly merged | +300 est. |
| `cohort_sweep_scheduling_v1.patch` | Ready | Cohort assembly merged | +100 est. |

---

## 14. TIMELINE OF MAJOR WORK ITEMS (Observable)

| Date | Work | Commit(s) | Status |
|------|------|-----------|--------|
| July 13–19 | Phase 2 governance hardening, ICEBERG early build | Various | Merged, archived in buildlog-early |
| July 22–24 | Phase 2 completion, cassette kernel/capability split, regulatory cassettes, CI repair | 96d5ca3, b660e21, 2c0d5e3, 5c61e64 | Merged |
| July 24–25 | C2 bias dimensions 2–5, fraud escalation scoring, Bayes learning loop, PPO removal | 77d0f96, 2782d5b, 672fcfd, 9cdb759 | Merged |
| July 25 | Dead code sweep (Grafana, _fallback_bounds, patch artifact), CI full green | abcaf57, f0d11fa | Merged |
| July 28–29 | EventV1/OutcomeV1 framework, cohort assembly, mortgage cassette, call-ingestion contract | 9faa298, 703484e, 68cadfb | Merged (EventV1/cohort), mortgage applied |
| July 29–30 | Mortgage follow-ons (ZIP/county equity, ABANDON-on-mod), cohort sweep scheduling | Patches unmerged | Pending Wm review |

---

## SUMMARY

**What exists:**
- A production-ready governance kernel with tamper-evident dual-ledger (primary + sealed twin replica)
- Five regulatory compliance dimensions (prohibited basis, tier screen, narrative, statistical equity, correlation), with geographic equity pending wiring
- Three cassettes: IVR (reference), Banking (operational), Mortgage (first outcome-obligation implementation)
- Full event/outcome lifecycle: stamped event ingestion → episode assembly → kernel judgment + capability-specific processing → obligation tracking with maturation rules
- Comprehensive test coverage (670+ tests, fully green CI)

**What is working but incomplete:**
- Geographic equity dimension built, not wired to live decision path
- Real event sources defined by contract but no live ingestion (Twilio placeholder)
- Learning loop persistent state added (Redis), but old in-memory behavior remains
- Several patches awaiting application and final review

**What is intentionally deferred:**
- Additional loan-type cassettes (cancelled)
- Real RL/PPO training (on hold)
- GALLM real execution (stubbed)

**Artifact locations:**
- Production code: `sentinel_os/` directory
- Tests: `Tests/` directory
- Cassettes: `cassettes/`, `regulatory_cassettes/`
- Governance: `governance/`, `observe/`, `Domain/`, `Model/`, `Sim/`, `Engines/`
- CI: `.github/workflows/tests.yml`
- Infrastructure: `scripts/`, `docker-compose.yml`, `conftest.py`
