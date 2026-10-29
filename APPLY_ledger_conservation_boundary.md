# Apply document: the conservation boundary at the ledger

Delivered on branch `ledger/enforce-conservation-boundary` of
`wking53214/sentinel_os`, one commit per numbered step (1 to 4), on top of
`main` at `b5d8e78`. Take "this change" throughout as the branch.

---

## 1. What changed, in plain words

The conservation check (`verify_governed_decision`: does the judgment follow
from the observed episode?) used to run only inside the governance harness.
Anything that wrote to the ledger directly skipped it. Now the ledger itself
runs the check before it writes a row, so there is no way to get a decision
into the chain without it, short of one named development switch.

- `append_decision` takes `episode=`. With an episode, it is verified first.
  A rejection raises `ConservationBoundaryRejected`; a failure of the check
  itself raises `RuntimeError` ("decision not persisted"). Neither writes.
- With no episode, the write is refused (`ValueError`, naming the way out).
- `governance_harness._write_decision` passes its episode through. It still
  verifies first, so on that path the check runs twice (about 4 ms).

## 2. The one exception

`ICEBERG_LEDGER_CONSERVATION_DEV_OVERRIDE` (truthy: `1`, `true`, `yes`, `on`)
permits a MISSING episode and warns once on stderr. It never waives a supplied
episode. It mirrors `ICEBERG_LEDGER_ATTESTATION_DEV_OVERRIDE`. It must not be
set in a production profile. This is a flagged assumption: the suite sets it
in `conftest.py` because most tests append hand-built records.

## 3. Steps

1. `conftest.py`: the suite runs with the override on (no effect until Step 2).
2. `ledger_postgres.py`, `governance_harness.py`: enforcement at the ledger.
3. `Tests/test_ledger_conservation_boundary.py`: 13 tests, each with the
   override turned off for itself.
4. Docs: `COMPLIANCE.md` (entry and Last Updated stamp), `conservation/CONFORMANCE.md`,
   the docstrings in `conservation/__init__.py` and `conservation/boundary.py`,
   `docs/CHANGELOG.md`, and this note.

## 4. Pre-existing tests edited

None. `Tests/test_conservation_boundary.py`'s source-guard test still passes
because the harness keeps its own call.

## 5. Known consequence outside this repository

Callers that write to the ledger without an episode now fail closed, with the
decision not recorded. GSA-815's governed pipeline is one: it builds
`assembly.episode` but does not pass it. A companion change in that repository
passes it.

## 6. What this does not prove

That the episode handed to the ledger is itself truthful. The check proves the
judgment is rooted in the episode, not that the episode matches the world.
