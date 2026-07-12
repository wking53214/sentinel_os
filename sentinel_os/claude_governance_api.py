"""
Claude Governance API - Real LLM decisions for Iceberg

Routes critical governance decisions to Claude instead of simulation.

SAFETY MODEL (fail-closed):
    Every decision path treats an unintelligible or unparseable governor
    response as a REFUSAL, not an approval. A parse failure, a missing
    required field, or a malformed API response block yields a decision
    flagged governed=False / parse_failed=True with the unsafe default
    (safe=False, should_heal=False, no fabricated action).

    This is Gate 1 -- the LLM boundary. Nothing leaves this class as a
    trustworthy decision unless the governor's output parsed AND validated.
    The harness applies Gate 2 (the ledger boundary) before it acts on or
    records any decision.
"""

import anthropic
from typing import Dict, List, Optional
import json


class ClaudeGovernanceDecider:
    """Uses the real Claude API for governance decisions (fail-closed)."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Claude client."""
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = "claude-opus-4-6"
        self.decisions: List[Dict] = []

    # ---- Gate 1 helpers: the LLM boundary --------------------------------

    @staticmethod
    def _extract_text(message) -> Optional[str]:
        """Pull assistant text out of a Messages API response.

        Returns None when the response shape is unintelligible (no content,
        or no text block). Callers treat None as a fail-closed signal rather
        than indexing blindly into content[0].
        """
        try:
            blocks = getattr(message, "content", None)
            if not blocks:
                return None
            for block in blocks:
                if getattr(block, "type", None) == "text":
                    text = getattr(block, "text", None)
                    if text:
                        return text
            return None
        except Exception:
            return None

    @staticmethod
    def _parse(response_text: Optional[str], required_keys) -> Optional[Dict]:
        """Parse + structurally validate a governor response.

        Returns the decision dict on success, or None to signal a
        fail-closed condition (unintelligible output, invalid JSON, wrong
        type, or a missing required field). None is the single 'refuse'
        signal that every caller maps to its own safe default.
        """
        if response_text is None:
            return None
        try:
            decision = json.loads(response_text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(decision, dict):
            return None
        for key in required_keys:
            if key not in decision:
                return None
        return decision

    def _call(self, prompt: str, max_tokens: int):
        """Make one Messages API call; return (text, transport_error).

        Any transport/client failure is converted to (None, err) so the
        caller can fail closed instead of raising through the pipeline.
        """
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return self._extract_text(message), None
        except Exception as e:  # noqa: BLE001 - fail closed on any client error
            return None, str(e)

    def _record(self, decision: Dict) -> Dict:
        self.decisions.append(decision)
        return decision

    @staticmethod
    def _note(transport_error: Optional[str]) -> str:
        return f" transport_error={transport_error}" if transport_error else ""

    # ---- Decision methods ------------------------------------------------

    def decide_healing_bounds(self, queue_name: str, current_wait: float,
                              baseline_wait: float, drift_magnitude: float) -> Dict:
        """Ask Claude: should we heal this queue? What bounds?"""

        prompt = f"""
You are an IVR governance expert. A call queue has experienced drift:

Queue: {queue_name}
Current wait time: {current_wait:.1f}s
Baseline wait time: {baseline_wait:.1f}s
Drift magnitude: {drift_magnitude*100:.1f}%

Your task: Decide if we should self-heal this queue parameter, and if so, what bounds.

Respond ONLY with valid JSON:
{{
    "should_heal": true/false,
    "reasoning": "brief explanation",
    "lo_bound": 4.0,
    "hi_bound": 120.0,
    "target_wait": proposed_target_in_seconds,
    "confidence": 0.0-1.0
}}
"""
        response_text, transport_error = self._call(prompt, max_tokens=200)
        decision = self._parse(response_text, ("should_heal", "reasoning"))

        if decision is None:
            # FAIL-CLOSED: unintelligible governor output -> do NOT heal.
            decision = {
                "should_heal": False,
                "reasoning": "Governor output unintelligible or unparseable; "
                             "fail-closed, no heal applied." + self._note(transport_error),
                "lo_bound": 4.0,
                "hi_bound": 120.0,
                "target_wait": None,
                "confidence": 0.0,
                "governed": False,
                "parse_failed": True,
            }
        else:
            decision.setdefault("lo_bound", 4.0)
            decision.setdefault("hi_bound", 120.0)
            decision["governed"] = True
            decision["parse_failed"] = False

        decision["queue"] = queue_name
        return self._record(decision)

    def decide_staffing_adjustment(self, queue_name: str, current_agents: int,
                                   current_wait: float, target_wait: float,
                                   abandonment_rate: float) -> Dict:
        """Ask Claude: how many agents should we staff?"""

        prompt = f"""
You are a contact center workforce manager. A queue needs staffing adjustment:

Queue: {queue_name}
Current agents: {current_agents}
Current wait: {current_wait:.1f}s
Target wait: {target_wait:.1f}s
Abandonment rate: {abandonment_rate*100:.1f}%

Based on Erlang C principles, recommend staffing level.

Respond ONLY with valid JSON:
{{
    "recommended_agents": integer,
    "reasoning": "brief explanation",
    "expected_wait": estimated_wait_in_seconds,
    "confidence": 0.0-1.0
}}
"""
        response_text, transport_error = self._call(prompt, max_tokens=200)
        decision = self._parse(response_text, ("recommended_agents", "reasoning"))

        if decision is None:
            # FAIL-CLOSED: no fabricated staffing recommendation.
            decision = {
                "recommended_agents": None,
                "reasoning": "Governor output unintelligible or unparseable; "
                             "fail-closed, no staffing change recommended." + self._note(transport_error),
                "expected_wait": None,
                "confidence": 0.0,
                "governed": False,
                "parse_failed": True,
            }
        else:
            decision["governed"] = True
            decision["parse_failed"] = False

        decision["queue"] = queue_name
        return self._record(decision)

    def decide_queue_reordering(self, current_order: list, success_rates: Dict,
                                caller_distribution: Dict) -> Dict:
        """Ask Claude: how should we reorder the queue menu?"""

        prompt = f"""
You are an IVR menu design expert. Current queue ordering and performance:

Current order: {current_order}
Success rates by queue: {json.dumps(success_rates, indent=2)}
Caller distribution (intent likelihood): {json.dumps(caller_distribution, indent=2)}

Recommend optimal menu ordering to maximize resolution rates and minimize abandonment.

Respond ONLY with valid JSON:
{{
    "proposed_order": ["queue1", "queue2", ...],
    "reasoning": "explanation of reordering logic",
    "expected_impact": 0.0-1.0,
    "confidence": 0.0-1.0
}}
"""
        response_text, transport_error = self._call(prompt, max_tokens=300)
        decision = self._parse(response_text, ("proposed_order", "reasoning"))

        if decision is None:
            # FAIL-CLOSED: no fabricated reordering.
            decision = {
                "proposed_order": None,
                "reasoning": "Governor output unintelligible or unparseable; "
                             "fail-closed, menu order left unchanged." + self._note(transport_error),
                "expected_impact": 0.0,
                "confidence": 0.0,
                "governed": False,
                "parse_failed": True,
            }
        else:
            decision["governed"] = True
            decision["parse_failed"] = False

        return self._record(decision)

    def safety_check(self, action: str, details: Dict) -> Dict:
        """Ask Claude: is this governance action safe? (fail-closed)"""

        prompt = f"""
You are an AI safety auditor for IVR systems. Evaluate this governance action:

Action: {action}
Details: {json.dumps(details, indent=2)}

Questions to consider:
- Would this harm customer experience?
- Could this cause cascading failures?
- Is this reversible?
- Does it respect governance bounds?

Respond ONLY with valid JSON:
{{
    "safe": true/false,
    "risk_level": "low"/"medium"/"high",
    "reasoning": "detailed explanation",
    "recommendations": ["if", "not", "safe"],
    "confidence": 0.0-1.0
}}
"""
        response_text, transport_error = self._call(prompt, max_tokens=300)
        decision = self._parse(response_text, ("safe", "reasoning"))

        # Gate 1: for a SAFETY gate, `safe` must be an actual boolean. A
        # non-bool value is unintelligible for a go/no-go decision, so it
        # fails closed exactly like a parse error.
        if decision is not None and not isinstance(decision.get("safe"), bool):
            decision = None

        if decision is None:
            # FAIL-CLOSED: the critical flip. Unintelligible => NOT safe.
            decision = {
                "safe": False,
                "risk_level": "high",
                "reasoning": "Governor output unintelligible or unparseable; "
                             "fail-closed, action blocked pending manual review."
                             + self._note(transport_error),
                "recommendations": ["hold_action", "manual_review"],
                "confidence": 0.0,
                "governed": False,
                "parse_failed": True,
            }
        else:
            decision["governed"] = True
            decision["parse_failed"] = False

        return self._record(decision)

    def get_decision_log(self) -> list:
        """Get all decisions made by Claude (approvals, rejections, blocks)."""
        return self.decisions
