# Delivery record: the conservation gateway's root-guard opt-out is explicit

Branch: ccr-4ec84ed9-qqmhh2

## Steps

1. Step 1: `ConservationGateway` takes `allow_unguarded_roots` (default False). With no kernel passed and the flag unset it raises `InvalidContract`; a kernel passed in keeps the guard it was built with, and the flag together with a kernel is refused. `conservation/boundary.py` passes the flag on the path with no observed events, which is the path that always registered the root unguarded. Four new tests in `Tests/test_gateway_unguarded_roots_explicit.py`, each shown failing before the change.
2. Step 2: this record.

## Pre-existing tests edited

Setup only. No assertion changed.

- `Tests/test_conservation_boundary.py` (Step 1): two constructions, `ConservationGateway(registry=...)` at the spoofed-authority test and at `test_unrooted_judgment_is_rejected`, now pass `allow_unguarded_roots=True`. They exercised the unguarded default and still do, now by saying so.

## Not changed

- Behaviour for any caller that already passed a kernel, and for the guarded path (`verify_governed_decision` with observed events).
- The pin to Conservation_Kernel `7b08c79`. The Kernel's current `main` also refuses a kernel with no evidence guard unless `allow_unguarded_evidence=True` is passed, and this gateway does not pass it. Moving the pin will need that decided on its own.
- The unguarded root on the no-events path is still unguarded: nothing independent can vouch for an Episode without observed events. This change makes that visible at the call site; it does not close it.

## What was and was not run

- Affected tests against Conservation_Kernel `7b08c79` (the pin), CNS on the path: the same 9 tests fail before and after, every one needing a PostgreSQL server at localhost:5432, which was not available. The database-backed `_write_decision` path was therefore not exercised.
- `ruff check --select E4,E7,E9,F .` and `bandit -c sentinel_os/bandit.yaml -r sentinel_os/conservation` clean.
