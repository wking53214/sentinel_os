import os, random

from sentinel_os.governance.log_rotation_v1 import LogRotationManager
from sentinel_os.governance.ledger_postgres import LocalDiskAdapter

from sentinel_os.governance.drift_core_v1 import (
    DriftPolicy, detect_drift, baseline_from_holds
)

from sentinel_os.governance.self_heal_v1 import (
    heal, HealBand, InMemoryParameterStore
)

os.makedirs("tmp_ledger", exist_ok=True)

ledger = LogRotationManager(
    LocalDiskAdapter("tmp_ledger"),
    seed=123
)

policy = DriftPolicy()

baseline = baseline_from_holds(
    {"auth": [random.uniform(10, 20) for _ in range(50)]},
    policy
)

current = {
    "auth": [random.uniform(30, 60) for _ in range(50)]
}

signals = detect_drift(baseline, current, policy)
breached = [s for s in signals if s.breached]

store = InMemoryParameterStore()
band = HealBand(0.1, 0.9)

records = heal(
    breached,
    store,
    band,
    ledger,
    kind="expected_wait"
)

print("signals:", len(signals))
print("breaches:", len(breached))
print("heals:", len(records))
# python3 test_pipeline.py

