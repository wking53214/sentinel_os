"""
Claude Governance API -- the IVR/Iceberg half of the governor.

Domain half of the 2026-08-05 governor split. The domain-blind half --
client construction, the fail-closed safety gate, the decision log and
cost accounting -- now lives in the kernel repo as
`governance_decider.GovernanceDecider`. What is left here is the part
that is genuinely about call centers: three decision methods that take
queue names, agent counts, wait times and abandonment rates.

Subclassing rather than copying follows this repo's stated policy (see
DEPENDENCIES.md): one copy of the kernel, not two that can quietly
drift apart. `__init__`, `safety_check` and `get_decision_log` are
inherited. Do NOT override safety_check or its fail-closed behaviour
here -- that contract is the reason the base class is in the kernel.

COST (2026-07-31): every method that reaches the API returns a `cost` key
(real usage-derived token counts + dollar amount, see ai_cost_tracking.py
in the kernel repo) alongside its decision -- computing the cost is the
governor's job, since only it ever sees the raw API response; DISCLOSING
it to the ledger is the caller's, since only the caller
(production_harness.py) holds a ledger reference. See
governance/ledger_postgres.py's record_ai_governance_cost.
"""

import json
from typing import Dict

from governance_decider import GovernanceDecider, _cost_or_none
from governor_injection_defense import build_governance_call


class ClaudeGovernanceDecider(GovernanceDecider):
    """IVR/queue governance decisions, on top of the kernel's fail-closed gate."""

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
