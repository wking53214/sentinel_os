"""
Claude Governance API - Real LLM decisions for Iceberg

Routes critical governance decisions to Claude instead of simulation
"""

import anthropic
from typing import Dict, Optional
import json

from governor_injection_defense import build_governance_call

class ClaudeGovernanceDecider:
    """Uses real Claude API for governance decisions"""
    
    def __init__(self, api_key: Optional[str] = None, governance_params=None):
        """Initialize Claude client.

        The client is only constructed when an API key is actually
        provided -- constructing it unconditionally made the decider
        impossible to build in any environment without a key (every
        harness test, every offline run). governance_params, when
        supplied, is the validated GovernanceParameters view so fallback
        bounds come from the cassette instead of literals baked in here.
        """
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None
        self.model = "claude-opus-4-6"
        self.governance_params = governance_params
        self.decisions = []

    def _fallback_bounds(self):
        """Healing bounds for the JSON-parse fallback path, sourced from
        the cassette when available. No cassette wired in -> no invented
        numbers: the fields are omitted rather than defaulted."""
        if self.governance_params is None:
            return None, None
        try:
            lo, hi = self.governance_params.range_value("expected_wait_bounds")
            return lo, hi
        except (KeyError, TypeError):
            return None, None
    
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

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=system,
                messages=messages,
            )
            if not message.content or len(message.content) == 0:
                raise ValueError("Empty response")
            response_text = message.content[0].text
            model_identity = getattr(message, "model", None) or self.model
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
            }
        
        decision["parse_failed"] = False
        decision["model_identity"] = model_identity
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

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=system,
                messages=messages,
            )
            if not message.content or len(message.content) == 0:
                raise ValueError("Empty response")
            response_text = message.content[0].text
            model_identity = getattr(message, "model", None) or self.model
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
            }

        decision["queue"] = queue_name
        decision["parse_failed"] = False
        decision["model_identity"] = model_identity
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

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                system=system,
                messages=messages,
            )
            if not message.content or len(message.content) == 0:
                raise ValueError("Empty response")
            response_text = message.content[0].text
            model_identity = getattr(message, "model", None) or self.model
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
            }

        decision["parse_failed"] = False
        decision["model_identity"] = model_identity
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

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                system=system,
                messages=messages,
            )
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
            }

        decision["governed"] = decision.get("safe", False)
        decision["parse_failed"] = False
        decision["model_identity"] = model_identity
        self.decisions.append(decision)
        return decision

    def get_decision_log(self) -> list:
        """Get all decisions made by Claude"""
        return self.decisions
