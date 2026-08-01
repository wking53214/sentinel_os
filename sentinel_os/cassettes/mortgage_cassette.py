"""
Mortgage Cassette -- residential mortgage lending.

First cassette to enable outcome_obligation (cassette_capabilities.
OutcomeObligations). A mortgage decision's outcome is NOT known at
decision time: the loan performs, or it doesn't, over years -- so this
domain owes a durable obligation instead of a decision row that
reopens. See outcome_v1 for the record and the Provenance Rule
governing it.

CAPABILITIES is deliberately just (CAPABILITY_OUTCOME_OBLIGATION,) --
kernel-only otherwise. This domain has no call-center surface at all:
no telephony ingest, no queue routing, no RL reward, no self-healing.
Judgment happens entirely through the kernel surface (judge/explain
over episodes), the same posture banking_cassette takes for
telephony_ingest.

RESOLUTION VOCABULARY (locked domain decisions, Wm 2026-07-29)
----------------------------------------------------------------
Two RESOLVED paths, one ABANDONED path, one non-event:

  paid_in_full        -- full-term payoff OR refinance payoff. Both
                          count the same: the debt is retired on the
                          borrower's terms. favorable=True.
  involuntary_closure  -- foreclosure, short sale, and deed-in-lieu are
                          ONE bucket (unfavorable outcome for the
                          borrower is the same regardless of exact
                          mechanism); RESOLUTION_MECHANISM in `detail`
                          is an optional sub-classification a customer
                          can turn on, never required. favorable=False.
  permanent modification -- NOT a resolution. A permanent loan
                          modification always issues a new loan number
                          (servicer/GSE convention, not universal --
                          see the module docstring note below). The
                          ORIGINAL obligation must be abandoned via
                          outcome_v1.abandon(..., REASON_DECISION_
                          SUPERSEDED) -- not resolved, no verdict,
                          excluded from fairness stats -- and a fresh
                          decision + obligation opens under the new
                          loan number. This cassette does not perform
                          that abandon() call itself (that is
                          orchestration, the same layer obligation_
                          sweep.py lives at, not cassette logic); it is
                          documented here because classify_outcome must
                          never be handed a modification event and
                          asked to call it favorable/unfavorable --
                          there is no outcome to classify, only a
                          decision to supersede.

                          MECHANISM (locked 2026-07-29): the new
                          decision declares which old one it replaces
                          by setting GovernanceDecisionRecord.
                          replaces_hash to the old decision's
                          current_hash -- an explicit fact the loan
                          origination system states, never inferred by
                          matching address/loan-number/date.
                          append_decision refuses the write outright if
                          that hash isn't a real prior decision
                          (fail-closed). obligation_supersession.py
                          reads every declared replaces_hash off the
                          primary ledger and abandons the matching old
                          obligation via the twin's existing
                          transition endpoint. See that module's
                          docstring for the full mechanism, and
                          canonical_fields.OPTIONAL_HASHED_FIELDS for
                          why replaces_hash is a distinct field from
                          Item 6's supersedes_hash (a human correcting
                          an existing decision's own verdict -- a
                          different concept from a brand-new decision
                          making an old obligation moot).
  forbearance          -- does NOT resolve the obligation. Loan stays
                          OPEN under the SAME loan number; the horizon
                          keeps counting. A servicing detail, not a
                          governance event. classify_outcome is never
                          called with a forbearance-shaped evidence
                          dict for this reason -- forbearance produces
                          no resolved_value at all.

Secured-loan generalization note: paid-in-full / involuntary-closure-
family / forbearance-style relief all generalize to other secured loan
types (a future auto-loan cassette's repo replaces foreclosure, for
example). "Modification issues a new loan number" does NOT generalize
by default -- it is a mortgage/GSE-reporting convention. Each future
loan-type cassette sets its own rule; nothing here should be imported
by another cassette as a shared default.

resolved_value SHAPE (the Dict classify_outcome reads)
--------------------------------------------------------
  resolution_type                    -- RESOLUTION_PAID_IN_FULL or
                                         RESOLUTION_INVOLUNTARY_CLOSURE
  resolution_date                    -- when the resolution occurred
  outstanding_balance_at_resolution  -- balance at that point
  recovery_amount                    -- involuntary-closure path only

OUTCOME HORIZON -- 3 years (1095 days), NOT the original 24-month
roadmap default. The original default doesn't fit mortgages (they run
far longer than 24 months); researched replacement, locked 2026-07-29:
"seasoning" -- the mortgage industry's own standard measure of when a
loan vintage's default risk has revealed itself. Urban Institute and
Dallas Fed credit-risk research both use the 3-year default rate as
the standard measure of a vintage's quality (FDIC data: only 10% of
loans that eventually default do so in year 1, but 43% have by year
3 -- risk is heavily front-loaded and mostly visible in that window).
A separate, narrower number exists -- the FFIEC Uniform Retail Credit
Classification Policy's 180-day charge-off/tax-writeoff trigger for
an ALREADY-delinquent loan secured by residential real estate -- but
that answers a different question (how to account for a loan already
in trouble) and lives in resolved_value's timing, not the horizon
itself.

PROPERTY ADDRESS -- decision.input_fields carries the property address
under PROPERTY_ADDRESS_FIELD ("loan_property_address"). No prior
convention existed for this anywhere in the real repo (input_fields is
a free-form Dict[str, Any] per cassette -- confirmed by reading
production_harness.py's actual IVR/Banking ledger write, which has no
address field at all). This is the field the new ZIP/county regional-
equity cohort dimension geocodes via bisg_estimator.CensusGeocoder,
reusing the existing address-to-tract pipeline rather than a new data
source.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from cassette_interface import Cassette, CassetteConfig, QualityResult
from cassette_capabilities import (
    CAPABILITY_OUTCOME_OBLIGATION,
    OutcomeObligations,
)
from episode import Episode, outcome_mismatches
from outcome_v1 import MaturationRule

# --- Resolution vocabulary (locked, see module docstring) ---
RESOLUTION_PAID_IN_FULL = "paid_in_full"
RESOLUTION_INVOLUNTARY_CLOSURE = "involuntary_closure"
RESOLUTION_TYPES = (RESOLUTION_PAID_IN_FULL, RESOLUTION_INVOLUNTARY_CLOSURE)

# Optional sub-detail for involuntary_closure -- never required, a
# customer can turn this on to distinguish the mechanism.
INVOLUNTARY_CLOSURE_MECHANISMS = ("foreclosure", "short_sale", "deed_in_lieu")

# The maturation rule's kind and horizon (see module docstring for the
# research backing 3y). Declared here once so get_maturation_rule() and
# the governance parameter below cannot drift apart -- validate()
# checks that agreement at load time.
_MATURATION_KIND = "loan_performance"
_OUTCOME_HORIZON_DAYS = 1095  # 3 years

# decision.input_fields key carrying the property address (locked
# 2026-07-29 -- see module docstring; no prior convention existed).
PROPERTY_ADDRESS_FIELD = "loan_property_address"


class MortgageCassette(Cassette, OutcomeObligations):
    """Residential mortgage lending cassette.

    Kernel-only plus outcome_obligation: no telephony/routing/RL/
    self-healing surface, because this domain has none. Judgment
    happens through judge/explain over episodes (see _score_components
    for what "judgment" means here).
    """

    CAPABILITIES = (CAPABILITY_OUTCOME_OBLIGATION,)

    # THE declaration site for mortgage governance numbers (see
    # cassette_schema). governance_trigger is the kernel-required
    # parameter every cassette declares; outcome_horizon_days is owned
    # by the outcome_obligation capability this cassette enables.
    _GOVERNANCE_PARAMETERS = {
        "governance_trigger": {
            "value": 1,
            "type": "int",
            "min": 0,
            "max": 100,
            "unit": "integrity issues",
            "description": (
                "Episodes with >= this many decision-process integrity "
                "issues (requested-vs-actual mismatches, or an adverse "
                "action recorded with a reason too thin to be a "
                "documented basis) are routed to the governor "
                "(inclusive)."
            ),
            "metadata": {
                "approval_date": None,
                "justification": (
                    "Set to 1 (not higher): unlike IVR/banking friction "
                    "events, a mortgage decision-integrity issue is a "
                    "regulatory-adjacent signal (ECOA/Reg B adverse-"
                    "action reason requirements) on its own, not "
                    "something that should accumulate before review."
                ),
                "last_reviewed": None,
            },
        },
        "outcome_horizon_days": {
            "value": _OUTCOME_HORIZON_DAYS,
            "type": "int",
            "min": 1,
            "max": 10950,  # 30 years -- a full mortgage term, as an outer bound
            "unit": "days",
            "description": (
                "Days from decision until the loan_performance outcome "
                "obligation matures (3-year mortgage 'seasoning' "
                "convention -- see module docstring for the industry "
                "research backing this number)."
            ),
            "metadata": {
                "approval_date": None,
                "justification": (
                    "Replaces an originally-assumed 24-month default "
                    "that does not fit mortgages. Researched 2026-07-29: "
                    "Urban Institute / Dallas Fed mortgage credit-risk "
                    "research uses 3-year default rates as the standard "
                    "seasoning measure (FDIC: 43% of eventual defaulters "
                    "have defaulted by year 3, vs 10% by year 1)."
                ),
                "last_reviewed": None,
            },
        },
    }

    def get_config(self) -> CassetteConfig:
        return CassetteConfig(
            name="mortgage-v1",
            version="1.0.0",
            description="Residential mortgage lending",
            domain="mortgage",
        )

    def get_governance_parameters(self) -> Dict[str, Dict]:
        """The typed governance declaration (see cassette_schema)."""
        return copy.deepcopy(self._GOVERNANCE_PARAMETERS)

    # ---- outcome_obligation capability ----

    def get_maturation_rule(self) -> MaturationRule:
        """3-year loan_performance horizon. Computed from the SAME
        _OUTCOME_HORIZON_DAYS constant the governance parameter
        declares, not a separately-typed literal, so the hashed
        declaration and the governance snapshot cannot silently
        diverge (validate() checks this agreement at load time)."""
        return MaturationRule(
            kind=_MATURATION_KIND,
            horizon_seconds=float(_OUTCOME_HORIZON_DAYS) * 86400.0,
        )

    def classify_outcome(self, evidence: Dict[str, Any]) -> Optional[bool]:
        """The domain call on a matured obligation's resolved_value.

        Reads resolution_type only -- see module docstring for the
        full resolved_value shape. An unrecognized or missing
        resolution_type returns None (genuinely ambiguous) rather than
        guessing: forbearance and permanent-modification events never
        reach here with resolved_value data in the first place (see
        module docstring), so a resolution_type this cassette doesn't
        recognize is a genuine gap to surface, not a shape to coerce.
        """
        resolution_type = evidence.get("resolution_type")
        if resolution_type == RESOLUTION_PAID_IN_FULL:
            return True
        if resolution_type == RESOLUTION_INVOLUNTARY_CLOSURE:
            return False
        return None

    # ---- Kernel judgment surface ----
    #
    # "Judgment" for a mortgage decision episode means decision-PROCESS
    # integrity, not call quality (this domain has no call): does the
    # recorded outcome match what was requested, and when it doesn't,
    # is the reason on file substantive rather than a bare
    # kernel-satisfying placeholder? (Locked with Wm 2026-07-29 -- the
    # kernel's own episode validation already guarantees a NON-EMPTY
    # reason exists on any mismatch; this cassette scores whether that
    # reason is SUBSTANTIVE, which is a judgment call the kernel
    # deliberately leaves to the cassette. This is deliberately
    # lighter-weight than regulatory_checks.check_reason_specificity's
    # boilerplate-phrase screening -- that is a separate, opt-in
    # compliance lens; this is the cassette's own baseline judgment,
    # every mortgage decision gets it whether or not a regulatory lens
    # is attached.)

    # A reason this short is present (satisfies the kernel) but too
    # thin to be a documented basis for an adverse action.
    _THIN_REASON_WORD_COUNT = 3

    # An episode recording no requested fields at all has nothing for
    # outcome_mismatches to compare `actual` against -- outcome_mismatches
    # iterates episode.requested, so an empty requested dict silently
    # produces the SAME "no mismatch" result as a genuinely verified
    # clean match. Fixed 2026-08-01: this can no longer reach the top
    # score -- see the requested_vs_actual_unverifiable factor below.
    _UNVERIFIABLE_PENALTY = 0.25

    def _episode_facts(self, episode: Episode):
        mismatches = outcome_mismatches(episode)
        reasons = list(episode.outcome_reasons)
        requested_recorded = bool(episode.requested)
        return mismatches, reasons, requested_recorded

    def _score_components(self, mismatches, reasons, requested_recorded):
        factors: List[Dict[str, Any]] = []
        score = 1.0

        if not mismatches:
            if requested_recorded:
                factors.append({
                    "factor": "requested_vs_actual",
                    "value": 0,
                    "contribution": 0.0,
                    "detail": "outcome matched what was requested; no mismatch",
                })
            else:
                score -= self._UNVERIFIABLE_PENALTY
                factors.append({
                    "factor": "requested_vs_actual_unverifiable",
                    "value": 0,
                    "contribution": -self._UNVERIFIABLE_PENALTY,
                    "detail": (
                        "no requested fields were recorded on this episode "
                        "-- there is nothing to compare actual against, so "
                        "decision-process integrity on this axis could not "
                        "be checked (this is NOT the same as a verified "
                        "clean match, and cannot score in the top tier)"
                    ),
                })
        else:
            penalty = min(len(mismatches) * 0.2, 0.6)
            score -= penalty
            factors.append({
                "factor": "requested_vs_actual_mismatch",
                "value": len(mismatches),
                "contribution": -penalty,
                "detail": (
                    f"{len(mismatches)} requested field(s) differ from "
                    f"actual ({', '.join(m.name for m in mismatches)}); "
                    f"0.2 per mismatch, capped at 0.6"
                ),
            })

        # Reason substance is checked on EVERY episode that has a reason
        # on file, not only inside the mismatch branch -- a placeholder
        # reason attached with no mismatch (e.g. an adverse action whose
        # recorded outcome matched what was requested, so no mismatch
        # ever fires) previously skipped this check entirely and could
        # score 1.0/excellent with a one-word reason never examined.
        if reasons:
            thin = [r for r in reasons
                   if len(r.split()) < self._THIN_REASON_WORD_COUNT]
            if thin:
                penalty2 = min(len(thin) * 0.2, 0.4)
                score -= penalty2
                factors.append({
                    "factor": "reason_substance",
                    "value": len(thin),
                    "contribution": -penalty2,
                    "detail": (
                        f"{len(thin)} outcome reason(s) under "
                        f"{self._THIN_REASON_WORD_COUNT} words -- "
                        f"present (kernel requires it on a mismatch) but "
                        f"too thin to be a documented basis for the "
                        f"adverse action"
                    ),
                })
            else:
                factors.append({
                    "factor": "reason_substance",
                    "value": len(reasons),
                    "contribution": 0.0,
                    "detail": (
                        f"all outcome reasons are substantive "
                        f"(>= {self._THIN_REASON_WORD_COUNT} words)"
                    ),
                })
        elif mismatches:
            # Defensive only: episode.validate_episode already refuses a
            # mismatch with no reason on file, so judge_episode never
            # reaches this cassette in that state. Scored anyway rather
            # than assumed unreachable.
            score -= 0.6
            factors.append({
                "factor": "reason_substance",
                "value": 0,
                "contribution": -0.6,
                "detail": "mismatch present with no outcome reason on file",
            })
        # else: no mismatch and no reasons on file -- normal, nothing to
        # explain (requested_recorded's own factor above already covers
        # the unverifiable case when requested was empty).

        score = max(0.0, min(1.0, score))

        if score > 0.80:
            tier = "excellent"
        elif score > 0.60:
            tier = "good"
        elif score > 0.35:
            tier = "poor"
        else:
            tier = "failed"

        return QualityResult(score=score, tier=tier), factors

    def judge(self, episode: Episode) -> QualityResult:
        """Judge one validated episode on decision-process integrity
        (see class-level note above for what that means here)."""
        mismatches, reasons, requested_recorded = self._episode_facts(episode)
        result, _ = self._score_components(mismatches, reasons, requested_recorded)
        return result

    def explain(self, episode: Episode) -> List[Dict[str, Any]]:
        """Factor-level reasons in mortgage vocabulary. Kernel
        verification findings (raw mismatches/discrepancies) are
        prepended by episode.explain_episode; these factors are this
        cassette's own scoring interpretation of them."""
        mismatches, reasons, requested_recorded = self._episode_facts(episode)
        _, factors = self._score_components(mismatches, reasons, requested_recorded)
        return factors

    def validate(self) -> bool:
        """Self-check: config present, and the declared
        outcome_horizon_days parameter agrees with what
        get_maturation_rule() actually returns -- the hashed
        declaration and the governance snapshot must not be able to
        silently diverge."""
        if self.get_config() is None:
            return False
        params = self.get_governance_parameters()
        declared_days = params.get("outcome_horizon_days", {}).get("value")
        if declared_days is None:
            return False
        rule = self.get_maturation_rule()
        return rule.kind == _MATURATION_KIND and rule.horizon_seconds == (
            float(declared_days) * 86400.0
        )
