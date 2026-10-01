# Standing rules for agent runs in this repository

Every execution script for this library carries these rules. A run's script
may add to them; it may not relax them.

1. Work on a branch, never on main. One commit per numbered step, and the
   commit message names the step number. When a step depends on a helper a
   later step introduces, commit the helper first and say so.
2. Minimal diffs. No rewrites. Preserve every existing behaviour not named
   in a step.
3. No new unhashed ledger columns, ever. New hashed data goes either into an
   existing hashed slot (the `output` mapping) or into
   `OPTIONAL_HASHED_FIELDS` in `canonical_fields.py` as a one-line,
   present-when-truthy addition, and nowhere else. Never a second list.
4. Never hard-code a key. Never truncate a hash. Never accept a default key
   in a production profile.
5. Run the affected test suite after every step. If a pre-existing test
   fails for any reason other than the intended change, stop and report.
   Do not edit the test to make it pass.
6. A pre-existing test that asserts the exact behaviour a numbered step
   changes may be updated to assert the new behaviour, and nothing else
   about it may change. Every such edit is listed in the run's status and
   in its delivery record (the `APPLY_*.md` note), with the step that
   caused it.
7. Do not use em dashes in code, comments, docs, or commit messages.
8. At the end of each phase, print a short status: tests run, pass and fail
   counts, commits made, anything skipped and why.

CI runs every test directory (`sentinel_os/` and `tools/`), ruff and bandit
as hard gates. A status document that says a piece of work is complete is a
claim to test, not evidence.
