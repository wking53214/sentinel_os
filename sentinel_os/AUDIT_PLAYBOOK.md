# Regulatory Audit Playbook: What Sentinel OS's Ledger Can and Cannot Prove

**For:** EU AI Act, NAIC, TCPA auditors
**Required:** PostgreSQL read access to the ledger database. Section 2's repro steps need a disposable staging copy — never production.

## Executive Summary

This playbook describes what Sentinel OS's ledger can and cannot prove today, and how to check it. It is not a claim that the ledger is tamper-proof.

Sentinel is currently Phase 1: the governance ledger is a self-contained PostgreSQL table, verified by code that runs inside the same trust boundary it's verifying. Every check in this document — including `verify_chain()` — is generated, stored, and evaluated by the same operator a regulator would be investigating. That's an architectural choice, not an oversight, and this playbook is the honest audit path given that choice, not the bulletproof one.

In practice: `verify_chain()` reliably catches accidental corruption and naive in-place edits. It does not, and cannot, prove that an operator with database access hasn't rewritten history — an operator with that access can regenerate a chain that is internally consistent by construction. Section 2 shows exactly how, with runnable SQL, so this isn't a claim to take on faith.

Phase 2 ("Witness") closes this by moving proof outside the operator's control: an external timestamp authority anchors the chain head, and counterparties hold their own signed receipt of each decision at the time it's made. That work is scoped, not yet built (Section 4). Until it ships, the honest claim is: internally consistent, operator-attestable, not independently verifiable. Sections 3 and 5 cover what you actually can check today, and what to ask instead of trusting a single boolean.

### Before You Start

```bash
psql -h <ledger_host> -U <ledger_user> -d <ledger_db>
\dt   -- one relevant table: ledger_entries
```

Everything in this playbook queries `ledger_entries`. There's no separate table for cassette snapshots, decision records, or anything else — one table, described in full in Section 1.

## 1. What `verify_chain()` Actually Verifies

`verify_chain()` runs exactly one SQL query, then does the rest in Python — there's no separate "chain query" and "hash query."

```sql
SELECT id, record_kind, previous_hash, current_hash,
       action_type, node, previous_value, applied_value, reason,
       data, cassette_version, input_data, policy_parameters,
       decision_output, cassette_hash
FROM ledger_entries
ORDER BY id ASC;
```

For every row, in `id` order, it checks two things:

1. **Chain linkage** — this row's `previous_hash` equals the prior row's `current_hash` (the first row's `previous_hash` must be the literal string `"genesis"`).
2. **Content integrity** — it rebuilds a canonical JSON object from the row's own columns and recomputes `sha256(json.dumps(canonical, sort_keys=True))`. A mismatch against the stored `current_hash` is flagged as a violation.

The canonical object covers `record_kind`, `action_type`, `node`, `cassette_version`, `input_data`, `policy_parameters`, `reason` (as `reasoning`), `decision_output` (as `output`), `previous_value`, `applied_value`, a derived `parameter_changed` flag, `previous_hash`, and `cassette_hash` when present.

It does **not** cover `id`, `timestamp`, `call_sid`, or `cassette_snapshot`. Those can change without the hash changing, because they were never part of what got hashed. `id` and `timestamp` aren't semantically part of "what was decided"; `cassette_snapshot` and `call_sid` were added to the schema after the hash format was fixed. But it means "`verify_chain()` says `ok=True`" is a claim about the fields listed above — not about every column in the row. Sections 2 and 3 both depend on that distinction.

Within that scope, `verify_chain()` does what it claims: it catches a bit-flip, a truncated write, or someone editing `decision_output` through a database client without recomputing the hash. What it assumes is that whoever has database access hasn't disabled the protections built to stop exactly that — which is Section 2.

## 2. What It Doesn't Verify: One Precondition, Three Consequences

H3, H4, and H5 are usually described as three separate findings. They aren't independent — they share one precondition, and everything below follows from it.

### The precondition

`ledger_entries` is owned by the application's database role (`iceberg` by default). Table owners in PostgreSQL can alter their own tables regardless of what's been granted to anyone else, including disabling triggers:

```sql
ALTER TABLE ledger_entries DISABLE TRIGGER USER;
```

One statement disables all three protective triggers from `ledger_immutability.sql` — `prevent_ledger_update`, `prevent_ledger_delete`, `prevent_ledger_truncate` — at once. No superuser privilege required, just ownership. The deployment ships a separate, restricted `ledger_reader` role (SELECT + INSERT only, no ALTER) specifically to avoid this, but the application only connects as `ledger_reader` if `ICEBERG_LEDGER_RUNTIME_USER` is explicitly set. It is not set by default — an unconfigured deployment logs a warning and runs as the owning credential, the one that can run the statement above.

**Run everything below against a disposable copy of the schema. Never against a production ledger.**

### H3 — a full, internally-consistent rewrite verifies clean

With triggers disabled, forge a row's content, then recompute that row's hash and re-chain every row after it using the same canonical construction Section 1 describes:

```sql
-- after ALTER TABLE ... DISABLE TRIGGER USER
UPDATE ledger_entries
SET decision_output = decision_output || '{"approved": true, "reasoning": "REWRITTEN: this call was never rejected"}'::jsonb
WHERE id = 3;
-- current_hash for id=3, and previous_hash/current_hash for every row after it,
-- must then be recomputed using Section 1's canonical form — this step requires
-- running the same Python construction verify_chain() uses, not raw SQL
```

Live result: `verify_chain()` returns `ok=True`, zero violations, on a ledger where a rejection was rewritten into an approval. This is the core limit of a self-anchored chain: the party who could tamper with it is the same party whose code defines what "valid" means. A mathematically consistent chain isn't evidence of who wrote it.

### H4 — `cassette_snapshot` isn't part of what gets hashed

Section 1 already establishes this: `cassette_snapshot` is a real column, but never enters the canonical object `verify_chain()` hashes. With triggers disabled the same way, an operator can overwrite it — swapping in a different governance policy than the one that actually ran — and `verify_chain()` reports zero violations, because it never looked at that column.

```sql
-- after ALTER TABLE ... DISABLE TRIGGER USER
UPDATE ledger_entries
SET cassette_snapshot = '<a different policy version>'::jsonb
WHERE id = 3;
```

The check that *does* catch this is `validate_cassette_snapshot_chain()` — a separate function, a separate query, run separately. It reconstructs each decision's cassette from its stored snapshot and hash and flags the mismatch. The gap isn't that no check exists; it's that the previous version of this playbook never told anyone to run it. Fixed in Section 3.

### H5 — a full wipe verifies clean because there's nothing left to check

```sql
-- after ALTER TABLE ... DISABLE TRIGGER USER
DELETE FROM ledger_entries;
```

`verify_chain()` on an empty table returns `ok=True, entries=0` — technically correct, since there are no rows to violate anything. Nothing in the schema records that the table was ever populated. A wiped ledger and one that legitimately never received traffic look identical to this check.

### A fourth thing worth naming here

`timestamp` and `call_sid` aren't in the canonical object either (Section 1). A row's recorded time or Twilio call SID can be altered, with triggers disabled the same way, without invalidating that row's `current_hash`. This matters directly for Section 3's timing check below — it isn't something `verify_chain()` would catch.

## 3. Meaningful Spot-Checks

None of these prove tamper-evidence. They tell you whether the ledger looks plausible — consistent with normal operation — which is a real, useful, different thing.

**Row-count continuity.** `id` is a Postgres `SERIAL` sequence: monotonic, never reused. A gap indicates a missing row.

```sql
SELECT count(*) AS total_rows, min(id) AS min_id, max(id) AS max_id,
       (max(id) - min(id) + 1) - count(*) AS missing_rows
FROM ledger_entries;
```

A gap isn't proof by itself — a rolled-back transaction consumes a sequence value it never uses, so isolated single-row gaps can be entirely benign; treat `missing_rows > 0` as a reason to ask, not a verdict. And this returns `NULL` for every derived column on an empty table, not a reassuring `0` — if you're checking for a wipe, read `total_rows` directly.

**Decision volume over time.** No `decision_date` column — use `timestamp`:

```sql
SELECT date_trunc('day', timestamp) AS day, count(*)
FROM ledger_entries
WHERE record_kind = 'governance_decision'
GROUP BY 1 ORDER BY 1;
```

Watch for days that drop to near-zero against an otherwise steady baseline — a partial-wipe indicator, though also consistent with an outage; corroborate against call volume from the Twilio side before concluding either way.

**Field completeness, 20-row sample.**

```sql
SELECT id, input_data, policy_parameters, decision_output, cassette_version, reason
FROM ledger_entries
WHERE record_kind = 'governance_decision'
ORDER BY random() LIMIT 20;
```

Confirm none of those fields are null.

**Reasoning quality, 20 adverse decisions.**

```sql
SELECT id, reason, decision_output
FROM ledger_entries
WHERE record_kind = 'governance_decision'
  AND (decision_output->>'approved')::boolean = false
ORDER BY random() LIMIT 20;
```

A real governance decision cites the actual friction/policy trigger in `reason`; a generic or templated string across many rows is worth escalating.

**Timing plausibility.** Sample decisions roughly six months apart and cross-check `timestamp` against an out-of-band record — Twilio call logs, a CRM entry, anything Sentinel doesn't control. This is not a chain-integrity check: `timestamp` isn't in the canonical hash (Section 2), so `verify_chain()` would report `ok=True` whether or not a timestamp had been altered. The only way to catch a retroactive change is corroboration against a system Sentinel doesn't write to.

## 4. The Architectural Gap & Phase 2 Path

Every gap above reduces to one structural fact: the system is its own witness. The chain verifies itself, the cassette declares its own version, and the hash covers what the cassette says about its parameters, not what its code does. Every proof in Phase 1 is generated, stored, and checked by the same party a regulator would be auditing.

Phase 2 ("Witness") is scoped to externalize that:

- **External chain-head anchoring** (RFC 3161 timestamp authority / transparency log) — fixes H3 and H5. An operator can still rewrite their own table, but can't make the rewrite match an external signature that already committed to the old head.
- **Per-decision counterparty receipts** — the recipient gets a signed copy at decision time. History can't be rewritten unilaterally once someone else holds proof of the original. Fixes H3.
- **Content-addressed cassettes** — identity becomes a hash of full content, including code, loaded by hash rather than name. Fixes H2 and the related gap that the current hash covers parameters, not decision logic.
- **`cassette_snapshot` folded into the chain's canonical form** — becomes part of what `verify_chain()` actually hashes. Fixes H4.
- **Model identity recorded per decision** — every decision carries which model was requested and which model actually served it.
- **Schema-constrained governor input** — validated against a strict schema before assembly, closing the current f-string interpolation path.

This isn't blocking Phase 1's deployment, and Phase 1's core gate — reject-unless-approved — holds independently of any of this (verified live, not asserted). It's scoped for Phase 2. Until it ships, the honest posture is Section 5.

## 5. Auditor Q&A Checklist

Questions to ask, not queries to run — Sections 1–3 cover what's directly checkable.

- **Does the production application connect as the schema-owning role, or as the restricted `ledger_reader` role?** This is the real version of "can someone disable the triggers" — owners can always run `ALTER TABLE ... DISABLE TRIGGER`, so the question isn't whether that's possible, it's whether the running app has that access. Check whether `ICEBERG_LEDGER_RUNTIME_USER` is set in production. Expect: yes, set to `ledger_reader`.
- **Who else has direct database access, and is it logged?** Expect a documented, minimal list, logged separately from the ledger itself.
- **How are backups encrypted, and who holds the key?** Expect encryption at rest, key custody separate from whoever administers the database.
- **What's the incident response if a wipe or rewrite is discovered?** No watermark or external record currently surfaces one on its own — expect a manual/procedural answer today.
- **When does Phase 2 external anchoring ship?** Get a date or an honest "not yet scheduled."
- **How would you rate this system's trustworthiness today, for a consequential decision, in one sentence?** The answer should sound like Section 4, not the pre-rewrite `COMPLIANCE.md`.

## 6. Reference: Current Audit Findings

| Finding | What it shows | Status |
|---|---|---|
| H2 | `cassette_version` is a self-asserted string; nothing binds it to the policy content that ran | Confirmed, live |
| H3 | Full chain rewrite, re-hashed, verifies clean | Confirmed, live |
| H4 | `cassette_snapshot` forgery passes `verify_chain()`, caught only by `validate_cassette_snapshot_chain()` | Confirmed, live |
| H5 | Full ledger wipe verifies clean, `entries=0` | Confirmed, live |
| H7 | Governor prompt assembly has no delimiter/role separation against injected caller data | Confirmed, live |
| H8 | Trigger bypass needs only the app's own credential; runtime-user restriction isn't on by default | Confirmed, live |

H1 and H6 are referenced in this project's finding numbering but aren't in any material available for this playbook — no invented descriptions here. If they're real and just undocumented, point at the source and they'll get folded in with the same rigor as the rest of this table.

The full engine audit (F-A through F-I) and the Phase 2 Witness roadmap referenced in Section 4 aren't checked into this repository as of this writing — no path to link. Treat Section 4 as the current authoritative summary until they are.

## 7. Additional Verification Checks

These check separate claims — not tamper-evidence, which Sections 1–3 cover in full. Each was independently verified against live execution before shipping; only the column names below needed correcting against the real schema.

**7.1 Fail-Closed Governor** — every governor error resolves to `approved: false, risk_level: critical`.
```sql
SELECT id, action_type, (decision_output->>'approved')::boolean AS approved,
       decision_output->>'risk_level' AS risk_level,
       decision_output->>'reasoning' AS reasoning, timestamp
FROM ledger_entries
WHERE action_type = 'governance_decision'
  AND ((decision_output->>'parse_failed')::boolean = true OR (decision_output->>'governed')::boolean = false)
ORDER BY id DESC LIMIT 100;
```
Pass: every row `approved=false`, `risk_level=critical`.

**7.2 Intent Classification Persistence** — every decision carries intent fields.
```sql
SELECT id, input_data->>'intent_classification' AS intent_classification,
       (input_data->>'intent_confidence')::float AS intent_confidence,
       input_data->>'intent_reasoning' AS intent_reasoning, timestamp
FROM ledger_entries
WHERE action_type = 'governance_decision'
ORDER BY id DESC LIMIT 10;
```
Pass: all three non-null, confidence between 0 and 1.

**7.3 Cassette Governance** — every decision governed by a recorded cassette version.
```sql
SELECT id, policy_parameters->>'cassette_version' AS cassette_version,
       (policy_parameters->>'long_wait_threshold')::float AS long_wait_threshold, timestamp
FROM ledger_entries
WHERE action_type = 'governance_decision'
ORDER BY id DESC LIMIT 10;
```
Pass: `cassette_version` present, parameters non-null. Caveat this check can't resolve: `cassette_version` is self-asserted (H2) — nothing here binds the label to the parameters that actually governed the decision. This confirms a version was recorded, not that it was honest.

**7.4 Error Handling** — no approved decision also carries an error flag.
```sql
SELECT id, decision_output->>'reasoning' AS reasoning, timestamp
FROM ledger_entries
WHERE action_type = 'governance_decision'
  AND (decision_output->>'approved')::boolean = true
  AND ((decision_output->>'parse_failed')::boolean = true OR (decision_output->>'governed')::boolean = false)
ORDER BY id DESC LIMIT 100;
```
Pass: zero rows.

**7.5 Bias / No Hidden Branching**
```sql
SELECT input_data->>'intent_classification' AS intent, count(*) AS total,
       round(100.0 * sum(CASE WHEN (decision_output->>'approved')::boolean THEN 1 ELSE 0 END) / count(*), 2) AS approval_rate_pct
FROM ledger_entries
WHERE action_type = 'governance_decision' AND timestamp > now() - interval '30 days'
GROUP BY 1 ORDER BY 2;
```
Pass: non-`UNKNOWN` intents within roughly ±10% of each other.

**7.6 Decision Traceability**
```sql
SELECT id, node, input_data->>'intent_classification' AS intent,
       input_data->>'intent_reasoning' AS intent_reasoning,
       policy_parameters->>'cassette_version' AS cassette_version,
       (decision_output->>'approved')::boolean AS approved,
       decision_output->>'reasoning' AS governor_reasoning, timestamp
FROM ledger_entries
WHERE id = <decision_id>;  -- substitute an actual ID from an earlier query
```
Pass: every field populated for a sampled ID.

**7.7 Monitoring & Alerting** — not a ledger query.
```bash
curl '<prometheus_url>/api/v1/query?query=sentinel_governance_decisions_total'
curl '<prometheus_url>/api/v1/query?query=sentinel_governance_errors_total'
```
Pass: metrics updating with recent timestamps, error rate under 1%.

**7.8 Audit Trail Retention**
```sql
SELECT min(timestamp) AS oldest, max(timestamp) AS newest, count(*) AS total
FROM ledger_entries
WHERE action_type = 'governance_decision';
```
Pass: span matches expected deployment history. What this can't rule out: H5. A wiped-and-restarted ledger can look either legitimately young or suspiciously recent depending on what survived — this query alone can't distinguish the two. Cross-check against an external record of go-live date.

## 8. Audit Checklist & Findings Template

| # | Check | Section | Notes |
|---|---|---|---|
| 1 | Chain internal consistency | 1 | Confirms hashes match content within the scope Section 1 defines |
| 2 | Full-rewrite resistance | 2 (H3) | Does not pass — architectural, Phase 2 scoped |
| 3 | Cassette-snapshot forgery resistance | 2 (H4) | Does not pass — run `validate_cassette_snapshot_chain()`, not just `verify_chain()` |
| 4 | Wipe detection | 2 (H5) | Does not pass — no watermark exists yet |
| 5 | Row-count continuity | 3 | Plausibility signal, not proof |
| 6 | Fail-closed governor | 7.1 | Independently verified live |
| 7 | Intent persistence | 7.2 | |
| 8 | Cassette version presence | 7.3 | Presence only — see H2 caveat |
| 9 | Error handling | 7.4 | |
| 10 | Bias / hidden branching | 7.5 | |
| 11 | Decision traceability | 7.6 | |
| 12 | Monitoring active | 7.7 | |
| 13 | Audit retention | 7.8 | See H5 caveat |

Fill in your own results — this is a template, not a pre-graded scorecard.

## 9. Post-Audit

There's no single number this document can hand you. Checks 1 and 5–13 passing tells you the system is behaving as designed and hasn't suffered accidental corruption. It doesn't tell you the operator hasn't used database access to rewrite, forge, or erase history — items 2–4 are architectural gaps, not implementation bugs, and no query changes that until Phase 2 ships. The accurate claim to bring back to a decision-maker: *internally consistent and operator-attestable today; independently verifiable once Witness ships.* Anything stronger isn't supported by this system yet.

If checks 1 or 6–13 fail: that's a real defect, not an architectural limit — file it as an incident with Sentinel, not a footnote in your report.

## Contact & Support

- **Compliance:** compliance@sentinel-ai.com
- **Technical support:** support@sentinel-ai.com
- **Code:** github.com/wking53214/sentinel_os

All compliance documentation is version-controlled in the repo root.
