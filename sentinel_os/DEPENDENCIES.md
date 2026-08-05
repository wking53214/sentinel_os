# External dependencies (not vendored on purpose)

This repo is the IVR/Iceberg side, extracted out of `sentinel_os`. It is not
meant to run standalone. It depends on the following modules from the
`sentinel_os` kernel repo, which are deliberately NOT copied in here --
one copy of the kernel, not two that can quietly drift apart:

- episode
- event_v1
- cassette_interface, cassette_loader, cassette_schema, cassette_capabilities, cassette_forensics
- governance/ (ledger_postgres, etc.)
- governance_decider  (base class of this repo's ClaudeGovernanceDecider)
- governor_injection_defense
- ai_cost_tracking
- queue_schema
- circuit_breaker, operational_resilience, api_key_auth, tracing
- array_ops

Until the kernel repo is packaged as something this repo can install
(pip package, git submodule, or similar), the practical path is running
this repo's code from inside a checkout that also has those files on
PYTHONPATH -- same as it worked inside sentinel_os today.

## Corrections (2026-08-05)

`ai_cost_tracking` was listed as this repo's own file and a copy was
vendored here. It is a kernel module: `governance_decider.safety_check`
in the kernel imports it, so the kernel cannot ship without it. The
vendored copy is byte-identical today and is being removed, because two
copies of a pricing table are two answers to "what did that decision
cost".

`sentinel_core`, `metrics_prometheus` and `grafana_dashboard` were listed
as kernel modules. They are not. All three are explicitly Iceberg/
telephony-shaped (SentinelCore requires the telephony_ingest and
routing_topology capabilities; the metrics and dashboard modules export
queue wait times and abandonment rates), so they leave the kernel with
the rest of the IVR mission and belong to this repo.

STILL OWED TO THIS REPO: copies of those three files. They are imported
by code already here -- `production_harness.py` (metrics_prometheus,
sentinel_core), `api_server.py` and `api_server_resilient.py`
(grafana_dashboard), `Tests/test_bayes_learning_loop.py` (sentinel_core)
-- and none of the three has been copied across yet.

Also dropped entirely, not carried forward: `gallm_coordinator.py` --
zero importers anywhere in the original repo.
