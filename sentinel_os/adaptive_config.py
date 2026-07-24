import json

# Production configuration for adaptive pipeline
CONFIG = {
    "ledger": {
        "storage_dir": "/tmp/iceberg_ledger",  # nosec B108 -- legacy/archived config, only referenced by archive/run_adaptive.py; the live production ledger is PostgreSQL-backed (governance/ledger_postgres.py), not this local-disk path
        "seed": "815-production",
        "secret": None,  # Set to b"your-secret" for HMAC signing
    },
    "drift": {
        "metric_q": 90.0,
        "rel_threshold": 0.40,
        "min_samples": 20,
    },
    "self_heal": {
        "kind": "expected_wait",
        # Heal band is NOT configured here -- it comes from the governing
        # cassette (expected_wait_bounds). A band literal in this file
        # would be a second source of truth competing with the cassette.
    },
    "data": {
        "baseline_seed": 815,
        "baseline_n_calls": 200,
        "current_seed": 816,
        "current_n_calls": 200,
    },
}

if __name__ == "__main__":
    print(json.dumps(CONFIG, indent=2))
