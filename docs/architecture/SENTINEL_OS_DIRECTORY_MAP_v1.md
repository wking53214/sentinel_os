# Sentinel OS Directory Map & Module Dependencies

## Complete Directory Structure

```
sentinel-os/
├── .github/
│   └── workflows/
│       └── tests.yml                             # CI: pytest runner, services setup
│
├── .gitignore                                    # Includes: *.patch, *.bundle, certs/*.pem
│
├── sentinel_os/                                  # Main application package
│   ├── __init__.py
│   ├── requirements.txt                          # Python dependencies (pip install)
│   │
│   ├── GOVERNANCE KERNEL (Tamper-Evident)
│   ├── ─────────────────────────────────────────
│   ├── episode.py                                # Frozen observation + validation
│   ├── event_v1.py                              # VERIFIED/ATTESTED/ESTIMATED stamps (NEW July 29)
│   ├── outcome_v1.py                            # Outcome tracking + maturation rules (NEW July 29)
│   ├── cassette_interface.py                    # Kernel ABC (judge/explain/manifest)
│   ├── cassette_capabilities.py                 # Capability registry, anti-placeholder (NEW July 23)
│   ├── cassette_schema.py                       # Validation, version 2.0+ format
│   ├── cassette_forensics.py                    # Code-hash computation, tampering detection
│   ├── cassette_loader.py                       # Dynamic module loading + sys.modules (FIXED July 24)
│   │
│   ├── GOVERNANCE (Ledger & Verification)
│   ├── ──────────────────────────────────────
│   ├── governance/
│   │   ├── __init__.py
│   │   ├── README.md                            # Governance framework documentation
│   │   ├── ledger_postgres.py                   # PostgreSQL append-only ledger
│   │   ├── ledger_interface.py                  # Abstract ledger contract
│   │   ├── ledger_immutability.sql              # BEFORE INSERT trigger
│   │   ├── verify_chain.py                      # Chain reconstruction (3 sites: writer, verify, twin)
│   │   ├── twin_custody.py                      # Sealed replica (X25519+AES-GCM)
│   │   ├── canonical_fields.py                  # Hashing spec (OPTIONAL_HASHED_FIELDS for maturation rules)
│   │   └── cassette_forensics.py                # (duplicate entry point?)
│   │
│   ├── REGULATORY COMPLIANCE
│   ├── ──────────────────────
│   ├── regulatory_cassette_interface.py         # Lens ABC (observer/live modes) (NEW July 23)
│   ├── regulatory_checks.py                     # Dimensions 1–6: proxy, tier, narrative, stats, correlation, geographic
│   ├── regulatory_deck.py                       # Disclosure-first recording (NEW July 23)
│   ├── regulatory_cassettes/
│   │   ├── __init__.py
│   │   ├── README.md                            # Regulatory framework documentation
│   │   ├── cfpb_reg_b.py                        # CFPB Reg B reference lens (C2 opt-in dims 2–5)
│   │   └── [Other regulatory lenses as added]
│   │
│   ├── CASSETTES (Domain Implementations)
│   ├── ────────────────────────────────────
│   ├── cassettes/
│   │   ├── __init__.py
│   │   ├── README.md                            # Cassettes documentation
│   │   ├── ivr_cassette.py                      # IVR/call-center (reference impl, v2.0.2)
│   │   ├── banking_cassette.py                  # Banking/high-risk lending (v2.0.3)
│   │   └── mortgage_cassette.py                 # Mortgage lending (NEW July 29)
│   │
│   ├── LIVE DECISION PATH
│   ├── ───────────────────
│   ├── production_harness.py                    # Call processing, episode assembly, judgment
│   ├── sentinel_worker.py                       # Entry point: call ingestion worker
│   ├── api_server_resilient.py                  # Entry point: HTTP API (judge/explain)
│   ├── claude_governance_api.py                 # Claude/Anthropic API integration wrapper
│   │
│   ├── OUTCOMES & EVENTS (NEW July 29–30)
│   ├── ──────────────────────────────────
│   ├── obligation_sweep.py                      # Cohort assembly + equity review (CLI tool July 30)
│   ├── obligation_supersession.py               # Loan modification orchestration (NEW July 30)
│   ├── twilio_log_ingestion.py                  # Call log parsing (REFACTORED July 29)
│   │
│   ├── INFERENCE ENGINES (mostly simulator)
│   ├── ──────────────────────────────────────
│   ├── Engines/
│   │   ├── __init__.py
│   │   ├── README.md                            # Simulator-only, no production (added July 23)
│   │   ├── simple_rl_trainer.py                 # REINFORCE-style learning (actual trainer)
│   │   ├── rl_ppo_adaptive.py                   # [REMOVED July 25 — was dead/untrained stub]
│   │   ├── bayes_learning_loop.py               # Bayesian belief state (FIXED July 25)
│   │   └── [Other engines: similarity matching, intent classification]
│   │
│   ├── DOMAIN DATA STRUCTURES
│   ├── ────────────────────────
│   ├── Domain/
│   │   ├── __init__.py
│   │   ├── README.md                            # Generic structures, no domain assumptions (added July 23)
│   │   ├── Call.py                              # Call record structure
│   │   ├── Queue.py                             # Queue definition
│   │   ├── Result.py                            # Decision result
│   │   └── [Other generic domain objects]
│   │
│   ├── SYSTEM MODELING
│   ├── ─────────────────
│   ├── Model/
│   │   ├── __init__.py
│   │   ├── README.md                            # Graph building (mutable, no versioning, added July 23)
│   │   ├── Build_Graph.py                       # Topology builder (pre-existing known limitations)
│   │   └── [Graph-related modules]
│   │
│   ├── STANDALONE SIMULATOR
│   ├── ────────────────────
│   ├── Sim/
│   │   ├── __init__.py
│   │   ├── README.md                            # Batch processing, no async (added July 23)
│   │   ├── iceberg_complete_simulator.py        # Entry point: batch sim (uses in-memory ledger)
│   │   ├── cluster_runner.py                    # Concurrent batch execution (run_batch() not stable-order)
│   │   └── [Simulation support modules]
│   │
│   ├── MULTI-DOMAIN PLATFORM (observe/perceive)
│   ├── ──────────────────────────────────────
│   ├── observe/
│   │   ├── __init__.py
│   │   ├── README.md                            # Physiological early-warning (added July 23)
│   │   └── [Platform code]
│   │
│   ├── RATE LIMITING & CACHING
│   ├── ─────────────────────────
│   ├── rate_limiter_v2.py                       # Redis-backed rate limiting (circuit breaker pattern)
│   ├── cache_manager.py                         # Cache utilities
│   │
│   └── TEST CONFIGURATION
│       └── conftest.py                          # Root conftest: DB role setup, TLS cert generation
│
├── Tests/                                       # Test suite (NOT mirrored to source)
│   ├── conftest.py                              # Per-test fixtures (test_ledger, etc.)
│   ├── test_*.py                                # ~280+ test files
│   │   ├── test_governance_*.py                 # Ledger, verification, twin tests
│   │   ├── test_cassette_*.py                   # Cassette loading, validation
│   │   ├── test_regulatory_*.py                 # Regulatory lens tests (C2 dimensions)
│   │   ├── test_episode.py                      # Episode validation tests (NEW July 23)
│   │   ├── test_event_v1.py                     # Event stamping tests (NEW July 29)
│   │   ├── test_outcome_v1.py                   # Outcome tracking tests (NEW July 29)
│   │   ├── test_live_path_*.py                  # Production path integration
│   │   ├── test_mortgage_cassette.py            # Mortgage cassette tests (NEW July 29)
│   │   ├── test_obligation_sweep.py             # Cohort assembly tests (NEW July 29)
│   │   ├── test_ivr_events_contract.py          # Event ingestion contract tests (NEW July 29)
│   │   ├── test_api_*.py                        # API endpoint tests
│   │   ├── test_twin_live.py                    # Twin replica tests [EXCLUDED from CI]
│   │   ├── test_graph_integrity.py              # Model/graph validation
│   │   ├── test_system_readiness.py             # System-level import/config checks
│   │   └── [70+ more test files]
│   │
│   └── test_twin_live.py                        # [CI-EXCLUDED: needs peer-auth + Unix socket]
│
├── cassettes/                                   # Production cassette implementations (symlink or duplicate?)
│   ├── ivr_cassette.py
│   ├── banking_cassette.py
│   └── mortgage_cassette.py
│
├── regulatory_cassettes/                        # Regulatory lens implementations
│   ├── cfpb_reg_b.py
│   └── [Other regulatory lenses]
│
├── scripts/
│   ├── twin_ensure_services.sh                  # Provision OS identities + Postgres roles (NEW July 23)
│   ├── twin_sync_worker.py                      # Sync primary to twin replica
│   ├── twin_migrate.py                          # Schema migration (twin-side)
│   └── [Other deployment scripts]
│
├── docs/
│   ├── CHANGELOG.md                             # Version history (current as of July 22)
│   └── [Additional documentation]
│
├── conftest.py                                  # Root pytest configuration (autouse fixtures)
│
├── requirements.txt                             # Root dependency list
│
├── README.md                                    # Top-level documentation
│
├── COMPLIANCE.md                                # Regulatory/compliance baseline (FIXED July 23)
│
├── MODEL_CARD.md                                # System documentation (FIXED July 23)
│
├── docker-compose.yml                           # Local deployment orchestration
│
├── Dockerfile                                   # Container image definition
│
├── certs/                                       # [.gitignore'd] Generated at runtime
│   ├── cert.pem                                 # Self-signed TLS certificate
│   └── key.pem                                  # Private key
│
└── [REMOVED FILES]
    ├── Engines/rl_ppo_adaptive.py              # [REMOVED July 25 — dead PPO stub]
    ├── sentinel_cassette_snapshot_forensics_v1.patch  # [REMOVED July 25 — stale delivery artifact]
    └── Deploy/grafana/                         # [REMOVED July 25 — dead Grafana export code]
```

---

## Module Dependency Graph (Production Path Only)

### Governance Kernel (Core, No External Deps Except DB)
```
episode.py
  ├→ outcome_v1.py (maturation rules validation)
  ├→ cassette_schema.py (schema version checking)
  └→ [No cassette imports — kernel is domain-blind]

event_v1.py
  └→ outcome_v1.py (provenance stamping)

outcome_v1.py
  └→ canonical_fields.py (maturation rule hashing)

cassette_interface.py
  ├→ cassette_schema.py (validation)
  └→ cassette_forensics.py (code hash)

cassette_capabilities.py
  ├→ [No imports — pure registry]
  └→ cassette_interface.py (ABC subclass check)

cassette_forensics.py
  └→ governance/canonical_fields.py (hash computation)

cassette_loader.py
  └→ cassette_interface.py (type checking)
  └→ sys.modules (built-in registration)
```

### Ledger & Verification (Governance)
```
governance/ledger_postgres.py
  ├→ governance/canonical_fields.py (record serialization)
  ├→ governance/ledger_immutability.sql (SQL triggers)
  ├→ psycopg2 (PostgreSQL client)
  └→ cryptography (Ledger sealing)

governance/verify_chain.py
  ├→ governance/ledger_postgres.py (query interface)
  ├→ governance/canonical_fields.py (hash reconstruction)
  └→ governance/twin_custody.py (twin verification)

governance/twin_custody.py
  ├→ governance/ledger_postgres.py (replica storage)
  ├→ cryptography (X25519, AES-GCM)
  └→ httpx (REST API to primary)
```

### Regulatory Compliance (Opt-In, Read-Only)
```
regulatory_cassette_interface.py
  ├→ episode.py (DecisionMaterial adapters)
  ├→ cassette_interface.py (ABC parent)
  └→ canonical_fields.py (evidence hashing)

regulatory_checks.py
  ├→ numpy/scipy (statistical tests)
  ├→ bisg_estimator.py (race/ethnicity estimation)
  └→ [No write side — only flagging]

regulatory_deck.py
  ├→ regulatory_cassette_interface.py (lens registry)
  ├→ governance/ledger_postgres.py (write regulatory_cassette_inserted)
  └→ episode.py (read decision input)

regulatory_cassettes/cfpb_reg_b.py
  ├→ regulatory_checks.py (dimension checkers)
  ├→ regulatory_cassette_interface.py (lens ABC)
  └→ [Config data — no code logic imports]
```

### Production Live Path
```
production_harness.py
  ├→ episode.py (assemble_episode)
  ├→ cassette_interface.py (judge + explain)
  ├→ cassette_loader.py (load_cassette)
  ├→ governance/ledger_postgres.py (append_decision)
  ├→ regulatory_deck.py (optional regulatory insertion)
  ├→ twilio_log_ingestion.py (parse call log → events)
  ├→ Engines/bayes_learning_loop.py (update beliefs)
  └→ rate_limiter_v2.py (circuit breaking)

sentinel_worker.py
  └→ production_harness.py (process_call entry point)

api_server_resilient.py
  ├→ production_harness.py (judge/explain)
  ├→ governance/ledger_postgres.py (query interface)
  └→ [HTTP server layer]
```

### Outcomes & Events (NEW)
```
obligation_sweep.py
  ├→ outcome_v1.py (to_cohort_decision)
  ├→ governance/twin_custody.py (fetch_resolved_obligations)
  ├→ governance/ledger_postgres.py (fetch_decision_materials)
  ├→ regulatory_checks.py (check_statistical_outcome_equity)
  └→ [CLI/argparse (no cassette imports)]

obligation_supersession.py
  ├→ governance/ledger_postgres.py (find replaces_hash decisions)
  └→ governance/twin_custody.py (check obligation state)

twilio_log_ingestion.py (REFACTORED July 29)
  ├→ event_v1.py (VERIFIED/ESTIMATED stamping)
  ├→ outcome_v1.py (maturation rules)
  └→ production_harness.py (called from _assemble_live_episode)
```

### Cassettes (Domain-Specific, Loadable)
```
cassettes/ivr_cassette.py
  ├→ cassette_interface.py (ABC: judge, explain, manifest)
  ├→ cassette_capabilities.py (declares: telephony, routing, rl, self_healing)
  ├→ Engines/simple_rl_trainer.py (routing optimization)
  └→ Engines/bayes_learning_loop.py (intent classification)

cassettes/banking_cassette.py
  ├→ cassette_interface.py (ABC)
  ├→ cassette_capabilities.py (declares: routing, rl, self_healing)
  └→ regulatory_checks.py (optional: fraud signals)

cassettes/mortgage_cassette.py (NEW July 29)
  ├→ cassette_interface.py (ABC: judge, explain, manifest)
  ├→ cassette_capabilities.py (declares: outcome_obligation only)
  └→ bisg_estimator.py (geography extraction for regional equity)
```

### Simulator (Standalone, In-Memory)
```
Sim/iceberg_complete_simulator.py
  ├→ cassette_loader.py (load domain cassettes)
  ├→ episode.py (assemble observations)
  ├→ cassette_interface.py (judge offline)
  ├→ [No governance/ledger — in-memory only]
  └→ Sim/cluster_runner.py (concurrent batches)

Engines/simple_rl_trainer.py
  └→ [Standalone, no cassette imports]
```

---

## External Dependencies (Non-Python Modules)

### Infrastructure (Runtime)
| Service | Consumer | Required For |
|---------|----------|--------------|
| PostgreSQL 16 | `governance/ledger_postgres.py` | Append-only ledger, twin replica, governance records |
| Redis 7 | `rate_limiter_v2.py`, `Engines/bayes_learning_loop.py` | Circuit breaking, belief-state persistence |
| TLS Cert | `governance/twin_custody.py` | Sealed envelope encryption (self-generated if missing) |

### External Services (Optional, Untested Live)
| Service | Consumer | Status |
|---------|----------|--------|
| Twilio API | `production_harness.py` → `twilio_log_ingestion.py` | Live ingestion unimplemented (fetch_recent_calls stub) |
| US Census Geocoder | `regulatory_checks.py` (dimension 6) | BISG + ZIP/county extraction |
| Anthropic Claude API | `claude_governance_api.py` | Decision explanations (GALLM executor stubbed) |

---

## Test Organization

### Test-to-Code Mapping
| Test Pattern | Source Coverage | Count |
|--------------|-----------------|-------|
| `test_governance_*.py` | `governance/` modules | ~80 tests |
| `test_cassette_*.py` | Cassette loading & validation | ~40 tests |
| `test_regulatory_*.py` | Regulatory lenses & C2 checks | ~100 tests |
| `test_episode.py` | `episode.py` validation | ~15 tests |
| `test_event_v1.py` | `event_v1.py` stamping | ~20 tests |
| `test_outcome_v1.py` | `outcome_v1.py` tracking | ~30 tests |
| `test_live_path_*.py` | Production harness, API endpoints | ~60 tests |
| `test_mortgage_cassette.py` | Mortgage cassette (NEW) | 27 tests |
| `test_obligation_sweep.py` | Cohort assembly (NEW) | ~20 tests |
| `test_ivr_events_contract.py` | Event ingestion contract (NEW) | 19 tests |
| `test_twin_live.py` | Twin replica (CI-EXCLUDED) | 18 tests |
| [Other] | Domain, simulators, utilities | ~100+ tests |
| **TOTAL** | | **~670+ passed** |

---

## File Size & Complexity Markers

### Large Modules (500+ LOC)
- `production_harness.py` — production live path
- `governance/ledger_postgres.py` — ledger implementation
- `cassette_forensics.py` — code hash + validation
- `regulatory_checks.py` — multi-dimension compliance checks

### Complex Test Files (100+ tests each)
- `Tests/test_regulatory_cassettes.py` (C2 dimensions)
- `Tests/test_governance_verification.py` (chain integrity)
- `Tests/test_live_path_kernel_wiring.py` (end-to-end)

### Recently Changed (July 29–30)
- `production_harness.py` (refactored for EventV1 integration)
- `obligation_sweep.py` (new, 17 tests)
- `obligation_supersession.py` (new, 24 tests)
- `twilio_log_ingestion.py` (refactored for generic events)

---

## Build Artifacts & Generated Files

### Generated at Runtime (Not Committed)
- `certs/cert.pem` — self-signed TLS certificate
- `certs/key.pem` — private key
- `__pycache__/` — Python bytecode
- `.pytest_cache/` — pytest cache

### Generated & Committed (Now Removed)
- `sentinel_cassette_snapshot_forensics_v1.patch` (REMOVED July 25)
- `.github/workflows/tests.yml` (UPDATED July 24)

### Ignored by .gitignore
- `*.patch`, `*.bundle`, `*.diff`, `*.git-archive` (added July 23)
- `certs/*.pem`, `certs/*.key`, `certs/*.crt` (added July 22)

---

## Import Safety & Circular Dependency Status

**No circular imports detected in production path** (confirmed by test suite importing all modules).

**Safe import patterns:**
- Cassettes import interfaces, not vice versa (ABC in kernel)
- Governance imports only schema + cryptography, not business logic
- Tests import everything (allowed to be "unsafe" for fixtures)

**One multi-definition noted:**
- `cassette_forensics.py` appears in both `sentinel_os/` and `sentinel_os/governance/` (verify which is live)
