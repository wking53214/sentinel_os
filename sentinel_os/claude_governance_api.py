"""
Claude Governance API - Real LLM decisions for Iceberg

Routes critical governance decisions to Claude instead of simulation
"""

import anthropic
from typing import Dict, Optional
import json

class ClaudeGovernanceDecider:
    """Uses real Claude API for governance decisions"""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize Claude client"""
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = "claude-opus-4-6"
        self.decisions = []
    
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
        
        message = self.client.messages.create(
            model=self.model,
            max_tokens=200,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        response_text = message.content[0].text
        
        try:
            decision = json.loads(response_text)
        except json.JSONDecodeError:
            # Fallback if Claude doesn't return valid JSON
            decision = {
                "should_heal": True,
                "reasoning": "Claude response parsing failed, defaulting to heal",
                "lo_bound": 4.0,
                "hi_bound": 120.0,
                "target_wait": baseline_wait * 1.2,
                "confidence": 0.3
            }
        
        decision["queue"] = queue_name
        self.decisions.append(decision)
        return decision
    
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
        
        message = self.client.messages.create(
            model=self.model,
            max_tokens=200,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        response_text = message.content[0].text
        
        try:
            decision = json.loads(response_text)
        except json.JSONDecodeError:
            # Fallback
            decision = {
                "recommended_agents": max(current_agents, 3),
                "reasoning": "Claude response parsing failed",
                "expected_wait": target_wait,
                "confidence": 0.3
            }
        
        decision["queue"] = queue_name
        self.decisions.append(decision)
        return decision
    
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
        
        message = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        response_text = message.content[0].text
        
        try:
            decision = json.loads(response_text)
        except json.JSONDecodeError:
            # Fallback
            sorted_queues = sorted(success_rates.items(), 
                                 key=lambda x: x[1], reverse=True)
            decision = {
                "proposed_order": [q for q, _ in sorted_queues],
                "reasoning": "Claude response parsing failed, defaulting to success-rate sort",
                "expected_impact": 0.1,
                "confidence": 0.3
            }
        
        self.decisions.append(decision)
        return decision
    
    def safety_check(self, action: str, details: Dict) -> Dict:
        """Ask Claude: is this governance action safe?"""
        
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
        
        message = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        response_text = message.content[0].text
        
        try:
            decision = json.loads(response_text)
        except json.JSONDecodeError:
            decision = {
                "safe": True,
                "risk_level": "low",
                "reasoning": "Claude response parsing failed, defaulting to safe",
                "recommendations": [],
                "confidence": 0.3
            }
        
        self.decisions.append(decision)
        return decision
    
    def get_decision_log(self) -> list:
        """Get all decisions made by Claude"""
        return self.decisions
