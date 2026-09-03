# Post-snapshot corrections — 2026-09-03

The nine `SENTINEL_OS_0*_v3.md` documents in this directory are a **frozen,
commit-anchored audit of the repository as it stood at `68cadfb` (July 29,
2026)**. They are not maintained, and by design they are not being rewritten:
their value is that every `FACT` line is anchored to a specific commit and a
specific source document, and a reader can check them against that baseline.

This addendum does something narrower. It reconciles the **forward-looking
quantitative and status claims** a present-day reader would otherwise take as
current — test totals, the security-scan framing, the CI gate set, and the
one open contradiction the audit itself flagged (mortgage cassette "not in CI
yet"). It does **not** re-verify the ~324 `FACT` lines about `68cadfb`; those
remain historical statements, true as of their baseline and labelled as such.

Baseline for the numbers below: **`main` at `4d99118`** (2026-09-03, after
PR #45). The CI figures are from the last green run at that tree; PR #46 (the
PR that adds this file) re-runs the same four gates and any reader can check
them on that PR.

---

## 1. Test suite total

| The v3 docs say | Current |
|---|---|
| "Roughly 670 tests pass with 6 skipped" (Docs 1, 3, 5) | **See the CI figure below.** |
| "384–673 tests depending on current branch" (Docs 6 C-12, 7 slide 8) | The range is retired. `pytest .` from `sentinel_os/` with **no exclusions** collects **959 tests**, and CI runs all of them — the twin live-suite (18), the mortgage cassette (37), and the conservation boundary suite included. There is no longer a branch-dependent subset. |

**Current CI result:** **937 passed / 22 skipped**, whole tree, twin tests
included (`main` at `4d99118`; the pre-merge PR #45 run reported the same, and
PR #46's run reproduces it). 2 warnings, 0 failures, 0 errors.

This is the run C-12 asked for: *"a CI run that includes the twin tests, on a
stated commit, with the exact figure and the suppression list published
alongside."* The suppression list is section 2.

The growth from ~670 to 937 is real new test code, not a counting change —
principally PRs #35–#45: the conservation boundary suite, the 79-check
GSA-815 contract test, the ledger-operations and mortgage-population work.

Not re-investigated: the "one test known-flaky under load" note in the v3
docs. It is not among the skips and was not chased down here.

**Doc 9 F-2 / Doc 6 C-12** ("the 670 figure circulates unqualified in Docs 1
and 5, the two most likely to travel alone"): every doc, Docs 1 and 5
included, now opens with a banner that scopes its test count as historical and
links here. That is where the qualification lives — one place, kept current —
rather than a hand-copied caveat in each. Treat F-2 as satisfied by the banner
link.

---

## 2. Security scan (bandit)

| The v3 docs say | Current |
|---|---|
| "Zero bandit findings at medium severity and above … reached by annotating and justifying one high and seventeen medium findings" (Docs 1, 2 §238, 3 §42, 4 §266, 7 §176) | **Obsolete framing.** The gate was widened on 2026-09-03 (PR #38). |
| `bandit -r . -ll` (medium-and-above), `Tests/` excluded | `bandit -r . -c bandit.yaml` — **the whole tree** (37,182 LoC, `Tests/` included) at **every severity**. Current result: **no issues identified**, 0 at every severity. |

How zero is reached now: a documented `bandit.yaml` skip list (each entry
verified to have zero findings outside test code, with a one-line rationale)
plus inline `# nosec BXXX` annotations — bandit reports 10 as actually
suppressing a finding — each with a plain-comment justification above it
(local-dev password defaults, `xml.sax.saxutils` used only for escaping, and
test-only cases). `B101` (`assert`) stays live for non-test code. The "17
medium + 1 high" number was the July 2026 `-ll` picture and no longer
describes the scan.

---

## 3. CI gate set

| The v3 docs say | Current |
|---|---|
| "CI produces test, lint, and security-scan results" (Doc 3 §111) — three things, one job | **Four hard gates across three jobs** (`.github/workflows/tests.yml`): |

1. `test` — full `pytest .` suite (native Postgres/Redis/TLS, no exclusions) **+** `ruff==0.15.22 check .` at zero **+** `bandit -r . -c bandit.yaml` at zero.
2. `deps` — `pip-audit==2.10.1 -r requirements.txt` against the OSV database, one `--ignore-vuln` (PYSEC-2026-1845, justified inline).
3. `secrets` — `gitleaks==8.30.1 dir .` over the working tree, one allowlist entry (justified in `.gitleaks.toml`).

None carry `|| true`. `ruff` unchanged from the v3 docs (still 0.15.22,
still zero findings, still no `ruff`/`pyproject` config by choice).

"First fully green July 24, last five recorded runs green" is a July 2026
statement; leave it as historical. Since 2026-09-03 the four gates above are
an enforced merge requirement.

---

## 4. Mortgage cassette in CI — C-12 / Doc 9 F-2 contradiction, resolved

Doc 6 C-12 and Doc 9's defect table record the mortgage cassette as *"not in
CI yet"* while its tests appear in the suite total — a contradiction the
audit explicitly left *"not independently re-verified."*

**Resolved: it runs in CI.** `Tests/test_mortgage_cassette.py` is 37 tests
(the v3 docs say 27 — also grown), collected by the default `pytest .` and
run by CI's `test` job, which has no exclusions. There is no separate
"mortgage cassette" gate and no exclusion of it.

---

## 5. Twin replica tests in CI

No change from the v3 correction. `test_twin_live.py` (18 tests) has run in
CI since commit `d881bc0` and still does — CI provisions the OS identities
(`scripts/twin_ensure_services.sh`) and a native Postgres for peer auth, and
runs the file with the rest of the suite.

---

## 6. Scope: the IVR/Iceberg application is gone from this repo

Already stated in every doc's banner; repeated here because it is the largest
single change. The standalone simulator, its `Domain/ Sim/ Engines/ Model/
observe/` tree, Twilio ingestion, the Claude governor client, and the
queue/staffing/Bayes layer were extracted to
[GSA-815](https://github.com/wking53214/GSA-815) on 2026-08-28. Directory
maps, module inventories, and `production_harness.py` / `api_server_resilient.py`
references in the v3 docs describe the pre-extraction repo.

---

## 7. New since the snapshot, not covered by the v3 docs at all

- **The mandatory conservation boundary.** `governance_harness._write_decision`
  routes every governed decision through `conservation/boundary.py`, which
  submits the `episode → judgment` transformation to `conservation_kernel`'s
  verifier and fail-closes the ledger write on anything but a clean
  acceptance. Status and the deliberate `authorized_by` boundary:
  `sentinel_os/conservation/CONFORMANCE.md`.
- **`SECURITY.md`** is now a real disclosure policy pointing at
  `AUDIT_PLAYBOOK.md`'s operator-trust boundary and twin-not-yet-accepted
  caveat (PR #42).
- **Ledger operations runbook** — backup/restore drill, integrity-incident
  procedure, `scripts/verify_ledger.py` (PR #43).

---

## What this addendum does *not* touch

- The ~324 `FACT` lines about the `68cadfb` architecture. Most are core
  invariants (append-only ledger, no runtime UPDATE/DELETE, code-hash
  binding, advisory-only verdict, three-of-six dimension coverage) that
  still hold, but they have **not** been re-verified line by line here.
- The structural concerns in Doc 6 that remain true: single-maintainer
  construction with no documented code review or separation of duties
  (C-10), the short documented history (C-11), and — the big one — **nothing
  has touched real production data** (C-13). This addendum corrects numbers,
  not the maturity picture.
- `docs/architecture/*_v1.md` and other bannered `*_v1` documents. Same
  posture: dated, anchored, historical.

_Anchored at `main` `4d99118`, 2026-09-03. If you are reading this well after
that date, treat this addendum the same way it treats the documents it
corrects, and check the [repository root README](../../README.md)._
