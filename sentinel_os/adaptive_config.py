import json

# Production configuration for adaptive pipeline
CONFIG = {
    "ledger": {
        "storage_dir": "/tmp/iceberg_ledger",
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
        "band_lo": 4.0,
        "band_hi": 120.0,
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
