"""
Regulatory checks -- reusable, regulation-parameterized screening.

This module is the REUSABLE half of the regulatory-cassette framework:
generic checker code, configured per regulation by a
RegulationCheckProfile. The CFPB / ECOA / Reg B lens
(regulatory_cassettes/cfpb_reg_b.py) is one profile; a CMS lens
("denial notices must cite specific current criteria, not generic
algorithmic output") or a NAIC insurance-adverse-outcome lens is
another profile over the SAME functions here -- a configuration file,
not a rewrite. That reusability is the point of this module; the CFPB
lens is the reference proof it works.

Two checks this session, both deterministic and fully explainable:

1. check_reason_specificity -- does a recorded outcome reason read as
   case-specific, or as generic/placeholder boilerplate? The kernel
   already guarantees a reason EXISTS on any outcome mismatch
   (episode.validate_episode -- any mismatch, not just denials); this
   check screens whether the reason that exists actually says
   anything. Grounded in the Reg B adverse-action requirement that
   stated reasons be specific principal reasons, and in the same
   "specific criteria, not generic algorithmic output" expectation CMS
   applies to denial notices.

2. check_proxy_variables -- flag input variables that are known
   proxies for protected characteristics (zip code standing in for
   race is the canonical lending example, straight from this repo's
   own auditor-question catalog), and flag protected characteristics
   present directly in decision inputs. DECLARED-PATTERN SCREENING
   ONLY, scoped down on purpose: this does NOT compute statistical
   correlation or disparate impact. Full disparate-impact testing
   requires deciding whether Sentinel ever captures real
   protected-characteristic data or works proxy-only -- an open
   product decision that belongs to Wm, deliberately not guessed at
   here. Until it is made, this check screens by declared variable
   names, which needs no protected-class data at all.

Every function here SCORES AND FLAGS FOR HUMAN REVIEW. Nothing in this
module determines legal compliance, and nothing may be described as
doing so (see regulatory_cassette_interface.SCREENING_DISCLAIMER).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Tuple

from regulatory_cassette_interface import (
    ACTION_FLAG,
    DecisionMaterial,
    RegulatoryFinding,
)

# Score vocabulary for the specificity check, kept as named constants so
# the arithmetic below is legible as policy rather than magic numbers.
_BASE_SCORE = 0.5           # a reason starts neutral
_VALUE_REFERENCE_CREDIT = 0.25   # cites a concrete value (number/amount/date)
_FIELD_REFERENCE_CREDIT = 0.25   # names an actual case field
_GENERIC_HIT_PENALTY = 0.25      # per distinct generic phrase, capped
_GENERIC_PENALTY_CAP = 0.5

# Default pattern for "this reason cites a concrete value": any digit
# sequence (amounts, scores, dates, criterion numbers, billing codes).
# Profiles may override for regulations with different notions of a
# concrete citation.
_DEFAULT_VALUE_REFERENCE_PATTERN = r"\d"

# Default placeholder shapes: pure codes ("R-12", "DECLINE_001"),
# classic non-answers, and bare outcome restatements. A reason that is
# only a code or only restates the outcome explains nothing.
_DEFAULT_PLACEHOLDER_PATTERNS = (
    r"^[A-Za-z]{1,10}[-_ ]?\d{1,6}$",
    r"^(n/?a|none|tbd|pending|unknown|see\s+file|standard|policy)$",
    r"^(denied|declined|rejected|adverse\s+action|not\s+approved)\.?$",
    r"^reason(\s*\d+)?$",
)


@dataclass(frozen=True)
class RegulationCheckProfile:
    """One regulation's configuration of the shared checkers.

    This dataclass IS the extension point: a new regulation is a new
    profile instance (typically declared as data in that regulation's
    lens module), never new checker code. Everything here is JSON-safe
    via as_dict(), because the profile rides inside the lens snapshot
    and is therefore part of the content hash the ledger binds --
    changing a phrase list is changing the lens, and the binding
    tripwire treats it that way.

    Fields:
      regulation           -- citation string findings carry.
      generic_phrases      -- substrings that mark boilerplate for THIS
                              regulation ("does not meet our standards",
                              "algorithmic output", ...). Case-insensitive
                              substring match; each distinct hit lowers
                              the specificity score.
      placeholder_patterns -- full-string regexes for reasons that are
                              codes or non-answers; a match classifies
                              the reason "placeholder" outright.
      value_reference_pattern -- regex whose presence counts as citing a
                              concrete value.
      extra_case_fields    -- field names beyond the material's own
                              (mismatched fields + input variables) that
                              count as case references when named in a
                              reason.
      specific_score_threshold -- reasons scoring BELOW this flag as
                              generic. 0.5 default: a reason with no
                              specific signal and any generic hit flags;
                              one concrete signal with no generic hit
                              passes.
      proxy_variables      -- variable-name pattern -> what it proxies
                              for. Matched against input variable NAMES
                              (word-ish, case-insensitive).
      direct_protected_terms -- variable-name pattern -> the protected
                              characteristic itself, for inputs that
                              carry a protected characteristic directly.
    """

    regulation: str
    generic_phrases: Tuple[str, ...] = ()
    placeholder_patterns: Tuple[str, ...] = _DEFAULT_PLACEHOLDER_PATTERNS
    value_reference_pattern: str = _DEFAULT_VALUE_REFERENCE_PATTERN
    extra_case_fields: Tuple[str, ...] = ()
    specific_score_threshold: float = 0.5
    proxy_variables: Mapping[str, str] = field(default_factory=dict)
    direct_protected_terms: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe form for lens snapshots (content-hashed)."""
        return {
            "regulation": self.regulation,
            "generic_phrases": sorted(self.generic_phrases),
            "placeholder_patterns": list(self.placeholder_patterns),
            "value_reference_pattern": self.value_reference_pattern,
            "extra_case_fields": sorted(self.extra_case_fields),
            "specific_score_threshold": float(self.specific_score_threshold),
            "proxy_variables": dict(sorted(self.proxy_variables.items())),
            "direct_protected_terms": dict(sorted(self.direct_protected_terms.items())),
        }


# ---------------------------------------------------------------------------
# Check 1: reason specificity
# ---------------------------------------------------------------------------

def _field_name_appears(field_name: str, text_lower: str) -> bool:
    """True when every token of a field name appears in the text as a
    word ("credit_limit" matches "your credit limit was reduced",
    "amount" matches "the amount requested"). Token-wise so snake_case
    field names match natural prose."""
    tokens = [t for t in re.split(r"[_\W]+", field_name.lower()) if t]
    if not tokens:
        return False
    return all(re.search(rf"\b{re.escape(token)}\b", text_lower) for token in tokens)


def assess_reason_specificity(reason: str, material: DecisionMaterial,
                              profile: RegulationCheckProfile) -> Dict[str, Any]:
    """Score ONE recorded reason for case-specificity under a profile.

    Always returns the full assessment (score, classification,
    evidence) whether or not it would flag -- check_reason_specificity
    turns flagged assessments into findings; reports and tests can
    read the assessment for passing reasons too.

    The arithmetic, stated as policy: a reason starts neutral (0.5).
    Citing a concrete value (+0.25) and naming an actual case field
    (+0.25) are the two specific signals. Each distinct generic phrase
    hit costs 0.25, capped at 0.5. A reason matching a placeholder
    pattern is classified "placeholder" at score 0.0 regardless --
    a bare code cannot buy its way back with a digit, which is also
    why placeholder patterns are tested BEFORE value credit.
    """
    text = reason.strip()
    text_lower = text.lower()

    for pattern in profile.placeholder_patterns:
        if re.fullmatch(pattern, text_lower, flags=re.IGNORECASE):
            return {
                "reason": reason,
                "score": 0.0,
                "classification": "placeholder",
                "flagged": True,
                "evidence": {
                    "placeholder_pattern": pattern,
                    "detail": "reason is a code or non-answer; it explains nothing "
                              "about this case",
                },
            }

    generic_hits = sorted({p for p in profile.generic_phrases
                           if p.lower() in text_lower})
    has_value = bool(re.search(profile.value_reference_pattern, text))

    candidate_fields = list(material.mismatched_fields)
    candidate_fields += list(material.input_fields.keys())
    candidate_fields += list(profile.extra_case_fields)
    referenced_fields = sorted({name for name in candidate_fields
                                if _field_name_appears(name, text_lower)})

    score = _BASE_SCORE
    if has_value:
        score += _VALUE_REFERENCE_CREDIT
    if referenced_fields:
        score += _FIELD_REFERENCE_CREDIT
    score -= min(_GENERIC_HIT_PENALTY * len(generic_hits), _GENERIC_PENALTY_CAP)
    score = max(0.0, min(1.0, score))

    flagged = score < profile.specific_score_threshold
    classification = "generic" if flagged else "specific"
    return {
        "reason": reason,
        "score": score,
        "classification": classification,
        "flagged": flagged,
        "evidence": {
            "generic_phrase_hits": generic_hits,
            "cites_concrete_value": has_value,
            "case_fields_referenced": referenced_fields,
            "score_meaning": "0.0-1.0, how case-specific this reason reads "
                             "under this profile; a screening score, not a "
                             "compliance probability",
        },
    }


def check_reason_specificity(material: DecisionMaterial,
                             profile: RegulationCheckProfile,
                             check_name: str = "reason_specificity"
                             ) -> List[RegulatoryFinding]:
    """Screen every recorded reason on one decision; findings for the
    ones that read generic/placeholder, plus a "missing" finding when
    a mismatch is on record with no reason at all (reachable only for
    ledger rows -- the kernel refuses to validate such an episode, so
    live-mode material can never arrive reasonless with a mismatch).
    """
    findings: List[RegulatoryFinding] = []

    if not material.reasons:
        if material.mismatched_fields:
            findings.append(RegulatoryFinding(
                check=check_name,
                subject_id=material.subject_id,
                regulation=profile.regulation,
                action=ACTION_FLAG,
                classification="missing",
                score=0.0,
                evidence={
                    "mismatched_fields": list(material.mismatched_fields),
                    "detail": "outcome differs from what was requested and no "
                              "reason is on record",
                },
            ))
        return findings

    for assessment in (assess_reason_specificity(r, material, profile)
                       for r in material.reasons):
        if assessment["flagged"]:
            findings.append(RegulatoryFinding(
                check=check_name,
                subject_id=material.subject_id,
                regulation=profile.regulation,
                action=ACTION_FLAG,
                classification=assessment["classification"],
                score=float(assessment["score"]),
                evidence={"reason": assessment["reason"],
                          **assessment["evidence"]},
            ))
    return findings


# ---------------------------------------------------------------------------
# Check 2: proxy-variable / direct-protected-input screen
# ---------------------------------------------------------------------------

def _name_matches(pattern: str, variable_name: str) -> bool:
    """Pattern match against a variable NAME (not its value):
    case-insensitive regex search over the name AND over the name with
    separators spaced out. The second form exists because \\b patterns
    like r"\\brace\\b" would never fire on snake_case names
    ("applicant_race" -- underscore is a word character, so there is no
    boundary before "race"), and snake_case is exactly how decision
    inputs are usually named."""
    spaced = re.sub(r"[_\-]+", " ", variable_name)
    return any(
        re.search(pattern, candidate, flags=re.IGNORECASE) is not None
        for candidate in (variable_name, spaced)
    )


def check_proxy_variables(material: DecisionMaterial,
                          profile: RegulationCheckProfile,
                          check_name: str = "proxy_variable_screen"
                          ) -> List[RegulatoryFinding]:
    """Screen the decision's input VARIABLE NAMES against the profile's
    declared proxy and direct-protected patterns.

    Direct hits are reported before proxy hits, and a variable that
    matches a direct pattern is not double-reported as a proxy of
    itself. Values are never inspected -- this is a declared-name
    screen (see the module docstring for why statistical testing is
    deliberately out of scope this session).
    """
    findings: List[RegulatoryFinding] = []
    for name in sorted(material.input_fields):
        direct = next((char for pattern, char
                       in sorted(profile.direct_protected_terms.items())
                       if _name_matches(pattern, name)), None)
        if direct is not None:
            findings.append(RegulatoryFinding(
                check=check_name,
                subject_id=material.subject_id,
                regulation=profile.regulation,
                action=ACTION_FLAG,
                classification="direct_protected_characteristic",
                score=1.0,
                evidence={
                    "variable": name,
                    "characteristic": direct,
                    "detail": "decision input carries a protected characteristic "
                              "directly; flagged for human review of whether its "
                              "use here is permitted",
                    "score_meaning": "1.0 = declared name pattern matched; not a "
                                     "measure of influence on the decision",
                },
            ))
            continue
        proxied = next((char for pattern, char
                        in sorted(profile.proxy_variables.items())
                        if _name_matches(pattern, name)), None)
        if proxied is not None:
            findings.append(RegulatoryFinding(
                check=check_name,
                subject_id=material.subject_id,
                regulation=profile.regulation,
                action=ACTION_FLAG,
                classification="proxy_variable",
                score=1.0,
                evidence={
                    "variable": name,
                    "proxies_for": proxied,
                    "detail": "decision input matches a declared proxy pattern "
                              "for a protected characteristic; flagged for human "
                              "review (declared-name screening only -- no "
                              "statistical correlation is computed)",
                    "score_meaning": "1.0 = declared name pattern matched; not a "
                                     "measure of influence on the decision",
                },
            ))
    return findings
