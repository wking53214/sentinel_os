# The Twin: re-run addendum (Nov 13, 2026)

> **Dated report, not maintained.** It records one run on one machine on the date below. Read it beside `twin_verification_report_v1.md` (July 18, 2026), which describes what the twin is and what each test proves.

**Run by:** the maintainer, on his Chromebook (Debian container), from a fresh clone of `main` on Nov 13, 2026. The commit hash was not captured at run time.
**Environment:** Python 3.11.2, Postgres 15.20, Redis 7.0.15, the three OS accounts `sentinelsvc`, `twincustomer` and `twincustodian`, real HTTP processes, run as root. Dependencies installed from `requirements.txt` into a clean virtual environment (`/opt/twin_venv`), which supplies `cns` v4.0.0 as that file requires.

## Result

Across the three twin test files: 30 tests, 30 passed.

| File | Tests | Result |
|---|---|---|
| `test_twin_custody.py` | 9 | 9 passed (7 on the first run, the other 2 after the ledger was filled, see below) |
| `test_twin_live.py` | 18 | 18 passed |
| `test_twin_snapshot_forgery.py` | 3 | 3 passed |

The July report covered 27 tests (9 custody, 18 live). The snapshot-forgery file is new since then.

## The two first-run failures

The first full run finished 28 passed, 2 failed (99 s). The two failures were `test_recompute_matches_every_live_ledger_row` and `test_recompute_catches_field_edit`. Both read real governance rows from the `iceberg` database's `ledger_entries` table, which did not exist yet on this machine, so there was nothing to read.

The ledger was then filled by running `Tests/test_governance_harness.py` (17 passed). That left 53 rows in `ledger_entries`. `test_twin_custody.py` was re-run alone: 9 of 9 passed.

Read this plainly: the two tests depend on a ledger populated by other suites. Run alone on an empty database they fail. They are not self-contained. A full-repo run, as CI does, fills the ledger first. The failures were a missing precondition, not a twin defect, and no test was edited.

## What this does and does not show

- It refreshes the July evidence for the twin's custody, live pipeline, divergence detection, transport and custody-model D claims on a second machine and a newer repo state.
- It does not re-run the claims v1 listed as not proven (for example retention pruning at scale).
- The two ledger-dependent tests were passed against rows written moments earlier by the same machine's own test suite, not against a long-lived production ledger.

Evidence: first-run log `~/twin_rerun_2026-11-13.log`, second-run log `~/twin_rerun_2026-11-13_step5.log`, both on the maintainer's Chromebook (not in the repo).
