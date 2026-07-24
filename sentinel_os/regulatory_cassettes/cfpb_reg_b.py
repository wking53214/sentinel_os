"""
CFPB / ECOA / Regulation B lens -- the reference regulatory cassette.

Checks lending-shaped decisions against two Reg B expectations by
default, plus two independent OPT-IN C2 bias-identification screens:

1. Adverse-action reason SPECIFICITY (12 CFR 1002.9): when an outcome
   differs from what was requested, the stated reasons must be
   specific to the case -- "does not meet our minimum credit
   standards" and its relatives are the textbook failures. The
   kernel's own invariant already guarantees a reason EXISTS on ANY
   outcome mismatch (episode.validate_episode -- deliberately broader
   than formal denials, covering approved-but-reduced shapes too);
   this lens screens whether the reason that exists is specific or
   boilerplate.

2. Prohibited-basis INPUT screening (C2 dimension 1, always on):
   ECOA's prohibited bases (race, color, religion, national origin,
   sex, marital status, age, receipt of public assistance income)
   appearing directly in decision inputs, and declared PROXY
   variables for them -- zip code standing in for race being the
   canonical example from this repo's own lending auditor-question
   catalog, and name-based (BISG-style) proxying being the CFPB's own
   documented methodology risk. Declared-name screening only;
   statistical disparate-impact testing is deliberately out of scope
   (see regulatory_checks module docstring for the open product
   decision it waits on).

3. Input-authorization TIER screening (C2 dimension 2, OPT-IN, off by
   default -- constructor arg enable_input_authorization_tier_screen):
   is each input variable on record as authorized to be used at all?
   See regulatory_checks.check_input_authorization_tier. CFPB itself
   has no filed-variable regime, so this lens's profile leaves
   authorized_inputs/tier_floor at RegulationCheckProfile's defaults
   (empty map, T2_PERMITTED floor) -- enabling the toggle with no
   populated tier declarations means every input reports
   T5_UNDECLARED, which is itself an honest, valid screening result,
   not a bug. Populating CFPB-specific tier declarations is a
   separate data decision, not attempted this session.

4. Narrative-legitimacy screening (C2 dimension 3, OPT-IN, off by
   default -- constructor arg enable_narrative_legitimacy_screen):
   screens a free-text decision narrative for protected-
   characteristic-adjacent language when the outcome deviated from
   what was requested. See regulatory_checks.check_narrative_legitimacy.
   CFPB_REG_B_PROFILE leaves narrative_field unset (None) -- Reg B
   itself does not designate one universal narrative field across
   lenders, so enabling this toggle with no narrative_field configured
   is a legitimate no-op (Phase A: "this regulation has no narrative
   expectation" -- see the checker's own docstring). Declaring a real
   narrative_field for a specific deployment is, again, a data
   decision left to whoever configures that deployment.

Both C2 opt-in screens are wired the same way block_on_placeholder
already is: independent booleans that live in get_profile()'s
returned dict, so enabling either automatically changes this lens's
content hash (no new hashing logic needed -- see
Tests/test_regulatory_cassettes.py's binding-conflict tests for the
proof pattern). Turning either on does NOT change this lens's default
behavior for existing (non-opted-in) callers: the corresponding
dimension's key is omitted entirely from the C2 rollup mapping when
its toggle is off (see c2_rollup below), never passed as None -- None
means "applicable but not yet evaluated" and would force every
non-opted-in caller's rollup status to INDETERMINATE, which is exactly
the silent behavior change this lens avoids.

DISCLOSED LIMITATIONS (also documented in regulatory_checks, repeated
here because they apply directly to how this lens screens): renaming a
bad, proxy, or undeclared-tier variable to an innocuous name defeats
both the proxy screen and the tier screen alike. The narrative screen
cannot catch a sufficiently disconnected fabricated reason, and its
phrase matching is English-only to start.

FUTURE BLOCKING VARIANTS: block_on_placeholder shows the pattern for
granting a live insertion blocking behavior -- escalate a SPECIFIC
finding classification (here, "placeholder" reasons) to ACTION_BLOCK,
never the whole check. If a blocking variant of the tier or narrative
screen is ever wanted, follow that same pattern (e.g. escalate
"prohibited_input" tier findings, not every tier finding) rather than
gating an entire check behind one blocking switch. Not built this
session -- both C2 opt-in screens are disclosure-only regardless of
their enable toggle (see review() below).

WHY THIS IS THE REFERENCE LENS: everything regulation-specific in this
file is DATA -- the RegulationCheckProfile below and a few identity
strings. The checking machinery lives in regulatory_checks and is
shared. A CMS lens ("denial notices must cite specific current
criteria, not generic algorithmic output") or a NAIC
insurance-adverse-outcome lens is this file with different phrases and
different proxy maps -- proven by test (a CMS-style profile runs
through the same checker in Tests/test_regulatory_cassettes.py), not
just asserted.

This lens SCORES AND FLAGS FOR HUMAN REVIEW. It does not, and must
never be described to, determine or certify ECOA / Reg B compliance.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from regulatory_cassette_interface import (
    ACTION_BLOCK,
    MODE_LIVE,
    MODE_OBSERVER,
    DecisionMaterial,
    RegulatoryCassette,
    RegulatoryCassetteConfig,
    RegulatoryFinding,
)
from regulatory_checks import (
    DIMENSION_INPUT_AUTHORIZATION_TIER,
    DIMENSION_KNOWN_BAD_VARIABLE_NAMES,
    DIMENSION_NARRATIVE_LEGITIMACY,
    DIMENSION_STATISTICAL_OUTCOME_EQUITY,
    C2Rollup,
    RegulationCheckProfile,
    check_input_authorization_tier,
    check_narrative_legitimacy,
    check_proxy_variables,
    check_reason_specificity,
    rollup_c2_bias_identification,
)

CHECK_REASON_SPECIFICITY = "adverse_action_reason_specificity"
CHECK_PROHIBITED_BASIS = "prohibited_basis_input_screen"
# C2 dimensions 2 & 3 -- opt-in, off by default. Names match
# check_input_authorization_tier / check_narrative_legitimacy's own
# default check_name so a disclosure event's "check" field always
# reads the same whether invoked here or directly.
CHECK_INPUT_AUTHORIZATION_TIER = "input_authorization_tier_screen"
CHECK_NARRATIVE_LEGITIMACY = "narrative_legitimacy_screen"

_REGULATION = ("ECOA / Regulation B, 12 CFR Part 1002 "
               "(adverse action notification, 1002.9)")

# The Reg B profile: this dict-of-data IS the lens's regulatory
# content. It rides in the snapshot and therefore in the content hash
# the ledger binds at insertion -- editing a phrase here without
# bumping the lens version trips the binding refusal, by design.
CFPB_REG_B_PROFILE = RegulationCheckProfile(
    regulation=_REGULATION,
    # Boilerplate that, unaccompanied by case specifics, marks a reason
    # as generic under Reg B's specific-principal-reasons expectation.
    # (Presence only lowers the score -- "credit score 574 is below the
    # 620 required for the amount requested" names a factor AND cites
    # case values, and passes on those signals.)
    generic_phrases=(
        "does not meet",
        "did not meet",
        "minimum standards",
        "credit standards",
        "internal policy",
        "internal criteria",
        "policy reasons",
        "proprietary",
        "creditworthiness",
        "credit score",
        "risk score",
        "risk model",
        "model output",
        "algorithm",
        "algorithmic",
        "automated decision",
        "unable to approve",
        "at this time",
        "general standards",
        "score threshold",
        "overall profile",
    ),
    # Prohibited bases appearing DIRECTLY as input variable names.
    # ECOA 701(a) list; age and public assistance carry limited
    # permitted uses, which is exactly why the finding is a flag for
    # human review, never a violation determination.
    direct_protected_terms={
        r"\brace\b": "race (ECOA prohibited basis)",
        r"\bcolor\b": "color (ECOA prohibited basis)",
        r"religio": "religion (ECOA prohibited basis)",
        r"national[_ ]?origin": "national origin (ECOA prohibited basis)",
        r"\bsex\b": "sex (ECOA prohibited basis)",
        r"\bgender\b": "sex (ECOA prohibited basis)",
        r"marital": "marital status (ECOA prohibited basis)",
        r"\bage\b": "age (ECOA prohibited basis; limited permitted uses -- "
                    "review required)",
        r"public[_ ]?assistance": "receipt of public assistance income "
                                  "(ECOA prohibited basis)",
    },
    # Declared proxy patterns: variables with documented history of
    # standing in for a prohibited basis in lending. Name screening
    # only.
    proxy_variables={
        r"zip": "race / national origin (geographic proxy)",
        r"postal": "race / national origin (geographic proxy)",
        r"census": "race / national origin (geographic proxy)",
        r"neighborhood": "race / national origin (geographic proxy)",
        r"geo": "race / national origin (geographic proxy)",
        r"latitude|longitude": "race / national origin (geographic proxy)",
        r"surname|last[_ ]?name": "race / national origin (BISG-style "
                                  "name proxy)",
        r"first[_ ]?name": "race / national origin / sex (name proxy)",
        r"language": "national origin (language-preference proxy)",
    },
)


class CFPBRegBLens(RegulatoryCassette):
    """The CFPB / ECOA / Reg B reference lens.

    Supports both modes. Default live behavior is FLAG-ONLY (screening
    for human review -- decision 4 of this framework's architecture);
    block_on_placeholder=True opts a live insertion into blocking
    judgment when a recorded reason is a bare code / non-answer, the
    one classification where "needs a human before this proceeds" is
    hard to argue with. That switch is part of the profile and
    therefore part of the content hash: a blocking variant is
    DIFFERENT LENS CONTENT and must carry its own version string, or
    the binding tripwire will refuse it -- which is the tripwire
    doing its job.

    Two additional, INDEPENDENT opt-in booleans (both default False,
    both off by default so existing callers see byte-identical
    behavior): enable_input_authorization_tier_screen wires in C2
    dimension 2 (regulatory_checks.check_input_authorization_tier);
    enable_narrative_legitimacy_screen wires in C2 dimension 3
    (check_narrative_legitimacy). Independent because a deployment may
    have no free-text narrative field to screen but still want tier
    checking, or vice versa -- a single combined toggle would force
    that choice. Like block_on_placeholder, both booleans ride in
    get_profile()'s returned dict, so flipping either changes this
    lens's content hash automatically -- no new hashing logic. Both
    screens are disclosure-only (ACTION_FLAG) regardless of the toggle:
    neither is ever rewritten to ACTION_BLOCK the way placeholder
    reasons optionally are (see the module docstring's "FUTURE BLOCKING
    VARIANTS" note for the pattern to follow if that's ever wanted).

    See c2_rollup() for how findings from all four C2 dimensions
    (this lens supplies at most three; dimension 4, statistical
    outcome-equity, is unbuilt) combine into one PASS/FLAG/INDETERMINATE
    status via regulatory_checks.rollup_c2_bias_identification.
    """

    MODES = (MODE_OBSERVER, MODE_LIVE)

    def __init__(self, block_on_placeholder: bool = False,
                 enable_input_authorization_tier_screen: bool = False,
                 enable_narrative_legitimacy_screen: bool = False,
                 version: str = "1.0.0"):
        self._block_on_placeholder = bool(block_on_placeholder)
        self._enable_tier_screen = bool(enable_input_authorization_tier_screen)
        self._enable_narrative_screen = bool(enable_narrative_legitimacy_screen)
        self._version = str(version)

    def get_config(self) -> RegulatoryCassetteConfig:
        return RegulatoryCassetteConfig(
            name="cfpb-ecoa-reg-b",
            version=self._version,
            description=("Screens adverse-action reason specificity and "
                         "prohibited-basis / proxy input variables for "
                         "lending-shaped decisions. Screening for human "
                         "review; not a compliance determination."),
            regulation=_REGULATION,
            authority="Consumer Financial Protection Bureau (CFPB)",
        )

    def get_checks(self) -> Tuple[str, ...]:
        checks = [CHECK_REASON_SPECIFICITY, CHECK_PROHIBITED_BASIS]
        if self._enable_tier_screen:
            checks.append(CHECK_INPUT_AUTHORIZATION_TIER)
        if self._enable_narrative_screen:
            checks.append(CHECK_NARRATIVE_LEGITIMACY)
        return tuple(checks)

    def get_profile(self) -> Dict[str, Any]:
        return {
            **CFPB_REG_B_PROFILE.as_dict(),
            "block_on_placeholder": self._block_on_placeholder,
            "enable_input_authorization_tier_screen": self._enable_tier_screen,
            "enable_narrative_legitimacy_screen": self._enable_narrative_screen,
        }

    def review(self, material: DecisionMaterial) -> List[RegulatoryFinding]:
        findings = check_reason_specificity(
            material, CFPB_REG_B_PROFILE, check_name=CHECK_REASON_SPECIFICITY,
        )
        if self._block_on_placeholder:
            findings = [
                RegulatoryFinding(
                    check=f.check, subject_id=f.subject_id,
                    regulation=f.regulation, action=ACTION_BLOCK,
                    classification=f.classification, score=f.score,
                    evidence={**f.evidence,
                              "escalated": "placeholder reasons block under "
                                           "this lens configuration"},
                ) if f.classification == "placeholder" else f
                for f in findings
            ]
        findings.extend(check_proxy_variables(
            material, CFPB_REG_B_PROFILE, check_name=CHECK_PROHIBITED_BASIS,
        ))
        # Both C2 opt-in screens below are disclosure-only (ACTION_FLAG
        # from the checker itself) -- unlike block_on_placeholder above,
        # nothing here ever escalates a finding to ACTION_BLOCK.
        if self._enable_tier_screen:
            findings.extend(check_input_authorization_tier(
                material, CFPB_REG_B_PROFILE,
                check_name=CHECK_INPUT_AUTHORIZATION_TIER,
            ))
        if self._enable_narrative_screen:
            findings.extend(check_narrative_legitimacy(
                material, CFPB_REG_B_PROFILE,
                check_name=CHECK_NARRATIVE_LEGITIMACY,
            ))
        return findings

    def c2_rollup(self, material: DecisionMaterial,
                  statistical_outcome_equity_findings: Optional[List[RegulatoryFinding]] = None,
                  ) -> C2Rollup:
        """Combine this lens's findings into one C2 bias-identification
        status via rollup_c2_bias_identification.

        Dimension 1 (known_bad_variable_names) is always evaluated --
        the proxy/direct-protected screen always runs. Dimensions 2 and
        3 are evaluated ONLY when their constructor toggle is on; when
        a toggle is off, that dimension's key is OMITTED from the
        mapping entirely (not passed as None) -- omission means "not
        applicable to this call," which rollup_c2_bias_identification
        excludes from the status calculation. Passing None instead
        would mean "applicable but not yet evaluated" and force
        INDETERMINATE on every non-opted-in caller, which is exactly
        the silent default-behavior change this lens's opt-in design
        avoids (see the class and module docstrings).

        Dimension 4 (statistical_outcome_equity) is COHORT-level, not
        per-decision (see regulatory_checks.check_statistical_outcome_
        equity's own docstring for why) -- this single-`material` method
        has no cohort of its own to compute it from. Default None:
        exactly the same "unbuilt/no data" posture as before dimension 4
        existed, so a caller who does nothing differently sees identical
        behavior. A caller that HAS already run
        check_statistical_outcome_equity against the relevant cohort
        (reading sealed-channel data -- never the live decision) may pass
        its findings list here (an empty list for a clean cohort result,
        a non-empty list for flagged groups) to have it included for
        real. Passing None still means "not yet evaluated" and keeps the
        rollup INDETERMINATE -- this method never runs the cohort check
        itself, so it cannot silently fabricate a result the caller
        didn't actually compute.
        """
        dimension_findings: Dict[str, Any] = {
            DIMENSION_KNOWN_BAD_VARIABLE_NAMES: check_proxy_variables(
                material, CFPB_REG_B_PROFILE, check_name=CHECK_PROHIBITED_BASIS,
            ),
        }
        if self._enable_tier_screen:
            dimension_findings[DIMENSION_INPUT_AUTHORIZATION_TIER] = (
                check_input_authorization_tier(
                    material, CFPB_REG_B_PROFILE,
                    check_name=CHECK_INPUT_AUTHORIZATION_TIER,
                )
            )
        if self._enable_narrative_screen:
            dimension_findings[DIMENSION_NARRATIVE_LEGITIMACY] = (
                check_narrative_legitimacy(
                    material, CFPB_REG_B_PROFILE,
                    check_name=CHECK_NARRATIVE_LEGITIMACY,
                )
            )
        dimension_findings[DIMENSION_STATISTICAL_OUTCOME_EQUITY] = (
            statistical_outcome_equity_findings
        )
        return rollup_c2_bias_identification(dimension_findings)

    def validate(self) -> bool:
        config = self.get_config()
        expected_checks = 2 + int(self._enable_tier_screen) \
            + int(self._enable_narrative_screen)
        return (
            config is not None
            and bool(CFPB_REG_B_PROFILE.generic_phrases)
            and bool(CFPB_REG_B_PROFILE.proxy_variables)
            and bool(CFPB_REG_B_PROFILE.direct_protected_terms)
            and len(self.get_checks()) == expected_checks
        )
