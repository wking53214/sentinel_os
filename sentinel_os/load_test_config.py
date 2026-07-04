# Load test configuration
LOAD_TEST_CONFIG = {
    "small": {
        "baseline_n_calls": 200,
        "current_n_calls": 200,
        "description": "Small (400 total)",
    },
    "medium": {
        "baseline_n_calls": 500,
        "current_n_calls": 500,
        "description": "Medium (1000 total)",
    },
    "large": {
        "baseline_n_calls": 1000,
        "current_n_calls": 1000,
        "description": "Large (2000 total)",
    },
    "xlarge": {
        "baseline_n_calls": 2500,
        "current_n_calls": 2500,
        "description": "XLarge (5000 total)",
    },
}
