"""
Cassette Schema -- the typed, versioned configuration contract.

Every governance parameter the running system reads MUST be declared
here-shaped: named, typed, bounded (min/max), documented, and carrying
the forensic metadata slots (approval_date, justification,
last_reviewed). The cassette is the single source of truth; the engine
reads, it never decides.

Validation is fail-loud and total: an invalid cassette raises
CassetteValidationError carrying EVERY violation found, not just the
first. There are deliberately no fallback defaults anywhere in this
module -- a missing parameter is a halt, not a guess, because a value
the cassette never declared is a value no auditor can trace.

Schema versioning: SCHEMA_VERSION identifies the shape of the
declaration itself and rides inside every ledger policy snapshot, so a
decision recorded today can still be interpreted after the schema
evolves.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

SCHEMA_VERSION = "1.0.0"

# Forensic metadata slots. Item #3 scope: the slots must EXIST on every
# parameter (keys present); enforcement of their contents (approval
# aging, justification quality) is Item #8+ scope.
METADATA_SLOTS = ("approval_date", "justification", "last_reviewed")

ALLOWED_TYPES = ("float", "int", "range")

# Parameters the governance path cannot run without. A cassette may
# declare more; it may not declare fewer.
REQUIRED_GOVERNANCE_PARAMETERS: Dict[str, str] = {
    # A wait longer than this, at any node, is one friction event.
    "long_wait_threshold": "float",
    # friction_count >= this value routes the call to the governor
    # (inclusive semantics -- the cassette declares the line, the
    # engine stands on it).
    "governance_trigger": "int",
    # Self-healing clamp band for the expected_wait parameter.
    "expected_wait_bounds": "range",
}


class CassetteValidationError(Exception):
    """A cassette failed schema validation. Carries the full violation
    list so one load attempt reports every problem at once."""

    def __init__(self, cassette_label: str, violations: List[str]):
        self.cassette_label = cassette_label
        self.violations = list(violations)
        lines = "\n".join(f"  - {v}" for v in self.violations)
        super().__init__(
            f"Cassette '{cassette_label}' failed schema validation "
            f"({len(self.violations)} violation(s)):\n{lines}"
        )


@dataclass(frozen=True)
class ParameterSpec:
    """One validated governance parameter, exactly as declared."""

    name: str
    value: Any
    type: str
    min_value: float
    max_value: float
    unit: str
    description: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_snapshot(self) -> Dict[str, Any]:
        """JSON-safe form for the ledger's policy snapshot."""
        return {
            "value": list(self.value) if self.type == "range" else self.value,
            "type": self.type,
            "min": self.min_value,
            "max": self.max_value,
            "unit": self.unit,
            "description": self.description,
            "metadata": dict(self.metadata),
        }


class GovernanceParameters:
    """The validated, typed view of a cassette's governance declaration.

    This is what the engine reads at decision time. Accessors are
    type-strict: asking for a float where the cassette declared an int
    is a contract violation and raises, because a silent coercion is a
    second place the value's meaning could live.
    """

    def __init__(self, cassette_version: str, parameters: Dict[str, ParameterSpec]):
        self.cassette_version = cassette_version
        self._parameters = dict(parameters)

    def names(self) -> List[str]:
        return sorted(self._parameters)

    def _get(self, name: str, expected_type: str) -> ParameterSpec:
        if name not in self._parameters:
            raise KeyError(
                f"Cassette '{self.cassette_version}' declares no parameter "
                f"'{name}'; declared: {self.names()}"
            )
        spec = self._parameters[name]
        if spec.type != expected_type:
            raise TypeError(
                f"Parameter '{name}' is declared '{spec.type}', "
                f"accessed as '{expected_type}'"
            )
        return spec

    def float_value(self, name: str) -> float:
        return float(self._get(name, "float").value)

    def int_value(self, name: str) -> int:
        return int(self._get(name, "int").value)

    def range_value(self, name: str) -> Tuple[float, float]:
        lo, hi = self._get(name, "range").value
        return (float(lo), float(hi))

    def snapshot(self) -> Dict[str, Any]:
        """The full policy snapshot a ledger decision record carries:
        every parameter, with bounds and metadata, plus the schema and
        cassette versions that make the record self-describing."""
        return {
            "schema_version": SCHEMA_VERSION,
            "cassette_version": self.cassette_version,
            "parameters": {
                name: spec.as_snapshot()
                for name, spec in sorted(self._parameters.items())
            },
        }


def cassette_version_of(cassette) -> str:
    """Canonical identity string a ledger row uses to name the policy
    that governed it: domain:name:version."""
    config = cassette.get_config()
    return f"{config.domain}:{config.name}:{config.version}"


def _validate_one_parameter(name: str, raw: Any, violations: List[str]) -> ParameterSpec:
    if not isinstance(raw, dict):
        violations.append(f"parameter '{name}': declaration must be a dict, got {type(raw).__name__}")
        return None

    for required_key in ("value", "type", "min", "max", "unit", "description", "metadata"):
        if required_key not in raw:
            violations.append(f"parameter '{name}': missing required field '{required_key}'")
    if violations and any(f"parameter '{name}': missing required field" in v for v in violations):
        return None

    ptype = raw["type"]
    if ptype not in ALLOWED_TYPES:
        violations.append(f"parameter '{name}': type '{ptype}' not in {ALLOWED_TYPES}")
        return None

    value = raw["value"]
    min_v, max_v = raw["min"], raw["max"]
    ok = True

    if not isinstance(min_v, (int, float)) or not isinstance(max_v, (int, float)):
        violations.append(f"parameter '{name}': min/max must be numeric")
        ok = False
    elif min_v > max_v:
        violations.append(f"parameter '{name}': min {min_v} > max {max_v} (contradictory bounds)")
        ok = False

    if ptype == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            violations.append(f"parameter '{name}': declared float, value is {type(value).__name__}")
            ok = False
        elif ok and not (min_v <= float(value) <= max_v):
            violations.append(
                f"parameter '{name}': value {value} outside declared range [{min_v}, {max_v}]"
            )
    elif ptype == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            violations.append(f"parameter '{name}': declared int, value is {type(value).__name__}")
            ok = False
        elif ok and not (min_v <= value <= max_v):
            violations.append(
                f"parameter '{name}': value {value} outside declared range [{min_v}, {max_v}]"
            )
    elif ptype == "range":
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)):
            violations.append(f"parameter '{name}': declared range, value must be [lo, hi] numerics")
            ok = False
        else:
            lo, hi = float(value[0]), float(value[1])
            if lo >= hi:
                violations.append(
                    f"parameter '{name}': range lo {lo} >= hi {hi} (contradictory range)"
                )
            if ok and isinstance(min_v, (int, float)) and isinstance(max_v, (int, float)):
                if not (min_v <= lo and hi <= max_v):
                    violations.append(
                        f"parameter '{name}': range [{lo}, {hi}] outside declared "
                        f"bounds [{min_v}, {max_v}]"
                    )

    metadata = raw.get("metadata")
    if not isinstance(metadata, dict):
        violations.append(f"parameter '{name}': metadata must be a dict with slots {METADATA_SLOTS}")
    else:
        for slot in METADATA_SLOTS:
            if slot not in metadata:
                violations.append(f"parameter '{name}': missing metadata slot '{slot}'")

    if not isinstance(raw.get("description"), str) or not raw.get("description", "").strip():
        violations.append(f"parameter '{name}': description must be a non-empty string")

    return ParameterSpec(
        name=name,
        value=tuple(value) if ptype == "range" and isinstance(value, (list, tuple)) else value,
        type=ptype,
        min_value=float(min_v) if isinstance(min_v, (int, float)) else float("nan"),
        max_value=float(max_v) if isinstance(max_v, (int, float)) else float("nan"),
        unit=str(raw.get("unit", "")),
        description=str(raw.get("description", "")),
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


def validate_governance_parameters(cassette) -> GovernanceParameters:
    """Validate a cassette's governance declaration against the schema.

    Raises CassetteValidationError with the complete violation list, or
    returns the typed GovernanceParameters view. Nothing is defaulted,
    nothing is repaired: the cassette is either the source of truth or
    it does not load.
    """
    label = cassette_version_of(cassette)
    violations: List[str] = []

    getter = getattr(cassette, "get_governance_parameters", None)
    if not callable(getter):
        raise CassetteValidationError(
            label, ["cassette does not implement get_governance_parameters()"]
        )

    raw_params = getter()
    if not isinstance(raw_params, dict):
        raise CassetteValidationError(
            label, [f"get_governance_parameters() must return a dict, got {type(raw_params).__name__}"]
        )

    specs: Dict[str, ParameterSpec] = {}

    for name, expected_type in REQUIRED_GOVERNANCE_PARAMETERS.items():
        if name not in raw_params:
            violations.append(f"missing required governance parameter '{name}' ({expected_type})")
            continue
        declared_type = raw_params[name].get("type") if isinstance(raw_params[name], dict) else None
        if declared_type != expected_type:
            violations.append(
                f"parameter '{name}': must be declared type '{expected_type}', got '{declared_type}'"
            )

    for name, raw in sorted(raw_params.items()):
        spec = _validate_one_parameter(name, copy.deepcopy(raw), violations)
        if spec is not None:
            specs[name] = spec

    # Cross-parameter contradiction checks (only meaningful when the
    # individual parameters parsed cleanly).
    if "long_wait_threshold" in specs and specs["long_wait_threshold"].type == "float":
        if float(specs["long_wait_threshold"].value) <= 0:
            violations.append("parameter 'long_wait_threshold': must be > 0 seconds")
    if "governance_trigger" in specs and specs["governance_trigger"].type == "int":
        if int(specs["governance_trigger"].value) < 0:
            violations.append("parameter 'governance_trigger': must be >= 0 friction events")

    if violations:
        raise CassetteValidationError(label, violations)

    return GovernanceParameters(label, specs)


def validate_cassette(cassette) -> GovernanceParameters:
    """The single fail-loud validation entry point for EVERY load path:
    loader, registry, harness construction, and direct injection alike.

    Structural checks first (config present, queues non-empty, the
    cassette's own self-check), then the full governance-parameter
    schema. Returns the typed parameters so callers validate and read
    in one step.
    """
    label = cassette_version_of(cassette)
    violations: List[str] = []

    config = cassette.get_config()
    if config is None:
        violations.append("get_config() returned None")
    queues = cassette.get_queue_definitions()
    if not queues:
        violations.append("get_queue_definitions() returned no queues")
    try:
        if cassette.validate() is not True:
            violations.append("cassette.validate() self-check did not return True")
    except Exception as exc:  # a self-check that crashes is a failed self-check
        violations.append(f"cassette.validate() raised: {exc}")

    if violations:
        raise CassetteValidationError(label, violations)

    return validate_governance_parameters(cassette)
