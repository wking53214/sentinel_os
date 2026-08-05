"""
Claude Governance API - Real LLM decisions for Iceberg

Routes critical governance decisions to Claude instead of simulation

COST (2026-07-31): every method that reaches the API returns a `cost` key
(real usage-derived token counts + dollar amount, see ai_cost_tracking.py)
alongside its decision -- computing the cost is this module's job, since
only it ever sees the raw API response; DISCLOSING it to the ledger is the
caller's, since only the caller (production_harness.py) holds a ledger
reference. See governance/ledger_postgres.py's record_ai_governance_cost.
"""

import anthropic
from typing import Dict, Optional
import json

from governor_injection_defense import build_governance_call
from ai_cost_tracking import cost_of_call


def _cost_or_none(model_identity: Optional[str], usage) -> Optional[Dict]:
    """Build the cost dict for one call, or None if no usage data exists.

    No usage data happens when the API call itself never completed (a
    genuine transport/auth error raised before `message` was assigned) or
    when a test stub's fake response has no `usage` attribute at all --
    both are "we don't know what this cost" and get the same None, never
    a guessed or zeroed cost.
    """
    if model_identity is None or usage is None:
        return None
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if input_tokens is None or output_tokens is None:
        return None
    return cost_of_call(model_identity, input_tokens, output_tokens).as_dict()

class ClaudeGovernanceDecider:
    """Uses real Claude API for governance decisions"""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize Claude client.

        The client is only constructed when an API key is actually
        provided -- constructing it unconditionally made the decider
        impossible to build in any environment without a key (every
        harness test, every offline run).
        """
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None
        self.model = "claude-opus-4-6"
        self.decisions = []

    def decide_healing_bounds(self, queue_name: str, current_wait: float,
                             baseline_wait: float, drift_magnitude: float) -> Dict:
        """Ask Claude: should we heal this queue? Fail-closed on any error."""
        
        if self.client is None:
            return {
                "should_heal": False,
                "governed": False,
                "parse_failed": False,
                "reasoning": "No API client configured",
                "lo_bound": None,
                "hi_bound": None,
                "target_wait": None,
                "confidence": 0.0,
                "model_identity": None,
                "cost": None,
            }

        system, messages = build_governance_call(
            system_instruction=(
                "You are an IVR governance expert. A call queue has experienced "
                "drift, described in the untrusted data block. Decide whether to "
                "self-heal."
            ),
            caller_fields={
                "queue": queue_name,
                "current_wait_seconds": f"{current_wait:.1f}",
                "baseline_wait_seconds": f"{baseline_wait:.1f}",
                "drift_magnitude_percent": f"{drift_magnitude*100:.1f}",
            },
            task_and_format=(
                'Respond ONLY with valid JSON: {"should_heal": true/false, '
                '"reasoning": "...", "lo_bound": ..., "hi_bound": ..., '
                '"target_wait": ..., "confidence": 0.0-1.0}'
            ),
        )

        # Set before the try so a genuine transport error (raised by
        # messages.create itself, before either name would otherwise be
        # bound) still leaves both names defined for the except blocks --
        # None correctly means "no usage data exists" either way.
        usage = None
        model_identity = None
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=system,
                messages=messages,
            )
            usage = getattr(message, "usage", None)
            model_identity = getattr(message, "model", None) or self.model
            if not message.content or len(message.content) == 0:
                raise ValueError("Empty response")
            response_text = message.content[0].text
            decision = json.loads(response_text)
            if not isinstance(decision.get("should_heal"), bool):
                raise ValueError("should_heal not bool")
        except json.JSONDecodeError:
            return {
                "should_heal": False,
                "governed": False,
                "parse_failed": True,
                "reasoning": "Governor response not valid JSON",
                "lo_bound": None,
                "hi_bound": None,
                "target_wait": None,
                "confidence": 0.0,
                "model_identity": None,
                "cost": _cost_or_none(model_identity, usage),
            }
        except Exception as e:
            return {
                "should_heal": False,
                "governed": False,
                "parse_failed": True,
                "reasoning": f"Governor call failed: {str(e)}",
                "lo_bound": None,
                "hi_bound": None,
                "target_wait": None,
                "confidence": 0.0,
                "model_identity": None,
                "cost": _cost_or_none(model_identity, usage),
            }
        
        decision["parse_failed"] = False
        decision["model_identity"] = model_identity
        decision["cost"] = _cost_or_none(model_identity, usage)
        self.decisions.append(decision)
        return decision

    def decide_staffing_adjustment(self, queue_name: str, current_agents: int,
                                  current_wait: float, target_wait: float,
                                  abandonment_rate: float) -> Dict:
        """Ask Claude: how many agents should we staff? Fail-closed on any error.

        Finding-2 fix: this path previously had NO client-None guard and NO
        try/except around the API call, so in any environment without a key it
        raised AttributeError on None.messages and propagated -- a governor path
        that did not fail closed, violating the system invariant. It now returns
        a conservative fail-closed dict (no staffing increase authorized) on
        every error, matching the other governor methods.
        """
        if self.client is None:
            return {
                "recommended_agents": None,
                "queue": queue_name,
                "governed": False,
                "parse_failed": False,
                "reasoning": "No API client configured",
                "expected_wait": None,
                "confidence": 0.0,
                "model_identity": None,
                "cost": None,
            }

        system, messages = build_governance_call(
            system_instruction=(
                "You are a contact center workforce manager. A queue needs a "
                "staffing adjustment, described in the untrusted data block. "
                "Apply Erlang C principles."
            ),
            caller_fields={
                "queue": queue_name,
                "current_agents": current_agents,
                "current_wait_seconds": f"{current_wait:.1f}",
                "target_wait_seconds": f"{target_wait:.1f}",
                "abandonment_rate_percent": f"{abandonment_rate*100:.1f}",
            },
            task_and_format=(
                'Respond ONLY with valid JSON: {"recommended_agents": integer, '
                '"reasoning": "brief explanation", "expected_wait": '
                'estimated_wait_in_seconds, "confidence": 0.0-1.0}'
            ),
        )

        usage = None
        model_identity = None
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=system,
                messages=messages,
            )
            usage = getattr(message, "usage", None)
            model_identity = getattr(message, "model", None) or self.model
            if not message.content or len(message.content) == 0:
                raise ValueError("Empty response")
            response_text = message.content[0].text
            decision = json.loads(response_text)
        except json.JSONDecodeError:
            return {
                "recommended_agents": None,
                "queue": queue_name,
                "governed": False,
                "parse_failed": True,
                "reasoning": "Governor response not valid JSON",
                "expected_wait": None,
                "confidence": 0.0,
                "model_identity": None,
                "cost": _cost_or_none(model_identity, usage),
            }
        except Exception as e:
            return {
                "recommended_agents": None,
                "queue": queue_name,
                "governed": False,
                "parse_failed": True,
                "reasoning": f"transport_error: Governor call failed: {str(e)}",
                "expected_wait": None,
                "confidence": 0.0,
                "model_identity": None,
                "cost": _cost_or_none(model_identity, usage),
            }

        decision["queue"] = queue_name
        decision["parse_failed"] = False
        decision["model_identity"] = model_identity
        decision["cost"] = _cost_or_none(model_identity, usage)
        self.decisions.append(decision)
        return decision
    
    def decide_queue_reordering(self, current_order: list, success_rates: Dict,
                               caller_distribution: Dict) -> Dict:
        """Ask Claude: how should we reorder the queue menu? Fail-closed on error.

        Finding-2 fix: like decide_staffing_adjustment, this path had no
        client-None guard and no try/except and would raise on a missing key.
        It now fails closed. The previous parse-failure fallback silently
        emitted a success-rate-sorted order as if it were a governed decision;
        that is replaced with an explicit ungoverned/no-change result, because
        a reorder the governor never actually approved must not be presented as
        governed output.
        """
        if self.client is None:
            return {
                "proposed_order": None,
                "governed": False,
                "parse_failed": False,
                "reasoning": "No API client configured",
                "expected_impact": 0.0,
                "confidence": 0.0,
                "model_identity": None,
                "cost": None,
            }

        system, messages = build_governance_call(
            system_instruction=(
                "You are an IVR menu design expert. Current queue ordering and "
                "performance are in the untrusted data block. Recommend an "
                "ordering that maximizes resolution and minimizes abandonment."
            ),
            caller_fields={
                "current_order": json.dumps(current_order),
                "success_rates_by_queue": json.dumps(success_rates),
                "caller_distribution": json.dumps(caller_distribution),
            },
            task_and_format=(
                'Respond ONLY with valid JSON: {"proposed_order": '
                '["queue1", "queue2", ...], "reasoning": "...", '
                '"expected_impact": 0.0-1.0, "confidence": 0.0-1.0}'
            ),
        )

        usage = None
        model_identity = None
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                system=system,
                messages=messages,
            )
            usage = getattr(message, "usage", None)
            model_identity = getattr(message, "model", None) or self.model
            if not message.content or len(message.content) == 0:
                raise ValueError("Empty response")
            response_text = message.content[0].text
            decision = json.loads(response_text)
        except json.JSONDecodeError:
            return {
                "proposed_order": None,
                "governed": False,
                "parse_failed": True,
                "reasoning": "Governor response not valid JSON",
                "expected_impact": 0.0,
                "confidence": 0.0,
                "model_identity": None,
                "cost": _cost_or_none(model_identity, usage),
            }
        except Exception as e:
            return {
                "proposed_order": None,
                "governed": False,
                "parse_failed": True,
                "reasoning": f"transport_error: Governor call failed: {str(e)}",
                "expected_impact": 0.0,
                "confidence": 0.0,
                "model_identity": None,
                "cost": _cost_or_none(model_identity, usage),
            }

        decision["parse_failed"] = False
        decision["model_identity"] = model_identity
        decision["cost"] = _cost_or_none(model_identity, usage)
        self.decisions.append(decision)
        return decision
    
    def safety_check(self, action: str, details: Dict) -> Dict:
        """Ask Claude: is this governance action safe? Fail-closed on any error.

        Item 4: caller data (`action`, `details`) is delivered as an escaped,
        XML-delimited untrusted-data block with the instruction in the `system`
        role, so a hostile queue name or detail value cannot be read as an
        instruction to the governor.

        Item 5: the model string the API actually resolved to (response.model)
        is returned as `model_identity` so the caller can put it in the ledger
        row and thus in the hash. On every fail-closed path model_identity is
        None -- a decision that didn't come from a model has no model identity,
        and inventing one would be a forged fact in a tamper-evident record.

        Item 6 (2026-07-31): every path that actually reached the API --
        including a parsed-but-invalid response -- also returns `cost`, real
        token counts plus a computed dollar amount from ai_cost_tracking (None
        if the model isn't in that module's pricing table; never guessed).
        `cost` is None only when no API call ever completed, same posture as
        `model_identity`.
        """

        if self.client is None:
            return {
                "safe": False,
                "governed": False,
                "parse_failed": False,
                "risk_level": "critical",
                "reasoning": "No API client configured",
                "recommendations": ["Configure API key"],
                "confidence": 1.0,
                "model_identity": None,
                "cost": None,
            }

        system, messages = build_governance_call(
            system_instruction=(
                "You are an AI safety auditor for IVR systems. Evaluate the "
                "governance action described in the untrusted data block."
            ),
            caller_fields={"action": action, "details": json.dumps(details)},
            task_and_format=(
                'Respond ONLY with valid JSON: {"safe": true/false, '
                '"risk_level": "low"/"medium"/"high", "reasoning": "...", '
                '"recommendations": [], "confidence": 0.0-1.0}'
            ),
        )

        usage = None
        model_identity = None
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                system=system,
                messages=messages,
            )
            usage = getattr(message, "usage", None)
            if not message.content or len(message.content) == 0:
                raise ValueError("Empty response")
            response_text = message.content[0].text
            # response.model is the ground truth (what actually served the
            # call), which can differ from self.model under aliasing.
            model_identity = getattr(message, "model", None) or self.model
            decision = json.loads(response_text)
            if not isinstance(decision.get("safe"), bool):
                raise ValueError(f"'safe' not bool: {type(decision.get('safe'))}")
        except json.JSONDecodeError:
            return {
                "safe": False,
                "governed": False,
                "parse_failed": True,
                "risk_level": "critical",
                "reasoning": "Governor response not valid JSON",
                "recommendations": ["Check governor output"],
                "confidence": 0.0,
                "model_identity": None,
                "cost": _cost_or_none(model_identity, usage),
            }
        except ValueError as e:
            return {
                "safe": False,
                "governed": False,
                "parse_failed": True,
                "risk_level": "critical",
                "reasoning": str(e),
                "recommendations": ["Check governor"],
                "confidence": 0.0,
                "model_identity": None,
                "cost": _cost_or_none(model_identity, usage),
            }
        except Exception as e:
            return {
                "safe": False,
                "governed": False,
                "parse_failed": True,
                "risk_level": "critical",
                "reasoning": f"transport_error: Governor call failed: {str(e)}",
                "recommendations": ["Check API connectivity"],
                "confidence": 0.0,
                "model_identity": None,
                "cost": _cost_or_none(model_identity, usage),
            }

        decision["governed"] = decision.get("safe", False)
        decision["parse_failed"] = False
        decision["model_identity"] = model_identity
        decision["cost"] = _cost_or_none(model_identity, usage)
        self.decisions.append(decision)
        return decision

    def get_decision_log(self) -> list:
        """Get all decisions made by Claude"""
        return self.decisions
