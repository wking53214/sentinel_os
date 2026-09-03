> **⚠ Point-in-time snapshot — not maintained.** The canonical description of Sentinel OS is the [repository root README](../../README.md).
>
> Major changes since this document was written: keyed `authorized_by` ledger attestation with key rotation (PR #28); the persisted observed-event layer (PR #29); and the extraction of the IVR/Iceberg application to the [GSA-815](https://github.com/wking53214/GSA-815) repo (PR #30, 2026-08-28) — the standalone simulator and its `Domain/` `Sim/` `Engines/` `Model/` `observe/` support tree, Twilio ingestion, the Claude governor client, and the queue/staffing/Bayes layer. The directory maps, dependency graphs, and counts below predate all of that.

---

# Sentinel OS — Quick Reference

**Current State:** Commit `68cadfb` (July 29, 2026)  
**Test Suite:** 670+ passed / 6 skipped, fully green  
**Status:** Production repository, active development  

---

## Entry Points

### Production Services
```bash
# Worker: Ingest call records, judge, record outcomes
python sentinel_os/sentinel_worker.py \
  --ledger-dsn "postgresql://iceberg:iceberg@localhost/iceberg" \
  --redis-url "redis://localhost:6379"

# API Server: HTTP endpoints for judge/explain/ledger queries
python sentinel_os/api_server_resilient.py \
  --port 8000 \
  --ledger-dsn "postgresql://iceberg:iceberg@localhost/iceberg"

# Batch Simulator: In-memory ledger, load testing
python sentinel_os/Sim/iceberg_complete_simulator.py \
  --batch-size 1000 \
  --cassette cassettes.ivr_cassette
```

### Maintenance Scripts
```bash
# Provision twin OS identities + Postgres roles
sudo scripts/twin_ensure_services.sh

# Generate cohort equity reviews + post to twin
python sentinel_os/obligation_sweep.py \
  --ledger-dsn "postgresql://iceberg:iceberg@localhost/iceberg" \
  --receiver-url "http://twin:7001" \
  --replica-id sentinel-primary-1 \
  --domain lending:mortgage

# Sync primary ledger to twin replica
python scripts/twin_sync_worker.py --ledger-dsn ... --receiver-url ...

# Twin schema migration
python scripts/twin_migrate.py --action migrate
```

### Testing
```bash
# Full suite (CI-equivalent)
cd sentinel_os/
python -m pytest . --ignore=test_twin_live.py -v --tb=short

# Linting + security
ruff check .
bandit -r . -ll

# Single test file
python -m pytest Tests/test_mortgage_cassette.py -v
```

---

## Key Decisions (Locked, Do Not Litigate)

| Decision | Rationale | Code Location |
|----------|-----------|---|
| Ledger is append-only | Immutable audit trail | `governance/ledger_immutability.sql` (BEFORE INSERT) |
| Twin is independent witness | Tamper detection without trust | `governance/twin_custody.py` (sealed envelope) |
| Actor self-report is distrusted | Cross-check against observed data | `episode.py:validate_episode()` |
| Reason required on ANY outcome mismatch | Governance transparency | `episode.py:actor_discrepancies` logic |
| Judge verdict doesn't drive behavior | Quality score still controls routing | `production_harness.py:process_call()` (kernel rides alongside) |
| No implicit cassette parameters | Fail-safe at load time | `cassette_capabilities.py` (anti-placeholder) |
| Regulatory lenses are read-only | No write authority on judge | `regulatory_deck.py:judge()` (zero external calls) |
| Disclosure precedes effect | Compliance logging must succeed first | `regulatory_deck.py:judge()` (regulatory_cassette_inserted) |
| Two records, two lifespans | Decision is permanent; obligation is durable | `episode.py`, `outcome_v1.py`, `obligation_ledger` table |
| The Provenance Rule | Every claim is stamped VERIFIED/ATTESTED/ESTIMATED | `outcome_v1.py` module docstring |

---

## Configuration & Credentials

### Environment Variables (Production)
```bash
ICEBERG_LEDGER_DSN="postgresql://ledger_reader:PASSWORD@postgres:5432/iceberg"
# Runtime identity: ledger_reader (SELECT + INSERT only)
# Fail-closed: RuntimeError if unset or if identity is table owner/superuser

ICEBERG_REDIS_URL="redis://redis:6379/0"
# Rate limiting + belief-state persistence
# Fail-open: in-memory fallback if unreachable

SENTINEL_TWIN_RECEIVER_URL="http://twin-replica:7001"
SENTINEL_TWIN_REPLICA_ID="sentinel-primary-1"
SENTINEL_TWIN_SHIP_TOKEN="<sealed-envelope-keymat>"
```

### Development (Conftest)
```python
# Tests/ conftest.py autouse fixtures
pytest.fixture(autouse=True)
def setup_ledger():
    # Creates: postgres:16 with iceberg/iceberg
    # Creates roles: ledger_reader (SELECT+INSERT)
    # Creates roles: sentinelsvc, twincustodian, twincustomer (twin)
```

### Docker-Compose
```yaml
# Services: postgres:16, redis:7, sentinel-api
# Healthcheck: pg_isready with 30s timeout
docker-compose up  # Starts all services
```

---

## Cassettes Reference

| Cassette | Domain | Capabilities | Outcome Obligation | Code Hash | Notes |
|----------|--------|--------------|-------------------|----|---|
| `ivr_cassette` | IVR/call-center | telephony, routing, rl, self_healing | ❌ No | 2.0.2 | Reference implementation |
| `banking_cassette` | Banking/lending | routing, rl, self_healing | ❌ No | 2.0.3 | High-risk routing |
| `mortgage_cassette` | Mortgage lending | (kernel + outcome_obligation only) | ✅ Yes | TBD | First real outcome implementation |

### Cassette Loading
```python
from sentinel_os.cassette_loader import CassetteLoader

loader = CassetteLoader(cassette_dir="sentinel_os/cassettes")
cassette = loader.load_cassette("ivr_cassette")
# Returns: IVRCassette instance with:
# - judge(episode) -> QualityResult
# - explain(episode) -> ExplanationRecord
# - manifest -> dict of CAPABILITIES + REQUIRED_GOVERNANCE_PARAMETERS
```

### Adding a New Cassette
```python
# 1. Extend cassette_interface.CassetteBase
# 2. Declare CAPABILITIES and REQUIRED_GOVERNANCE_PARAMETERS
# 3. Implement judge() and explain() methods
# 4. Implement get_maturation_rule() if enabling outcome_obligation
# 5. Add tests to Tests/test_<domain>_cassette.py
# 6. Bump version: domain:name:version

# Example:
class MyCassette(CassetteBase):
    CAPABILITIES = (
        CAPABILITY_ROUTING,
        CAPABILITY_OUTCOME_OBLIGATION,
    )
    
    REQUIRED_GOVERNANCE_PARAMETERS = {
        'outcome_horizon_days': int,
    }
    
    def judge(self, episode: Episode) -> QualityResult:
        # Domain logic
        pass
```

---

## Regulatory Compliance Dimensions

| Dimension | Status | Purpose | Wired to C2? | Notes |
|-----------|--------|---------|------|---|
| 1. Proxy Variable Screen | ✅ Implemented | Catch known-bad names | ✅ Default-on | `check_prohibited_basis_input_screen()` |
| 2. Input Authorization Tier | ✅ Implemented | Tier-based access control | ❌ Opt-in | 7-tier ladder (T0–T6) |
| 3. Narrative Legitimacy | ✅ Implemented | Policy adherence check | ❌ Opt-in | Free-text screening |
| 4. Statistical Outcome Equity | ✅ Implemented | Cohort four-fifths rule | ✅ Default-on | BISG race/ethnicity estimate |
| 5. Correlation Proxy Signal | ✅ Implemented | Renaming evasion detection | ✅ Wired July 25 | Numeric/boolean cohort analysis |
| 6. Geographic Equity | ✅ Implemented | Regional disparate-impact | ❌ Not wired | ZIP + county level (NEW July 30) |

### Enabling Regulatory Checks (IVR Example)
```python
from sentinel_os.regulatory_cassettes.cfpb_reg_b import CFPBRegBLens

lens = CFPBRegBLens(
    profile={
        'enable_input_authorization_tier_screen': True,  # Opt-in
        'enable_narrative_legitimacy_screen': True,      # Opt-in
        # Dimensions 1, 4, 5 always enabled
    }
)

findings = lens.judge(episode)
# Returns: RegulatoryFindings with per-dimension flags/PASS/INDETERMINATE
```

---

## Database Schema (Essentials)

### Primary Ledger (PostgreSQL)
```sql
-- Append-only governance decisions
TABLE ledger_entries (
    id BIGSERIAL PRIMARY KEY,
    current_hash VARCHAR UNIQUE NOT NULL,
    previous_hash VARCHAR UNIQUE,
    cassette_version VARCHAR NOT NULL,
    governance_decision JSONB,
    governance_params JSONB,
    outcome_obligation JSONB,                     -- NEW July 29
    outcome_harm_event JSONB,                     -- NEW July 29
    created_at TIMESTAMP,
    CONSTRAINT hash_chain CHECK (/* no rewriting */)
);

-- Obligation tracking (NEW July 29)
TABLE obligation_ledger (
    obligation_id UUID PRIMARY KEY,
    decision_hash VARCHAR REFERENCES ledger_entries,
    obligation_kind VARCHAR,
    domain VARCHAR,                               -- NEW July 30
    opened_at TIMESTAMP,
    expected_by TIMESTAMP,
    state VARCHAR ('OPEN'|'RESOLVED'|'ABANDONED'),
    resolved_value JSONB,
    created_at TIMESTAMP
);

-- Cohort equity reviews (NEW July 29)
TABLE cohort_review_ledger (
    review_id UUID PRIMARY KEY,
    domain VARCHAR,
    obligation_kind VARCHAR,
    cohort_equity_review JSONB,                   -- Dimensions 4–6 findings
    created_at TIMESTAMP
);
```

### Twin Replica (Sealed, Independent)
```sql
-- Same schema as primary, but sealed envelope
-- Custody separation: separate Postgres role (twincustodian)
-- Verification: peer-auth via Unix socket
```

---

## Common Operations

### Judge a Decision
```python
from sentinel_os.production_harness import IcebergProductionHarness
from sentinel_os.episode import Episode

harness = IcebergProductionHarness(
    cassette_name='ivr',
    require_cassette_binding=True,  # Fail-closed
)

episode = Episode(
    events=[...],  # List of stamped events
    decision_input={...},
)

# Kernel judgment (domain-blind)
result = harness.judge_episode(episode)
# Returns: (QualityResult, ExplanationRecord)

# Live judgment (with quality scoring)
quality, explanation = harness.process_call(call_data)
```

### Record a Decision
```python
harness.append_decision(
    episode=episode,
    quality_score=0.85,
    outcome_obligation={
        'obligation_kind': 'loan_decision',
        'opened_at': '2026-07-29T00:00:00Z',
        'expected_by': '2029-07-29T23:59:59Z',  # 3-year maturity
    }
)
# Writes to ledger, triggers twin derivation
```

### Generate Cohort Reviews
```python
from sentinel_os.obligation_sweep import sweep

reviews = sweep(
    ledger_dsn='postgresql://...',
    receiver_url='http://twin:7001',
    replica_id='sentinel-primary-1',
    domain='lending:mortgage',  # Optional filter
    dry_run=False,
)
# Fetches resolved obligations, groups by domain+kind,
# runs dimensions 4–6, POSTs to twin for recording
```

### Query Governance Trail
```python
ledger = PostgreSQLLedger(dsn='postgresql://...')

# Find decision by hash
decision = ledger.get_decision(current_hash)

# Verify chain integrity
clean, findings = ledger.verify_chain()

# Examiner query: all decisions for a domain
decisions = ledger.query_decisions(
    domain='lending:mortgage',
    start_date='2026-01-01',
    limit=1000,
)
```

---

## Testing Checklist

### Before Submitting PR
```bash
# 1. Full suite passes (CI-equivalent)
cd sentinel_os/
python -m pytest . --ignore=test_twin_live.py -v --tb=short
# Expected: 670+ passed / 6 skipped

# 2. Linting passes
ruff check .
# Expected: 0 findings

# 3. Security scan passes
bandit -r . -ll
# Expected: 0 findings (Medium+)

# 4. Cassette code hash hasn't silently drifted
python -m pytest Tests/test_cassette_forensics.py -v
# Expected: hash mismatch detection works

# 5. Twin verification works (local peer-auth)
./scripts/twin_ensure_services.sh
python -m pytest Tests/test_twin_live.py -v
# Expected: 18 passed (optional, for full verification)
```

### Debugging
```bash
# Single test with full traceback
python -m pytest Tests/test_<name>.py::test_<function> -vv --tb=long

# Print all debug output
python -m pytest -s -v Tests/test_<name>.py

# Run against live infra (if Postgres running)
python -m pytest --capture=no -k "test_live_path"
```

---

## Known Limitations & Workarounds

| Limitation | Impact | Workaround |
|-----------|--------|-----------|
| Phone-digit call-journey heuristic (fallback) | Inferred route may be wrong | Real event source needed (Twilio Studio/TaskRouter) |
| Fixed 0.1/0.5/0.4 wait-time split (fallback) | Wait friction over/under-counted | Real event timestamps needed (ivr_events contract ready) |
| Twilio real-time stream unimplemented | No live-call ingestion from Twilio API | Use mock call records or custom webhook |
| Geography dimension not wired to live judge | Redlining not prevented in real decisions | Apply `zip_county_regional_equity_check_v1.patch` (awaiting Wm review) |
| GALLM executor stubbed | No real AI explanation fallback | Use mock/cached explanations for now |
| PPO router removed | No trained RL available | Use SimpleRLTrainer instead (simple REINFORCE) |

---

## Outstanding Work (Awaiting Application)

### Patches Ready for Wm
```
sentinel_os/
├── zip_county_regional_equity_check_v1.patch
│   └── Adds geographic equity dimension to cohort sweeps
│
├── abandon_on_modification_orchestration_v1.patch
│   └── Handles loan modifications (new loan number = new obligation)
│
└── cohort_sweep_scheduling_v1.patch
    └── CLI scheduling + dry-run support for obligation_sweep.py
```

### Future Work (Planned but Cancelled/Deferred)
| Item | Status | Reason |
|------|--------|--------|
| Auto/personal/BNPL/payday cassettes | Cancelled | Mortgage alone sufficient |
| Real RL/PPO training | Deferred | On hold, explicitly revisit later |
| Per-decision regulatory wiring | Not started | Latency/reliability trade needs deliberate choice |
| Real Twilio ingestion | Not started | Source-agnostic contract ready; vendor choice pending |

---

## Acronyms & Terminology

| Term | Definition | Where |
|------|-----------|-------|
| **Episode** | Frozen observation record (inputs + decision + outcome) | `episode.py` |
| **Cassette** | Domain-specific implementation (IVR, Banking, Mortgage) | `cassettes/` |
| **Capability** | Opt-in feature (telephony, routing, RL, outcome-obligation) | `cassette_capabilities.py` |
| **Lens** | Read-only regulatory view (CFPB, etc.) | `regulatory_cassettes/` |
| **Twin** | Independent sealed-envelope replica (tamper detection) | `governance/twin_custody.py` |
| **Obligation** | Durable outcome-tracking record (loan decision → 3-year horizon) | `outcome_v1.py` |
| **Cohort** | Group of decisions (by domain + obligation_kind) for statistical testing | `obligation_sweep.py` |
| **Provenance** | VERIFIED / ATTESTED / ESTIMATED stamp on every claim | `event_v1.py` |
| **Maturation Rule** | Declaration of when an obligation resolves (e.g., `loan_performance@3y`) | `outcome_v1.py` |
| **C2 Rollup** | AND-logic across 6 regulatory dimensions | `regulatory_checks.py:rollup_c2_bias_identification()` |
| **Four-Fifths Rule** | Statistical disparate-impact threshold (4/5 of best group) | CFPB / ECOA standard |

---

## Maintenance Notes

### Updating Dependencies
```bash
# Pin new version in sentinel_os/requirements.txt
# Test locally, then update CI
pip install -r sentinel_os/requirements.txt --upgrade
python -m pytest . --ignore=test_twin_live.py
```

### Adding a Module
1. Place in appropriate directory (`governance/`, `cassettes/`, etc.)
2. Update `.github/workflows/tests.yml` if new test location
3. Add to `cassette_forensics._GOVERNANCE_CODE_MODULES` if kernel-related
4. Bump relevant cassette version (code hash changed)
5. Add README entry if directory-level

### Deploying a New Cassette
```bash
# 1. Tag & version
git tag -a cassettes/mortgage-v1.0.0

# 2. Bind at deployment time
# production_harness.py will hash-check + refuse mismatch

# 3. Verify binding in ledger
SELECT DISTINCT cassette_version FROM ledger_entries WHERE cassette_version LIKE 'lending:mortgage:%';
```

---

## Support & Diagnostics

### Common Errors

**`RuntimeError: ICEBERG_LEDGER_RUNTIME_USER not set`**
- Fix: Ensure runtime identity is set to `ledger_reader` role (fail-closed)

**`cassette_version_conflict: governance_decision hash mismatch`**
- Fix: Drop ledger_entries and rebuild from twin (binding enforcement working correctly)

**`permission denied for ledger_entries`**
- Fix: Verify runtime role has SELECT + INSERT (not UPDATE/DELETE)

**`test_twin_live.py: psycopg2.OperationalError`**
- Fix: Ensure `./scripts/twin_ensure_services.sh` has run; requires Unix socket peer-auth

### Verify System Health
```bash
# Health check: query governance trail
psql -U iceberg iceberg -c "SELECT COUNT(*) FROM ledger_entries;"

# Verify twin sync
curl http://twin:7001/replica/sentinel-primary-1/obligations | jq .

# Check belief state persistence
redis-cli GET "bayes:intent_stats:billing" | jq .

# Run full verification suite
python -m pytest Tests/test_governance_verification.py -v
```
