"""
human_selection_v1 -- F2's locked vocabulary for a human's response to
a governed episode's governor verdict.

THE FOUNDING IDEA THIS CLOSES: nothing in this codebase has ever
captured which recommendation a human accepted, overrode, or rejected,
or fed that signal back anywhere -- confirmed by an exhaustive search
(2026-08-07) before this module was written: governance/recommend_v1.py's
recommend() is dead code (only its own test calls it); recommendation_
impact.py's healing_bounds/queue_reordering recommendations are shadow-
run ONLY, by explicit design, never surfaced to a human; queue_staffing_
bayes_integration.py's StaffingCoordinator.propose_adjustment is
constructed in production_harness.py but never called; RegulatoryBlock
("Human review required") is raised but never caught anywhere; adverse-
action reasoning (mortgage_cassette.py, the CFPB Reg B lens) only scores
reasons a human already wrote, never proposes one; no API endpoint
anywhere lets a human review/accept/override a decision. The ONE
recommendation surface confirmed live is the governor's own verdict
(GovernanceDecider.safety_check's {safe, reasoning}) on a governed
episode -- record_recommendation_shadow_run's own docstring says so
directly: "only safety_check is" wired into the live decision path.

THE PILOT SCOPE (locked 2026-08-07, confirmed before implementation):
this covers ONLY that one surface -- a human's review of the governor's
verdict on a governance_decision ledger row. ONE reversible experiment,
not a system-wide rollout: queue-reordering, healing-bounds, staffing-
adjustment, and every shadow-run recommendation remain exactly as they
were before this module -- untouched, still shadow-only, still never
acted on. See governance/ledger_postgres.py's record_human_selection
for the capture mechanism itself.

THE VOCABULARY:
  CONCUR   -- the human reviewed the governor's verdict and agrees
              with it (the recorded outcome stands).
  OVERRIDE -- the human reviewed the verdict and reversed it (their
              judgment differs from the governor's).
  ESCALATE -- the human declined to render a decision here and routed
              it elsewhere (a supervisor, a different reviewer, ...).

THIS DOES NOT FEED ANY LEARNER. Capturing the signal durably and
queryably is the whole scope here -- simple_rl_trainer.py is
simulator-only and slated to move to GSA-815; wiring this signal into
an actual training loop is a separate, later decision this module
deliberately does not make. record_human_selection and
get_human_selections exist so that decision can be made later, from
real data, instead of designed around a guess now.
"""

from __future__ import annotations

# Stable strings -- these ride in ledger rows, same posture as every
# other locked vocabulary in this repo (outcome_v1.py's RESOLVED/
# ABANDONED reasons, cassette_capabilities.py's CAPABILITY_* names).
HUMAN_SELECTION_CONCUR = "concur"
HUMAN_SELECTION_OVERRIDE = "override"
HUMAN_SELECTION_ESCALATE = "escalate"

HUMAN_SELECTIONS = frozenset({
    HUMAN_SELECTION_CONCUR,
    HUMAN_SELECTION_OVERRIDE,
    HUMAN_SELECTION_ESCALATE,
})
