"""
AI Cost Tracking - What governance decisions actually cost.

claude_governance_api.py is the only place in this repo that calls the real
Claude API (four methods on ClaudeGovernanceDecider: decide_healing_bounds,
decide_staffing_adjustment, decide_queue_reordering, safety_check). Before
this module, none of those calls ever inspected `usage` on the response --
every real API response carries exact input/output token counts and they
were simply never read, let alone costed or disclosed.

PRICING TABLE
-------------
MODEL_PRICING is a snapshot of base (non-cached, non-batch) per-million-token
USD rates from PRICING_SOURCE, fetched PRICING_FETCHED. claude_governance_api's
calls use neither prompt caching nor the Batch API today, so base rates are
the whole story for THIS codebase's actual usage -- if either gets adopted
later, cost_of_call needs cache/batch tiers added before its numbers can be
trusted for those calls.

A model not in this table is NOT priced at $0 and is NOT priced by guessing
a similarly-named model's rate -- either would be exactly the kind of
"almost/maybe/kind of" answer Wm's Provenance Rule exists to rule out (see
event_v1.py's module docstring). Instead cost_usd comes back None with
unpriced_reason naming the unpriced model, so an auditor reading the ledger
sees "we don't have a price for this model" rather than a silently wrong
number that looks as authoritative as a real one.

CLAUDE SONNET 5 PRICING NOTE: $2/$10 per MTok is INTRODUCTORY pricing, in
effect only through 2026-08-31; standard pricing of $3/$15 takes over
2026-09-01 (source page, same fetch). This table has ONE rate per model, not
one per date range -- whoever is still using this file after that date needs
to update the claude-sonnet-5 entry by hand, or extend cost_of_call to take
an as-of date. Flagging this now rather than let it go stale silently.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICING_FETCHED = "2026-07-31"

# model id (as returned in response.model) -> (input $/MTok, output $/MTok).
# Base rates only -- see module docstring. Source + fetch date above.
MODEL_PRICING: Dict[str, Tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-5": (2.0, 10.0),  # introductory through 2026-08-31, see above
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-3-5": (0.80, 4.0),
}


@dataclass
class AICallCost:
    """The cost of one real Claude API call.

    input_tokens/output_tokens are always real when present -- reported
    directly by the API on any response that has a `usage` field, whether
    or not the response's JSON body went on to parse. cost_usd is None
    when the model that served the call isn't in MODEL_PRICING;
    unpriced_reason then names it. A knowable-but-currently-absent price
    is the point of this shape -- silently reporting 0.0 would look like
    a real, free call rather than a gap in this table.
    """
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Optional[float]
    unpriced_reason: Optional[str]

    def as_dict(self) -> Dict:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "unpriced_reason": self.unpriced_reason,
            "pricing_source": PRICING_SOURCE if self.cost_usd is not None else None,
        }


def cost_of_call(model: str, input_tokens: int, output_tokens: int) -> AICallCost:
    """Compute the real cost of one API call from its actual reported usage.

    Never guesses a nearby model's price for one not in MODEL_PRICING --
    see module docstring.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return AICallCost(
            model=model, input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=None,
            unpriced_reason=(
                f"no pricing data for model {model!r} (MODEL_PRICING last "
                f"updated {PRICING_FETCHED} from {PRICING_SOURCE})"),
        )
    input_rate, output_rate = pricing
    cost = ((input_tokens / 1_000_000) * input_rate
            + (output_tokens / 1_000_000) * output_rate)
    return AICallCost(
        model=model, input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=round(cost, 6), unpriced_reason=None,
    )
