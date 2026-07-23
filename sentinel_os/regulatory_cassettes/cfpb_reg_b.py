"""
CFPB / ECOA / Regulation B lens -- the reference regulatory cassette.

Checks lending-shaped decisions against two Reg B expectations:

1. Adverse-action reason SPECIFICITY (12 CFR 1002.9): when an outcome
   differs from what was requested, the stated reasons must be
   specific to the case -- "does not meet our minimum credit
   standards" and its relatives are the textbook failures. The
   kernel's own invariant already guarantees a reason EXISTS on ANY
   outcome mismatch (episode.validate_episode -- deliberately broader
   than formal denials, covering approved-but-reduced shapes too);
   this lens screens whether the reason that exists is specific or
   boilerplate.

2. Prohibited-basis INPUT screening: ECOA's prohibited bases (race,
   color, religion, national origin, sex, marital status, age,
   receipt of public assistance income) appearing directly in
   decision inputs, and declared PROXY variables for them -- zip code
   standing in for race being the canonical example from this repo's
   own lending auditor-question catalog, and name-based (BISG-style)
   proxying being the CFPB's own documented methodology risk.
   Declared-name screening only; statistical disparate-impact testing
   is deliberately out of scope (see regulatory_checks module
   docstring for the open product decision it waits on).

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

from typing import Any, Dict, List, Tuple

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
    RegulationCheckProfile,
    check_proxy_variables,
    check_reason_specificity,
)

CHECK_REASON_SPECIFICITY = "adverse_action_reason_specificity"
CHECK_PROHIBITED_BASIS = "prohibited_basis_input_screen"

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
    """

    MODES = (MODE_OBSERVER, MODE_LIVE)

    def __init__(self, block_on_placeholder: bool = False,
                 version: str = "1.0.0"):
        self._block_on_placeholder = bool(block_on_placeholder)
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
        return (CHECK_REASON_SPECIFICITY, CHECK_PROHIBITED_BASIS)

    def get_profile(self) -> Dict[str, Any]:
        return {
            **CFPB_REG_B_PROFILE.as_dict(),
            "block_on_placeholder": self._block_on_placeholder,
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
        return findings

    def validate(self) -> bool:
        config = self.get_config()
        return (
            config is not None
            and bool(CFPB_REG_B_PROFILE.generic_phrases)
            and bool(CFPB_REG_B_PROFILE.proxy_variables)
            and bool(CFPB_REG_B_PROFILE.direct_protected_terms)
            and len(self.get_checks()) == 2
        )
