# Sentinel OS — Technical Architecture Map

**Source material:** `SENTINEL_OS_REPOSITORY_INVENTORY.md`, `SENTINEL_OS_DIRECTORY_MAP.md`, `SENTINEL_OS_QUICK_REFERENCE.md`
**Method:** Every statement below is traced to one of the three source documents. No claim is drawn from outside them.

**Classification key:**
- `[OBSERVED]` — stated directly in a source document
- `[DERIVED FROM DOCUMENTED RELATIONSHIP]` — not stated outright, but follows directly from two or more documented facts (e.g., a dependency graph entry plus a module table entry)
- `[UNKNOWN]` — the source documents do not contain the information; stated explicitly rather than inferred

---

# 1. System Boundary Map

### What is Sentinel OS?

- `[OBSERVED]` The Repository Inventory summary describes the built system as "a production-ready governance kernel with tamper-evident dual-ledger (primary + sealed twin replica)."
- `[OBSERVED]` The repository status is recorded, in both the Inventory and Quick Reference documents, as "Production repository, active development."
- `[OBSERVED]` The current tracked state is `origin/main` at commit `68cadfb`.
- `[UNKNOWN]` No source document contains a single canonical mission or product-definition statement for Sentinel OS as a whole; the description above is assembled from the "what exists" summary, not a stated charter.

### Inside the system boundary

- `[OBSERVED]` Governance kernel: `episode.py`, `event_v1.py`, `outcome_v1.py`, `cassette_interface.py`, `cassette_capabilities.py`, `cassette_schema.py`, `cassette_forensics.py`, `cassette_loader.py`.
- `[OBSERVED]` Ledger/verification subsystem (`governance/`): `ledger_postgres.py`, `verify_chain.py`, `twin_custody.py`, `canonical_fields.py`, `ledger_immutability.sql`.
- `[OBSERVED]` Regulatory compliance subsystem: `regulatory_cassette_interface.py`, `regulatory_checks.py`, `regulatory_deck.py`, `regulatory_cassettes/cfpb_reg_b.py`.
- `[OBSERVED]` Cassettes: `cassettes/ivr_cassette.py`, `cassettes/banking_cassette.py`, `cassettes/mortgage_cassette.py`.
- `[OBSERVED]` Live decision path: `production_harness.py`, `sentinel_worker.py`, `api_server_resilient.py`, `claude_governance_api.py`.
- `[OBSERVED]` Outcomes/events subsystem: `obligation_sweep.py`, `obligation_supersession.py`, `twilio_log_ingestion.py`.
- `[OBSERVED]` Inference engines (`Engines/`): `simple_rl_trainer.py`, `bayes_learning_loop.py`. `rl_ppo_adaptive.py` is listed as removed.
- `[OBSERVED]` Domain data structures (`Domain/`), system modeling (`Model/`), standalone simulator (`Sim/`), multi-domain platform (`observe/`).
- `[OBSERVED]` Utility modules: `rate_limiter_v2.py`, `cache_manager.py`.
- `[OBSERVED]` Test suite (`Tests/`) and maintenance scripts (`scripts/twin_ensure_services.sh`, `twin_sync_worker.py`, `twin_migrate.py`).

### Outside the system boundary / External dependencies

- `[OBSERVED]` PostgreSQL 16 — required infrastructure for the ledger and twin replica.
- `[OBSERVED]` Redis 7 — required infrastructure for rate limiting and belief-state persistence.
- `[OBSERVED]` Twilio API — external call data source; the streaming fetch path is documented as an unimplemented stub returning `[]`.
- `[OBSERVED]` US Census Geocoder — used for BISG estimation and ZIP/county extraction.
- `[OBSERVED]` Anthropic Claude API — used for decision explanations; the GALLM executor path is documented as stubbed (hardcoded return value).
- `[OBSERVED]` TLS certificate — self-generated at runtime if absent, per the Quick Reference configuration notes.

### Optional integrations

- `[OBSERVED]` Regulatory dimensions 2 (input authorization tier) and 3 (narrative legitimacy) are marked "Opt-in" in the Quick Reference regulatory dimension table; dimensions 1, 4, and 5 are marked "Default-on."
- `[OBSERVED]` Regulatory dimension 6 (geographic equity) is marked "Not wired" — implemented but not connected to the live C2 rollup.
- `[OBSERVED]` The Twilio real-time stream and the GALLM executor are both documented as present in code but not functionally implemented.
- `[DERIVED FROM DOCUMENTED RELATIONSHIP]` Because cassette capability declarations gate which parameters and methods a cassette may use (`cassette_capabilities.py`, anti-placeholder rule), every capability not declared by a given cassette is, by construction, optional to that cassette — e.g., the mortgage cassette is documented as declaring only the outcome-obligation capability and "no call-center surface at all."

### Human/operator touch points

- `[OBSERVED]` A named individual ("Wm") is documented as the party who applies patches, decides cassette scope, and makes governance calls — e.g., "Wm decided the mortgage cassette alone is sufficient," multiple patches marked "NOT yet applied by Wm."
- `[OBSERVED]` Deployment configuration is operator-set via environment variables (`ICEBERG_LEDGER_DSN`, `ICEBERG_REDIS_URL`, `SENTINEL_TWIN_RECEIVER_URL`, etc.), per the Quick Reference configuration section.
- `[OBSERVED]` The `obligation_sweep.py` CLI is operator-invoked with explicit flags (`--ledger-dsn`, `--receiver-url`, `--replica-id`, `--domain`, `--dry-run`).
- `[OBSERVED]` Three patches are documented as prepared but requiring manual application: `zip_county_regional_equity_check_v1.patch`, `abandon_on_modification_orchestration_v1.patch`, `cohort_sweep_scheduling_v1.patch`.
- `[UNKNOWN]` Whether operator roles exist beyond the single named individual referenced in the documents is not stated.

---

# 2. Layered Architecture Model

### Governance Kernel

**Purpose:** Domain-blind validation of observations, decisions, and cassette structure.
**Responsibilities:** Episode validation, event provenance stamping, outcome/obligation tracking, cassette interface/schema enforcement, code-hash tamper detection.
**Primary modules:** `episode.py`, `event_v1.py`, `outcome_v1.py`, `cassette_interface.py`, `cassette_capabilities.py`, `cassette_schema.py`, `cassette_forensics.py`, `cassette_loader.py`.
**Dependencies:** `outcome_v1.py` depends on `canonical_fields.py` (maturation-rule hashing); `cassette_interface.py` depends on `cassette_schema.py` and `cassette_forensics.py`; `cassette_capabilities.py` performs an ABC subclass check against `cassette_interface.py`.
**What it does NOT do:** The Directory Map annotates `episode.py` with "[No cassette imports — kernel is domain-blind]" — the kernel does not contain domain-specific decision logic.

### Data/Event Layer

**Purpose:** Convert raw call/decision inputs into provenance-stamped events.
**Responsibilities:** Event stamping (VERIFIED/ATTESTED/ESTIMATED), call-log parsing.
**Primary modules:** `event_v1.py`, `twilio_log_ingestion.py`.
**Dependencies:** `twilio_log_ingestion.py` depends on `event_v1.py` for stamping and `outcome_v1.py` for maturation rules; it is called from `production_harness.py`'s `_assemble_live_episode`.
**What it does NOT do:** `TwilioStreamAdapter.fetch_recent_calls()` is documented as returning `[]` — no live Twilio stream ingestion occurs.

### Decision Layer

**Purpose:** Assemble episodes from events and route them through cassette judgment.
**Responsibilities:** Live call processing, episode assembly, judgment invocation.
**Primary modules:** `production_harness.py`, `sentinel_worker.py`, `api_server_resilient.py`, `claude_governance_api.py`.
**Dependencies:** `production_harness.py` depends on `episode.py`, `cassette_interface.py`, `cassette_loader.py`, `governance/ledger_postgres.py`, `regulatory_deck.py` (optional), `twilio_log_ingestion.py`, `Engines/bayes_learning_loop.py`, `rate_limiter_v2.py`.
**What it does NOT do:** Per the documented "Additive, not a replacement" design call — the kernel judgment does not replace the existing `quality_score` path; `quality_score` still drives routing behavior while the kernel verdict is logged alongside it.

### Domain Cassette Layer

**Purpose:** Implement domain-specific judgment logic behind a common interface.
**Responsibilities:** Per-domain scoring, capability declaration, domain-specific data handling.
**Primary modules:** `cassettes/ivr_cassette.py`, `cassettes/banking_cassette.py`, `cassettes/mortgage_cassette.py`.
**Dependencies:** All three depend on `cassette_interface.py` (ABC) and `cassette_capabilities.py` (capability declaration). `ivr_cassette.py` additionally depends on `Engines/simple_rl_trainer.py` and `Engines/bayes_learning_loop.py`. `banking_cassette.py` optionally depends on `regulatory_checks.py`. `mortgage_cassette.py` depends on `bisg_estimator.py`.
**What it does NOT do:** The mortgage cassette is documented as declaring `CAPABILITIES = (CAPABILITY_OUTCOME_OBLIGATION,)` only — "kernel-only otherwise, no call-center surface at all."

### Regulatory Layer

**Purpose:** Provide read-only compliance evaluation over decisions.
**Responsibilities:** Six-dimension bias/compliance checking (proxy screen, tier screen, narrative screen, statistical equity, correlation proxy, geographic equity).
**Primary modules:** `regulatory_cassette_interface.py`, `regulatory_checks.py`, `regulatory_deck.py`, `regulatory_cassettes/cfpb_reg_b.py`.
**Dependencies:** `regulatory_cassette_interface.py` depends on `episode.py`, `cassette_interface.py`, `canonical_fields.py`. `regulatory_checks.py` uses statistical libraries and `bisg_estimator.py`.
**What it does NOT do:** Documented explicitly — "Regulatory lenses are read-only"; `judge()` "makes zero outside calls"; `explain()` "reports only." Dimension 6 is documented as "Not wired" into the live C2 rollup.

### Persistence Layer

**Purpose:** Durable, tamper-evident storage of decisions and obligations.
**Responsibilities:** Append-only ledger writes, chain verification, twin replica custody.
**Primary modules:** `governance/ledger_postgres.py`, `governance/verify_chain.py`, `governance/twin_custody.py`, `governance/canonical_fields.py`, `governance/ledger_immutability.sql`.
**Dependencies:** PostgreSQL 16, `psycopg2`, `cryptography` (X25519/AES-GCM for the twin's sealed envelope), `httpx` (twin REST API).
**What it does NOT do:** The Known Limitations table lists "PostgreSQL constructor lock optimization" as a "Known issue, not fixed."

### Interface Layer

**Purpose:** Expose HTTP and CLI access to the system.
**Responsibilities:** Judge/explain HTTP endpoints, ledger queries, CLI-driven cohort sweeps.
**Primary modules:** `api_server_resilient.py`, `obligation_sweep.py` (CLI), `sentinel_worker.py`.
**Dependencies:** `production_harness.py`, `governance/ledger_postgres.py`.
**What it does NOT do:** The Documentation Inventory section states: "No separate OpenAPI/Swagger spec found."

### Simulation Layer

**Purpose:** Offline, in-memory testing without live governance infrastructure.
**Responsibilities:** Batch cassette execution, concurrent batch processing.
**Primary modules:** `Sim/iceberg_complete_simulator.py`, `Sim/cluster_runner.py`, `Engines/simple_rl_trainer.py`.
**Dependencies:** `cassette_loader.py`, `episode.py`, `cassette_interface.py`. The Directory Map explicitly annotates this layer: "[No governance/ledger — in-memory only]."
**What it does NOT do:** The directory tree annotation for `cluster_runner.py` reads "Concurrent batch execution (run_batch() not stable-order)" — batch completion order is not guaranteed stable.

### Infrastructure Layer

**Purpose:** Deployment, provisioning, and continuous integration.
**Responsibilities:** Service orchestration, database/identity provisioning, test execution gating.
**Primary modules:** `.github/workflows/tests.yml`, `docker-compose.yml`, `Dockerfile`, `scripts/twin_ensure_services.sh`, `twin_sync_worker.py`, `twin_migrate.py`, `conftest.py`.
**Dependencies:** PostgreSQL 16 and Redis 7 as CI service containers; `pg_isready` healthcheck in `docker-compose.yml`.
**What it does NOT do:** The Build System section states that `test_twin_live.py` "requires Unix socket (peer-auth) + natively-installed Postgres (not available in current GitHub Actions job)" — CI as documented does not run the twin-live test suite.

---

# 3. Dependency Relationships

### Core modules

`[OBSERVED]` Listed in the Repository Inventory's "Core Governance Kernel" table: `episode.py`, `event_v1.py`, `outcome_v1.py`, `cassette_interface.py`, `cassette_capabilities.py`, `cassette_schema.py`, `cassette_forensics.py`, `cassette_loader.py`.

### Modules that depend on the core

| Module | Depends on core via | Evidence |
|---|---|---|
| `production_harness.py` | `episode.py` (assemble_episode), `cassette_interface.py` (judge/explain), `cassette_loader.py` (load_cassette) | Directory Map, "Production Live Path" dependency block |
| `cassettes/ivr_cassette.py`, `banking_cassette.py`, `mortgage_cassette.py` | `cassette_interface.py` (ABC), `cassette_capabilities.py` (capability declaration) | Directory Map, "Cassettes" dependency block |
| `regulatory_cassette_interface.py` | `episode.py` (DecisionMaterial adapters), `cassette_interface.py` (ABC parent) | Directory Map, "Regulatory Compliance" dependency block |
| `obligation_sweep.py` | `outcome_v1.py` (to_cohort_decision) | Directory Map, "Outcomes & Events" dependency block |
| `obligation_supersession.py` | `governance/ledger_postgres.py`, `governance/twin_custody.py` | Directory Map, "Outcomes & Events" dependency block |
| `Sim/iceberg_complete_simulator.py` | `cassette_loader.py`, `episode.py`, `cassette_interface.py` | Directory Map, "Simulator" dependency block |

### Domain implementations

`[OBSERVED]` `cassettes/ivr_cassette.py` (reference implementation, capabilities: telephony, routing, rl, self_healing), `cassettes/banking_cassette.py` (capabilities: routing, rl, self_healing), `cassettes/mortgage_cassette.py` (capability: outcome_obligation only).

### Governance enforcement points

`[OBSERVED]`, from the Repository Inventory's "Immutable Axioms" table:

| Principle | Enforcement location |
|---|---|
| Ledger is append-only | BEFORE INSERT trigger on `ledger_entries` |
| Twin is independent witness | Separate sealed envelope, separate Postgres role |
| Actor self-report is distrusted | `episode.py` cross-check; `actor_discrepancies` logged |
| Reason required on ANY outcome mismatch | `validate_episode()` |
| Judge verdict doesn't drive behavior | Quality score controls routing; kernel verdict rides alongside |
| No implicit cassette parameters | Anti-placeholder rule; fail-closed at load time |
| Regulatory lenses are read-only | `judge()` makes zero outside calls; `explain()` reports only |
| Disclosure precedes effect | Regulatory records flagged before action taken |

### External service boundaries

`[OBSERVED]`, from the Repository Inventory's "External Integrations" table:

| Service | Boundary point | Status |
|---|---|---|
| PostgreSQL | `governance/ledger_postgres.py` | Implemented |
| Redis | `rate_limiter_v2.py`, `Engines/bayes_learning_loop.py` | Implemented |
| Anthropic Claude API | `claude_governance_api.py` | Partial — explanations implemented, GALLM executor stubbed |
| Twilio Stream API | `TwilioStreamAdapter.fetch_recent_calls()` | Unimplemented |
| US Census Geocoder | `regulatory_checks.py` (dimension 6) | Referenced; live-call status not confirmed in source documents |

`[UNKNOWN]` The source documents do not describe authentication or contractual details for any of the above external services.

---

# 4. Runtime Flow

The following trace reconstructs the documented decision lifecycle strictly from code samples and module tables in the three source documents.

### Step 1 — Input

**Module:** `sentinel_worker.py` / `api_server_resilient.py`
**Input:** Call record (worker path) or HTTP request (API path).
**Output:** Raw call data handed to `production_harness.py`.
**Evidence from documents:** Inventory Entry Points table: `sentinel_worker.py` — "Process Twilio call records"; `api_server_resilient.py` — "Judge decisions, explain reasoning."

### Step 2 — Observation/Event creation

**Module:** `twilio_log_ingestion.py`, `event_v1.py`
**Input:** Raw call log.
**Output:** Stamped events (VERIFIED / ATTESTED / ESTIMATED).
**Evidence from documents:** Directory Map: "`twilio_log_ingestion.py` (REFACTORED July 29) → `event_v1.py` (VERIFIED/ESTIMATED stamping), `outcome_v1.py` (maturation rules), `production_harness.py` (called from `_assemble_live_episode`)."

### Step 3 — Episode assembly

**Module:** `episode.py`, `production_harness.py`
**Input:** Stamped events + decision input.
**Output:** `Episode` object.
**Evidence from documents:** Quick Reference code sample: `episode = Episode(events=[...], decision_input={...})`. Inventory table: `episode.py` — "Frozen observation record validation... Enforces reason-on-ANY-outcome-mismatch."

### Step 4 — Cassette execution

**Module:** `cassette_loader.py` (loading), `cassette_interface.py` (judge/explain contract), `cassettes/*.py` (domain logic).
**Input:** `Episode`.
**Output:** `QualityResult` + `ExplanationRecord`.
**Evidence from documents:** Quick Reference: `cassette = loader.load_cassette('ivr_cassette')` ... `judge(episode) -> QualityResult`; `explain(episode) -> ExplanationRecord`; also `result = harness.judge_episode(episode)`.

### Step 5 — Governance recording

**Module:** `production_harness.py` (`append_decision`), `governance/ledger_postgres.py`.
**Input:** `Episode` + `QualityResult` + `outcome_obligation` (if applicable).
**Output:** Hash-chained row in `ledger_entries`.
**Evidence from documents:** Quick Reference: `harness.append_decision(episode=episode, quality_score=0.85, outcome_obligation={...})` — "Writes to ledger, triggers twin derivation."

### Step 6 — Regulatory processing

**Module:** `regulatory_deck.py`, `regulatory_cassettes/cfpb_reg_b.py`.
**Input:** `Episode` / ledger row, via `DecisionMaterial` adapters.
**Output:** Regulatory disclosure event and findings (PASS / FLAG / INDETERMINATE per dimension).
**Evidence from documents:** Quick Reference: `findings = lens.judge(episode)` — "Returns: RegulatoryFindings with per-dimension flags/PASS/INDETERMINATE." Inventory table: `regulatory_deck.py` — "disclosure-first design (write event, then effect)."

### Step 7 — Outcome tracking

**Module:** `outcome_v1.py`, `obligation_ledger` table.
**Input:** `obligation_kind`, `opened_at`, `expected_by`.
**Output:** Obligation record in state OPEN, later transitioning to RESOLVED or ABANDONED.
**Evidence from documents:** Quick Reference database schema: `TABLE obligation_ledger (... state VARCHAR ('OPEN'|'RESOLVED'|'ABANDONED') ...)`. Key Decisions table: "Two records, two lifespans | Decision is permanent; obligation is durable | `episode.py`, `outcome_v1.py`, `obligation_ledger` table."

### Step 8 — Review/audit

**Module:** `obligation_sweep.py`, `governance/twin_custody.py`, `cohort_review_ledger` table.
**Input:** Resolved obligations fetched from the twin.
**Output:** `cohort_equity_review` posted to the twin for storage.
**Evidence from documents:** Quick Reference: `reviews = sweep(...)` — "Fetches resolved obligations, groups by domain+kind, runs dimensions 4–6, POSTs to twin for recording." Database schema: `TABLE cohort_review_ledger (... cohort_equity_review JSONB ...)`.

`[UNKNOWN]` The source documents do not state what triggers Step 8 to run automatically; the CLI is documented as operator-invoked, and no scheduling mechanism is described in the three source documents beyond the existence of the CLI flags.

---

# 5. Data Object Lifecycle

### Episode

- **Creation point:** `[OBSERVED]` Assembled in `production_harness.py`; instantiated per the Quick Reference sample as `Episode(events=[...], decision_input={...})`.
- **Modification rules:** `[OBSERVED]` Documented as a "Frozen observation record"; a reason is required "on ANY outcome mismatch" (`episode.py:actor_discrepancies` logic).
- **Storage location:** `[DERIVED FROM DOCUMENTED RELATIONSHIP]` No dedicated Episode table is listed in the documented schema (only `ledger_entries`, `obligation_ledger`, `cohort_review_ledger` appear); Episode content is reflected into the `governance_decision` JSONB field of `ledger_entries` at recording time.
- **Consumers:** `[OBSERVED]` `cassette_interface.py` (judge/explain), `regulatory_cassette_interface.py` (DecisionMaterial adapters).

### Event (EventV1)

- **Creation point:** `[OBSERVED]` `twilio_log_ingestion.py` / `event_v1.py`.
- **Modification rules:** `[OBSERVED]` Three-stamp system: VERIFIED / ATTESTED / ESTIMATED.
- **Storage location:** `[UNKNOWN]` The source documents do not state whether raw events are persisted independently of the Episode they are folded into.
- **Consumers:** `[OBSERVED]` `episode.py` (assembly), `outcome_v1.py` (per the Directory Map dependency graph, "provenance stamping").

### Outcome (OutcomeV1)

- **Creation point:** `[OBSERVED]` `outcome_v1.py`.
- **Modification rules:** `[OBSERVED]` "Every claim is stamped verified, attested, or estimated" — module docstring per the Key Decisions table ("The Provenance Rule").
- **Storage location:** `[OBSERVED]` `obligation_ledger.resolved_value` (JSONB).
- **Consumers:** `[OBSERVED]` `obligation_sweep.py`, `regulatory_checks.py` (statistical equity dimensions).

### Obligation

- **Creation point:** `[OBSERVED]` Written via `append_decision(..., outcome_obligation={...})`.
- **Modification rules:** `[OBSERVED]` State field constrained to OPEN / RESOLVED / ABANDONED.
- **Storage location:** `[OBSERVED]` `obligation_ledger` table (schema given in Quick Reference: `obligation_id`, `decision_hash`, `obligation_kind`, `domain`, `opened_at`, `expected_by`, `state`, `resolved_value`, `created_at`).
- **Consumers:** `[OBSERVED]` `obligation_sweep.py` (`fetch_resolved_obligations`), `obligation_supersession.py` (checks obligation state for decisions declaring `replaces_hash`).

### Cassette Manifest

- **Creation point:** `[OBSERVED]` Declared as class attributes (`CAPABILITIES`, `REQUIRED_GOVERNANCE_PARAMETERS`) per the Quick Reference "Adding a New Cassette" example.
- **Modification rules:** `[OBSERVED]` "No implicit cassette parameters" — enforced by the anti-placeholder rule in `cassette_capabilities.py` at load time.
- **Storage location:** `[DERIVED FROM DOCUMENTED RELATIONSHIP]` Not a database table; the manifest lives in code and is reflected into the `cassette_version` string bound at ledger write time (`domain:name:version` format, per the Quick Reference cassette versioning notes).
- **Consumers:** `[OBSERVED]` `cassette_loader.py`, `cassette_schema.py` (validation), `production_harness.py` (binding check via `require_cassette_binding`).

### Ledger Entry

- **Creation point:** `[OBSERVED]` `governance/ledger_postgres.py`, via `append_decision`.
- **Modification rules:** `[OBSERVED]` Append-only, enforced by a BEFORE INSERT trigger (`ledger_immutability.sql`); hash-chained via `current_hash`/`previous_hash`.
- **Storage location:** `[OBSERVED]` `ledger_entries` table (schema given: `id`, `current_hash`, `previous_hash`, `cassette_version`, `governance_decision` JSONB, `governance_params` JSONB, `outcome_obligation` JSONB, `outcome_harm_event` JSONB, `created_at`).
- **Consumers:** `[OBSERVED]` `governance/verify_chain.py`, `governance/twin_custody.py`, `obligation_sweep.py` (`fetch_decision_materials`), `api_server_resilient.py` (query interface).

### Regulatory Finding

- **Creation point:** `[OBSERVED]` `regulatory_checks.py` dimension checkers, surfaced through `regulatory_deck.py`.
- **Modification rules:** `[OBSERVED]` Read-only relative to the decision itself — `judge()` "makes zero outside calls"; disclosure is written before any effect ("Disclosure precedes effect").
- **Storage location:** `[DERIVED FROM DOCUMENTED RELATIONSHIP]` Per-decision disclosures are written as `regulatory_cassette_inserted` / `regulatory_disclosure` events on the ledger (Directory Map: "write `regulatory_cassette_inserted`"); cohort-level findings are recorded in `cohort_review_ledger.cohort_equity_review` (JSONB).
- **Consumers:** `[OBSERVED]` Callers of `CFPBRegBLens.judge()` / `.explain()`; `obligation_sweep.py` for dimensions 4–6.

---

# 6. Trust and Governance Model

This section documents mechanisms as described in the source material. It does not evaluate them.

### Immutability mechanisms

- `[OBSERVED]` `ledger_entries` is append-only, enforced by a BEFORE INSERT trigger defined in `governance/ledger_immutability.sql`.
- `[OBSERVED]` Rows are hash-chained: each `ledger_entries` row carries a `current_hash` and a `previous_hash` column.

### Verification mechanisms

- `[OBSERVED]` `governance/verify_chain.py` performs "Chain reconstruction and verification," described as having "Three recompute sites: writer, verify_chain, twin_custody" that must independently agree.
- `[OBSERVED]` The Repository Inventory's EventV1/OutcomeV1 build notes (folded from the "Defects Found & Fixed" content) state a rule: a new record kind is not considered complete until all three recompute sites agree.
- `[OBSERVED]` `verify_chain, findings = ledger.verify_chain()` is documented as a callable operation in the Quick Reference "Query Governance Trail" example.

### Provenance mechanisms

- `[OBSERVED]` `event_v1.py` stamps every observation as VERIFIED, ATTESTED, or ESTIMATED.
- `[OBSERVED]` `outcome_v1.py`'s module docstring is documented as carrying "The Provenance Rule": "Every claim is stamped verified, attested, or estimated, and they are not interchangeable. If it's unknown, Sentinel will timestamp why and what would close it."
- `[OBSERVED]` Regulatory dimension 2 implements a "7-tier ladder (T0–T6)" for input-authorization tier claims.

### Separation of responsibilities

- `[OBSERVED]` The twin replica uses a separate Postgres role (`twincustodian`) and a sealed envelope (X25519+AES-GCM), distinct from the primary ledger's runtime identity (`ledger_reader`).
- `[OBSERVED]` The governance kernel imports no cassette code ("kernel is domain-blind" per the Directory Map).
- `[OBSERVED]` Regulatory lenses cannot write during `judge()` — findings are logged as disclosures, and the lens interface is documented as read-only relative to decision content.

### Fail-closed behavior

- `[OBSERVED]` `cassette_capabilities.py` enforces an "anti-placeholder rule": a cassette cannot declare a parameter owned by a capability it has not enabled — enforced "at load time."
- `[OBSERVED]` Quick Reference "Common Errors": `RuntimeError: ICEBERG_LEDGER_RUNTIME_USER not set` is raised if the runtime identity is not explicitly configured.
- `[OBSERVED]` `production_harness.py`'s harness constructor example uses `require_cassette_binding=True`, described in the surrounding text as "Fail-closed."
- `[OBSERVED]` A `cassette_version_conflict` error is documented as occurring on a governance-decision hash mismatch, requiring the ledger to be rebuilt from the twin — described in the Quick Reference as "binding enforcement working correctly."

---

# 7. Capability Model

### Cassette capabilities

`[OBSERVED]`, from the Repository Inventory cassette table:

| Cassette | Capabilities | Outcome Obligation |
|---|---|---|
| `ivr_cassette` | telephony, routing, rl, self_healing | No |
| `banking_cassette` | routing, rl, self_healing | No |
| `mortgage_cassette` | (kernel + outcome_obligation only) | Yes |

### Capability registry

`[OBSERVED]` `cassette_capabilities.py` is documented as performing "Capability registration and parameter ownership," including the "Anti-placeholder rule" and "require_capabilities gates."

### Regulatory lenses

`[OBSERVED]`, from the Quick Reference regulatory dimension table:

| Dimension | Status | Wired to C2 rollup? |
|---|---|---|
| 1. Proxy Variable Screen | Implemented | Yes (default-on) |
| 2. Input Authorization Tier | Implemented | No (opt-in) |
| 3. Narrative Legitimacy | Implemented | No (opt-in) |
| 4. Statistical Outcome Equity | Implemented | Yes (default-on) |
| 5. Correlation Proxy Signal | Implemented | Yes (wired) |
| 6. Geographic Equity | Implemented | No (not wired) |

### Optional modules

- `[OBSERVED]` Regulatory dimensions 2 and 3 are opt-in per cassette configuration (`enable_input_authorization_tier_screen`, `enable_narrative_legitimacy_screen`).
- `[OBSERVED]` The GALLM executor is documented as present but stubbed — "returns hardcoded dict."
- `[OBSERVED]` The Twilio real-time stream adapter is present in code but documented as an unimplemented placeholder.

### Simulation-only components

`[OBSERVED]`, from the Directory Map tree annotations:

| Directory | Annotation |
|---|---|
| `Engines/` | "Simulator-only, no production (added July 23)" |
| `Domain/` | "Generic structures, no domain assumptions (added July 23)" |
| `Model/` | "Graph building (mutable, no versioning, added July 23)" |
| `Sim/` | "Batch processing, no async (added July 23)" |
| `observe/` | "Physiological early-warning (added July 23)" |

`[OBSERVED]` `Sim/iceberg_complete_simulator.py` is documented as using an in-memory ledger, explicitly annotated "[No governance/ledger — in-memory only]."

---

# 8. Architecture Glossary

**Sentinel OS** — The overall repository and system documented in the source material: a governance system that records, checks, and tracks the outcomes of decisions made by pluggable domain modules.

**Kernel** — The part of the system that doesn't know anything about any specific domain (like call centers or loans). It only knows how to check that a decision record is structurally valid and how to store it safely. Modules: `episode.py`, `event_v1.py`, `outcome_v1.py`, `cassette_interface.py`, `cassette_capabilities.py`, `cassette_schema.py`, `cassette_forensics.py`, `cassette_loader.py`.

**Cassette** — A plug-in module that adds domain-specific logic (for example, how to judge a call-center interaction versus a mortgage decision) on top of the domain-blind kernel. Three exist in the documented repository: IVR, Banking, Mortgage.

**Lens** — A read-only regulatory module that reviews a decision for compliance issues (like bias or missing justification) but cannot change the decision or write to it directly. Example: `CFPBRegBLens`.

**Episode** — A single frozen record of what happened for one decision: the events that were observed, the decision that was made, and any mismatches between what was expected and what actually happened.

**Event** — One observed fact about a decision (for example, a customer's wait time), tagged with how sure the system is that the fact is accurate (VERIFIED, ATTESTED, or ESTIMATED).

**Outcome** — What eventually happened after a decision was made — for example, whether a loan was paid off. Outcomes can take a long time to become known, so they are tracked separately from the decision itself.

**Obligation** — A tracked promise to eventually record an outcome for a decision. It stays "OPEN" until it is "RESOLVED" (the outcome is known) or "ABANDONED" (it no longer applies, such as when a loan is replaced by a new one).

**Twin** — A second, independently-controlled copy of the governance records, kept by a different identity than the main system, used to check whether the main copy has been tampered with.

**Provenance** — A label attached to every piece of information showing how the system knows it: whether it was directly verified, self-reported by someone the system doesn't fully trust (attested), or calculated/guessed (estimated).

**Ledger** — The main, append-only record of every decision the system has made. Rows cannot be changed or deleted once written; each row is cryptographically linked to the one before it.

---

## Traceability Note

Every claim in this document is grounded in one or more of:
- `SENTINEL_OS_REPOSITORY_INVENTORY.md`
- `SENTINEL_OS_DIRECTORY_MAP.md`
- `SENTINEL_OS_QUICK_REFERENCE.md`

Statements tagged `[UNKNOWN]` mark points where the three source documents do not provide enough information to answer the question posed by this architecture-map template. They are not filled in with assumptions.
