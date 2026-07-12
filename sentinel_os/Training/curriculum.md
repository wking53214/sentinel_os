# Sentinel OS / Iceberg — Engineering Curriculum

A structured onboarding path for the current, cassette-governed architecture.
Work the phases in order; each ends with runnable exercises and a mastery
check. Every file, symbol, endpoint, and command referenced here exists in
the repository as of this document's version — verify by running the
exercises rather than trusting prose.

## What this system is

Sentinel OS (the platform; "Iceberg" is the simulation/runtime lineage it
grew from) is an **AI-governance and forensic-audit layer for contact-center
IVR decisions**. It does not decide business policy. It enforces the policy a
customer declares, logs every AI recommendation with its reasoning, and chains
those log entries cryptographically so the audit trail can be proven intact.
The positioning is "Ariadne's thread for the compliance labyrinth": the system
does not promise you are compliant — it proves that, if you followed your
stated policy, the record shows you did, in a form a regulator can follow.

Two architectural ideas carry everything:

- **The boombox and the cassette.** The engine (the boombox) is
  domain-agnostic. Every domain-, risk-, and threshold-specific decision lives
  in a *cassette*. Swap the cassette, and the same engine governs a different
  domain. The engine never hardcodes a domain threshold; it reads it from the
  loaded cassette.
- **Fail closed.** When the governor cannot get an intelligible decision, it
  does not pass the action through — it returns unsafe and halts. Silence or a
  parse failure is treated as a rejection, never as an approval.

## How to use this curriculum

- Set up the environment once (see Phase 0), then work Phases 1–6 in order.
- Run every exercise. If an exercise's output does not match its description,
  that is a real signal — investigate before moving on.
- The Certification Checklist at the end is the bar for "onboarded."

---

## Phase 0 — Environment

### Objective
Get a clean checkout running the full test suite green.

### Setup
The dependency pins matter. The Anthropic SDK and `httpx` must be installed
together at compatible versions:

```
pip install -r requirements.txt --break-system-packages
```

`requirements.txt` pins `anthropic==0.116.0` with `httpx<0.28` on purpose:
0.116.0 raises a `proxies` keyword error on client construction under
httpx 0.28+. TLS-dependent tests need a certificate pair, which is gitignored;
generate an ephemeral one:

```
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem -out certs/cert.pem -days 365 -subj "/CN=localhost"
```

The ledger tests need a reachable PostgreSQL matching the `POSTGRES_*`
environment variables; without one they skip cleanly.

### Exercise
```
python3 -m pytest -q
```

### Mastery Check
You can explain why the suite reports skips (no live Postgres) versus failures
(missing certs), and you have a green run with certs generated.

---

## Phase 1 — Foundations

### Objective
Orient in the repository and name the moving parts.

### Key modules
- `production_harness.py` — the orchestration entry point
  (`IcebergProductionHarness`). It wires the cassette, the friction
  computation, the governor, and the ledger into one processing path.
- `cassette_loader.py` — how cassettes are discovered and loaded
  (`CassetteLoader`).
- `cassettes/` — the domain cassettes: `ivr_cassette.py`,
  `banking_cassette.py`.
- `sentinel_core.py` — the tier-translation seam (the cassette owns the score
  and tier; the core translates a tier via a fixed map and fails loudly on an
  unknown tier).
- `api_server_resilient.py` — the persistent HTTP service.
- `governance/` — `friction_core.py`, `ledger_postgres.py`, `drift_core_v1.py`,
  `self_heal_v1.py`, `recommend_v1.py`.
- `DEPLOYMENT.md` — the operational reference for environment and deploy paths.

### Core domain truths
These are load-bearing and non-obvious; internalize them:
- An IVR has no queue-dwelling states, only directionality. Hold music belongs
  to the ACD, not the IVR. Erlang C applies to agent-side waiting, not to the
  IVR itself.
- IVR success cannot be measured from inside the IVR. Abandonment is
  classified structurally and by intent, not by a duration threshold.

### Exercise
List the top-level runnable scripts and the governance modules:
```
grep -lE 'if __name__ == .__main__.' *.py
ls governance/
```

### Mastery Check
Given a file name from the repo, you can say which layer it belongs to
(cassette, harness, governor, ledger, server, or deploy).

---

## Phase 2 — The Cassette: the configuration contract

### Objective
Understand the cassette as the single source of truth for every domain
threshold, and how production loads it.

### Key modules
- `cassette_schema.py` — the typed schema: `GovernanceParameters`,
  `ParameterSpec`, `METADATA_SLOTS`, and `CassetteValidationError`. Every
  governance parameter is typed, carries min/max bounds, and has metadata
  slots (`approval_date`, `justification`, `last_reviewed`). Validation is
  fail-loud: a structurally invalid cassette raises rather than falling back to
  a default.
- `cassettes/ivr_cassette.py` — declares the IVR domain's parameters, including
  `governance_trigger` (the friction count at or above which a call is routed
  to the governor) and the self-healing clamp band for the expected-wait
  parameter. Every threshold carries a justification string.

### The rule
There are no domain-threshold literals in the engine. If the harness needs a
number to make a governance decision, it reads it from the loaded cassette. A
regression test exists specifically to break the build if a hardcoded
threshold is reintroduced (see Phase 6).

### Loading
`CassetteLoader.production_mode(domain)` loads exactly one named cassette for a
domain — it does not glob the directory, so a broken neighbor cassette can
never be silently pulled in. `load_all_cassettes(fail_on_invalid=True)` is the
only production-safe bulk mode: it fails on the first invalid cassette rather
than reporting partial success. `fail_on_invalid=False` exists for
debug/admin tooling only.

### Exercise
Load the IVR cassette and read its governance threshold directly:
```
python3 -c "
from cassette_loader import CassetteLoader
c = CassetteLoader.production_mode('ivr')
print(c.get_friction_thresholds())
"
```
You should see `min_friction_for_governance` and a `long_wait_threshold`
among the returned values.

### Mastery Check
You can state, without looking at the engine, where the governance trigger for
a domain is defined and how a customer would change it. (Answer: in that
domain's cassette; the engine reads it.)

---

## Phase 3 — The production harness and fail-closed governance

### Objective
Trace one call from friction measurement through the governance gate.

### The flow
1. Per-node waits are turned into a friction count. `friction_core.compute_friction(duration, long_wait_threshold)` returns 1 when a node's wait exceeds the cassette's long-wait threshold, else 0; the harness sums these across the call's journey nodes.
2. If the friction count meets the cassette's `governance_trigger` (inclusive), the call is routed to the governor. The trigger comes from the cassette, not from a literal in the harness.
3. The governor (`ClaudeGovernanceDecider`, backed by `claude_governance_api.py`) is asked to approve or reject. It is **fail-closed**: a parse failure, a non-boolean safety verdict, or a transport error all resolve to `safe: false` and halt. The governor's `reasoning` is captured and flows into the ledger.
4. Every decision — approval, rejection, or error — is logged. There is no path where an action is applied without a corresponding ledger entry.

### Key modules
- `production_harness.py` (`IcebergProductionHarness`) — the orchestration.
- `governance/friction_core.py` (`compute_friction`) — the single friction
  computation used on the governance path.
- `claude_governance_api.py` — the governor and its fail-closed gate.

### Exercise
Confirm the friction primitive's behavior:
```
python3 -c "
from governance.friction_core import compute_friction
print(compute_friction(30, 45), compute_friction(60, 45), compute_friction(120, 45))
"
```
Expected: `0 1 1` — a 30s wait under a 45s threshold is not friction; 60s and
120s each count as one.

### Mastery Check
You can describe exactly what the governor returns when the model's output
cannot be parsed, and why that is the safe behavior. (Answer: `safe: false`
and halt — an unintelligible decision must never be treated as approval.)

---

## Phase 4 — The forensic ledger

### Objective
Understand how decisions are recorded so the audit trail is provable.

### Key module
`governance/ledger_postgres.py` (`PostgreSQLLedger`). Entries are written to the
`ledger_entries` table. Each entry is linked into a **SHA-256 hash chain** so a
later entry commits to all earlier ones; tampering with any row breaks the
chain. Writes take a PostgreSQL advisory lock so concurrent appends cannot
interleave and corrupt the chain.

Structured decisions are written via `GovernanceDecisionRecord` and
`append_decision`; `get_decisions` reads them back. Both approvals and
rejections are recorded — the record of a blocked action is as important
forensically as an allowed one. The governor's reasoning is stored in the
entry's `reason` column.

### The forensic bar
This system is built to a standard where a forensic consultant could stake
professional testimony on it. That means: every decision is auditable
end-to-end (input to policy to reasoning to output), every parameter is
traceable to the cassette that declared it, and the chain proves the record
was not fabricated after the fact.

### Exercise (requires a live ledger database)
With a reachable Postgres and some processed calls, inspect the tail of the
ledger. The reasoning is in the `reason` column, and the table is
`ledger_entries`:
```
SELECT action_type, node, reason
FROM ledger_entries
ORDER BY id DESC
LIMIT 10;
```
Then verify chain integrity through the API's `/verify` endpoint (Phase 5).

### Mastery Check
You can explain how the ledger proves a decision was recorded at the time it
was made, and what a broken hash chain would indicate.

---

## Phase 5 — Serving, observability, and deployment

### Objective
Run the service, read its telemetry, and deploy it.

### The service
`api_server_resilient.py` is the persistent process — uvicorn on port 9090. Its
endpoints:

| Endpoint | Purpose |
|---|---|
| `/health` | Liveness/readiness probe target |
| `/metrics` | Prometheus metrics |
| `/status` | Service status |
| `/process` | Govern a single call |
| `/batch` | Govern a batch |
| `/ledger` | Read ledger entries |
| `/verify` | Verify hash-chain integrity |
| `/alerts` | Active alerts |
| `/dashboard` | Dashboard JSON (`grafana_dashboard.generate_dashboard_json`) |

Authentication is enforced by `api_key_auth.py`; keys are supplied via the
`ICEBERG_API_KEYS` environment variable.

### Environment
Per `DEPLOYMENT.md`: `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` /
`POSTGRES_USER` / `POSTGRES_PASSWORD` for the ledger; `CLAUDE_API_KEY` for live
governance (absent, the governor runs in fail-closed no-client mode);
`ICEBERG_API_KEYS` for the API server; `PORT` (default 9090);
`CERT_FILE` / `KEY_FILE` for TLS; `TWILIO_*` only if ingesting real call logs.

The API server is **stateless** — all persistence is the external Postgres
ledger. Nothing is written to local disk that must survive a restart. This is
why it scales horizontally cleanly.

### Deploy paths (see `DEPLOYMENT.md` for the authoritative version)
- **Local:** `docker-compose up -d` (dev stack), or `docker-compose-prod.yml`
  for the production variant with a bundled Postgres.
- **Container:** the `Dockerfile` installs `requirements.txt`, runs as a
  non-root user, and starts `api_server_resilient.py`; its healthcheck curls
  `/health`.
- **Kubernetes (minimal, direct-apply):** `k8s/` — `deployment.yaml`,
  `service.yaml`, `pvc.yaml`, plus a Secret shaped like
  `k8s/secret.yaml.example`. Apply with `kubectl apply -f k8s/`.
- **Kubernetes (HA / GitOps):** `Deploy/k8s/` is a Kustomize overlay that
  layers the production concerns the minimal set omits — a HorizontalPod
  Autoscaler, an Ingress with TLS termination, and a PodDisruptionBudget — on
  top of `k8s/` as the base. `Deploy/argocd/application.yaml` is a multi-source
  ArgoCD `Application` that deploys the base and the overlay together, so the
  two cannot drift apart.

### Exercise
Render the HA overlay and confirm it produces the three add-on resources:
```
kustomize build Deploy/k8s
```
Expected: a HorizontalPodAutoscaler (targeting the `iceberg` Deployment), an
Ingress (routing to `iceberg-service` on 9090), and a PodDisruptionBudget.

### Mastery Check
You can explain why `k8s/` and `Deploy/k8s/` are not two copies of the same
deployment, and what each is for.

---

## Phase 6 — Testing and conformance

### Objective
Know what the suite proves and how to extend it without weakening it.

### The suite
Tests live in `Tests/`. Run everything with `python3 -m pytest -q`. The
conformance-critical files:
- `Tests/test_cassette_governs_every_decision.py` — proves the running system
  reads the cassette at decision time: swapping the cassette changes the next
  decision, and ledger rows carry the cassette version plus the policy
  snapshot.
- `Tests/test_cassette_source_of_truth.py` — the hardcode-regression scanner.
  Reintroducing a domain-threshold literal into the engine makes this fail.
- `Tests/test_governor_failclosed.py` — proves the governor returns unsafe and
  halts on unintelligible output, with no API key required.

### Exercise
Run the three conformance files together:
```
python3 -m pytest \
  Tests/test_cassette_governs_every_decision.py \
  Tests/test_governor_failclosed.py \
  Tests/test_cassette_source_of_truth.py -q
```

### Mastery Check
You can name the one test that would fail if someone hardcoded a friction
threshold back into the harness, and the one that would fail if the governor
started treating a parse error as approval.

---

## Capstone — Add a domain the right way

### Objective
Extend the system to a new domain by adding a cassette, without touching an
engine threshold.

### Requirements
1. Create a new cassette under `cassettes/` following `cassette_schema.py` —
   typed parameters, min/max bounds, and populated metadata slots
   (`approval_date`, `justification`, `last_reviewed`) with a real
   justification per threshold.
2. Declare the domain's `governance_trigger` and self-healing bounds in the
   cassette. Do not add any of these numbers to the engine.
3. Load it via `CassetteLoader.production_mode('<your-domain>')` and process a
   call through the harness.
4. Add a conformance test in the style of
   `test_cassette_governs_every_decision.py` proving the harness honors your
   cassette's trigger, and confirm `test_cassette_source_of_truth.py` still
   passes.

### Success criteria
The engine code is unchanged. Every threshold your domain needs lives in your
cassette. The suite is green, and swapping your cassette for the IVR cassette
changes the governance behavior with no code change.

---

## Certification Checklist

You are onboarded when you can, without assistance:
- [ ] Run the full suite green, and explain every skip and how to clear it.
- [ ] Point to where any domain threshold is defined (the cassette) and change
      it as a customer would.
- [ ] Trace a call from friction measurement, through the `governance_trigger`
      gate, to a fail-closed governor decision, to a ledger entry.
- [ ] Explain what the governor does on unparseable output and why.
- [ ] Explain how the SHA-256 hash chain makes the ledger tamper-evident.
- [ ] Deploy the service via both the minimal `k8s/` set and the HA overlay,
      and explain the difference.
- [ ] Name the tests that guard cassette-as-source-of-truth and fail-closed
      governance.
- [ ] Add a new domain cassette that governs a call with no engine change.

---

## Versioning

This curriculum tracks the cassette-governed architecture. When the cassette
schema, the governance flow, the ledger schema, or the deploy topology changes,
update the affected phase and bump the version below.

Version: 2.0.0 — rewritten for the cassette-governed system (supersedes the
Iceberg Runtime 3.x curriculum, which described modules and RL engines that no
longer exist).
