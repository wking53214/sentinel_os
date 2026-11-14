# Delivery record: worker and API choose the cassette by configuration

Branch `refactor/cassette-by-config`. Goal: the engine's front door (worker and ingress) no longer assumes one industry. Default behaviour is unchanged.

## Steps
1. `sentinel_worker.py`: payload-to-episode mapping renamed `_payload_to_episode`, typed on the `Cassette` base. The thin-reason bar is read from the cassette only if it declares one. The old name stays as an alias.
2. `api_server_v2.py`: `DecisionSubmission` and `POST /submit-decision`. `MortgageDecisionSubmission` and `/submit-mortgage-decision` remain as aliases.
3. `sentinel_worker.py`, `regulatory_cassettes/__init__.py`, `cassettes/mortgage_cassette.py`: `SENTINEL_CASSETTE` picks the cassette (default `mortgage`); `SENTINEL_REGULATORY_LENS` picks the lens or `none`, else the cassette's `DEFAULT_REGULATORY_LENS` applies. Lenses are listed in `regulatory_cassettes.LENSES`. The deck's action label is `sentinel_worker:<domain>`, identical to before for mortgage.
4. `COMPLIANCE.md` re-checked and re-stamped; this note.

## Existing tests edited
None.

## Tests added
Two for the neutral route (`Tests/test_api_require_events.py`), four for cassette and lens selection (`Tests/test_sentinel_worker_regulatory_deck_env.py`).

## Not touched
No ledger columns, no hashed fields, no keys. The IVR and banking cassettes are unchanged. The worker has only been exercised with the mortgage cassette end to end; a second cassette on this path has only been loaded, not run through a full decision.
