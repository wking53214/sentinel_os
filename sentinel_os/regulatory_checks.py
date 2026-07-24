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

Checks in this module, all deterministic and fully explainable:

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

3. check_input_authorization_tier -- is each decision input variable
   on record as authorized to be used at all, and how solid is that
   record? A 7-tier ladder (T0 prohibited .. T6 vendor-opaque) that
   works whether an industry has a real filed-variable list (NAIC,
   FDA PCCP, DO-178C, NERC), only a blacklist (ECOA, NYC LL144), or
   nothing at all -- the checker never branches on industry; a
   profile just declares its own tier_floor and prohibited set (both
   may be empty). Every tier claim also carries a CONFIDENCE label
   (undeclared -> attested-unsupported -> attested-accountable-
   unsupported -> attested-accountable-evidenced -> verified) so a
   bare self-declared tier is never indistinguishable from an
   independently verified one -- see assess_input_authorization.

4. check_narrative_legitimacy -- screens free-text decision narrative
   (when a regulation expects one) for protected-characteristic-
   adjacent language, cross-referenced against whether the outcome
   deviated from what was requested and whether the stated reason(s)
   actually mention that content. A deviation + flagged language +
   an unrelated stated reason is a possible "laundered" reason: a
   real motivation dressed up as a policy-sounding one. Two-phase:
   Phase A (always runs) reports "not_screened" when a regulation
   declares it expects a narrative and the decision doesn't have
   one -- closing the gap where this would otherwise silently never
   fire. Phase B (the scan) only runs when narrative content exists.

5. check_statistical_outcome_equity -- dimension 4, the one check that
   can prove the AFFIRMATIVE ("outcomes were actually fair"), not just
   the negative. Structurally different from checks 1-4 above: those
   are PER-DECISION (one episode/row in, findings out); this one is
   inherently COHORT-level -- disparate impact is a comparison across
   groups, not a property of one decision. It does not run inside
   review()/judge()/explain() at all; it runs out-of-band against a
   cohort of decisions, the same batch shape
   regulatory_deck.RegulatoryDeck.observer_review already uses (reads a
   list, returns an aggregate report, writes nothing). Reads protected-
   characteristic data ONLY from the sealed channel
   (sealed_demographic_channel.py) -- self-reported or BISG-estimated
   (bisg_estimator.py) depending on the profile's consent_model -- NEVER
   from the live decision. Uses the EEOC four-fifths rule (a group's
   favorable-outcome rate below 80% of the highest-rate group's is a
   finding) -- the most established starting point in real regulatory
   practice, not a novel metric; flagged as a choice, not a certainty,
   in the function's own docstring. Below MIN_COHORT_SIZE_FOR_STATISTICAL_TEST,
   reports INDETERMINATE rather than a statistically meaningless PASS or
   FLAG from too few observations.

6. rollup_c2_bias_identification -- combines findings across whichever
   of the (up to four) C2 bias-identification dimensions actually ran
   for one decision into a single PASS / FLAG / INDETERMINATE status,
   per the AND-rollup rule: PASS only if every applicable dimension
   passes; FLAG if any applicable dimension flags; INDETERMINATE if
   any applicable dimension hasn't been evaluated. Checks 1-4 can only
   ever prove the NEGATIVE ("nothing bad found" -- absence of a bad
   signal); check 5 is the one dimension that can prove the
   AFFIRMATIVE, and only when real or estimated protected-characteristic
   data actually exists for the cohort in question -- absent that data,
   dimension 4 still reports None/INDETERMINATE, same posture as before
   it was built. Never describe checks 1-4 passing as though they were
   that affirmative proof.

DISCLOSED, UNSOLVED, NOT ATTEMPTED THIS SESSION: renaming a bad or
proxy or undeclared-tier variable to an innocuous name defeats checks
2 and 3 alike (same class of gap for both -- a model can encode bias
through jointly-boring declared variables with no single suspicious
name). Documented here, not silently shipped as solved, same posture
as every other disclosed limitation in this module.

JURISDICTION CONFLICT RULE for check 3: when two live regulatory
lenses in different jurisdictions disagree on the same variable's
tier, the STRICTER (lower-numbered, i.e. worse) tier wins. This is a
deliberately simple deterministic rule, not an elegant resolution --
see resolve_tier_conflict.

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


# ---------------------------------------------------------------------------
# Input-authorization tier ladder (check 3) -- vocabulary
# ---------------------------------------------------------------------------

T0_PROHIBITED = "T0_PROHIBITED"    # law bars this input outright
T1_FILED = "T1_FILED"              # on file with a regulator, approved
T2_PERMITTED = "T2_PERMITTED"      # legal, no positive approval on record
T3_INTERNAL = "T3_INTERNAL"        # company governance approved, no regulator
T4_PENDING = "T4_PENDING"          # submitted for approval, not yet granted
T5_UNDECLARED = "T5_UNDECLARED"    # in use, no authorization record anywhere
T6_OPAQUE = "T6_OPAQUE"            # vendor-supplied, input list not disclosable

TIERS = (T0_PROHIBITED, T1_FILED, T2_PERMITTED, T3_INTERNAL,
         T4_PENDING, T5_UNDECLARED, T6_OPAQUE)

# Ordinal position on the REAL ladder (T1 best .. T5 worst). T0 and T6
# are deliberately NOT part of this ordering -- they are categorical
# overrides (see _CATEGORICAL_FLAG_TIERS), not points on a spectrum a
# tier_floor can sit within.
_TIER_RANK = {T1_FILED: 1, T2_PERMITTED: 2, T3_INTERNAL: 3,
              T4_PENDING: 4, T5_UNDECLARED: 5}

# Tiers that always report as a finding regardless of tier_floor: T0
# because the law bars the input outright at any floor, T6 because an
# opaque/vendor-undisclosed input list must never silently pass, T5
# because "no authorization record anywhere" is never acceptable on
# its own ladder position (it is also the worst rank, so it would flag
# under any realistic floor anyway -- listed here for clarity, not
# because rank alone wouldn't already catch it).
_CATEGORICAL_FLAG_TIERS = frozenset({T0_PROHIBITED, T5_UNDECLARED, T6_OPAQUE})

# Confidence scale, low to high. Every tier CLAIM (a declared
# authorized_inputs entry) resolves to exactly one of these -- stacked,
# not alternatives (see module docstring). "undeclared" belongs to T5
# only (there is no claim to grade). A-only claims get
# attested-unsupported; +named owner (reusing the ledger's existing
# authorized_by field) promotes to attested-accountable-unsupported;
# +evidence (reusing cassette_schema.METADATA_SLOTS names --
# approval_date / justification / last_reviewed) promotes further to
# attested-accountable-evidenced; only an independently cross-checked
# claim (opt-in per-pattern, set by whoever authored the profile after
# doing that integration -- this checker never calls a registry itself)
# reaches "verified".
CONFIDENCE_UNDECLARED = "undeclared"
CONFIDENCE_ATTESTED_UNSUPPORTED = "attested-unsupported"
CONFIDENCE_ATTESTED_ACCOUNTABLE_UNSUPPORTED = "attested-accountable-unsupported"
CONFIDENCE_ATTESTED_ACCOUNTABLE_EVIDENCED = "attested-accountable-evidenced"
CONFIDENCE_VERIFIED = "verified"

# ---------------------------------------------------------------------------
# Consent model (check 5 / dimension 4 -- statistical outcome-equity)
# ---------------------------------------------------------------------------

# Default: GDPR, Virginia, and most non-CA US jurisdictions. Statistical
# (BISG) estimation is the default method for the fairness check under
# this model; voluntary opt-in self-disclosure is a supplement for
# customers who choose to provide real data, never a substitute this
# checker assumes exists.
CONSENT_OPT_IN_REQUIRED = "opt_in_required"

# e.g. California. Self-reported demographic data is collected by
# default (the customer may decline); a decline falls back to BISG
# estimation -- mirrors Reg B's own visual-observation/surname fallback
# pattern for mortgage GMI (12 CFR 1002.13), but statistical instead of
# human guessing.
CONSENT_OPT_OUT_PERMITTED = "opt_out_permitted"

VALID_CONSENT_MODELS = (CONSENT_OPT_IN_REQUIRED, CONSENT_OPT_OUT_PERMITTED)


@dataclass(frozen=True)
class TierDeclaration:
    """One profile's declared authorization-tier CLAIM for a variable-
    name pattern -- the configuration-time input to check 3, analogous
    to a single entry in proxy_variables but carrying the confidence
    metadata that entry doesn't need.

    tier            -- one of the TIERS constants.
    authorized_by   -- named accountable person/role on record for this
                       claim (same field name as the ledger's existing
                       authorized_by column -- reused, not reinvented).
                       None = a bare, unowned self-declaration.
    approval_date / justification / last_reviewed -- evidence slots,
                       same names as cassette_schema.METADATA_SLOTS.
                       None = that slot was not supplied.
    verified        -- True only when the profile's author has
                       independently cross-checked this specific claim
                       against a real external registry (e.g. NAIC
                       filings) and is asserting that in the profile
                       DATA. The checker never sets this itself and
                       never calls a registry -- that per-industry
                       integration is deliberately out of scope here,
                       which is what keeps this checker from branching
                       on industry.
    """

    tier: str
    authorized_by: str | None = None
    approval_date: str | None = None
    justification: str | None = None
    last_reviewed: str | None = None
    verified: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "authorized_by": self.authorized_by,
            "approval_date": self.approval_date,
            "justification": self.justification,
            "last_reviewed": self.last_reviewed,
            "verified": bool(self.verified),
        }


def _confidence_for(declaration: TierDeclaration) -> str:
    """Stack the four layers (A: bare attestation, B: evidence,
    C: named owner, D: independent verification) into one label.
    A declaration always has at least layer A (it exists at all), so
    the floor here is attested-unsupported, never undeclared --
    "undeclared" is reserved for variables with NO matching
    declaration (see assess_input_authorization)."""
    if declaration.verified:
        return CONFIDENCE_VERIFIED
    has_owner = bool(declaration.authorized_by)
    has_evidence = any((declaration.approval_date, declaration.justification,
                        declaration.last_reviewed))
    if has_owner and has_evidence:
        return CONFIDENCE_ATTESTED_ACCOUNTABLE_EVIDENCED
    if has_owner:
        return CONFIDENCE_ATTESTED_ACCOUNTABLE_UNSUPPORTED
    return CONFIDENCE_ATTESTED_UNSUPPORTED


def resolve_tier_conflict(tier_a: str, tier_b: str) -> str:
    """Jurisdiction conflict rule: when two live regulatory lenses
    disagree on the same variable's tier, the STRICTER tier wins.
    Categorical tiers (T0, T6) always win over any ranked tier, since
    both mean "always a finding" regardless of ladder position; T0
    (an outright legal bar) wins over T6 (undisclosed) when both
    lenses disagree on which categorical tier applies, since a legal
    prohibition is the stricter of the two. Between two ranked tiers
    (T1-T5), the higher-numbered (worse) rank wins. Deliberately a
    simple deterministic rule, not an elegant resolution -- see
    module docstring."""
    if tier_a == tier_b:
        return tier_a
    categorical_priority = {T0_PROHIBITED: 0, T6_OPAQUE: 1}
    a_cat = categorical_priority.get(tier_a)
    b_cat = categorical_priority.get(tier_b)
    if a_cat is not None or b_cat is not None:
        if a_cat is None:
            return tier_b
        if b_cat is None:
            return tier_a
        return tier_a if a_cat <= b_cat else tier_b
    # Both ranked (T1-T5, or T5 which is also categorical-by-rank):
    # higher rank number = worse = wins.
    rank_a = _TIER_RANK.get(tier_a, _TIER_RANK[T5_UNDECLARED])
    rank_b = _TIER_RANK.get(tier_b, _TIER_RANK[T5_UNDECLARED])
    return tier_a if rank_a >= rank_b else tier_b


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
      authorized_inputs    -- variable-name pattern -> TierDeclaration,
                              this regulation's input-authorization tier
                              ladder claims (check 3). May be empty --
                              every unmatched variable then reports
                              T5_UNDECLARED, which is itself a valid,
                              honest configuration for an industry with
                              no filed-variable regime at all.
      tier_floor            -- the minimum acceptable ranked tier
                              (T1-T5); a matched claim ranked worse than
                              this flags. Default T2_PERMITTED. Ignored
                              for T0/T6 (categorical, always flag) and
                              for T5 (already the worst rank).
      prohibited_inputs     -- variable-name patterns law bars outright,
                              independent of any declared tier -- a
                              variable matching here is T0 even if
                              authorized_inputs separately claims a
                              better tier for it (the prohibition wins).
      narrative_field       -- the input_fields key holding this
                              regulation's expected free-text narrative,
                              if it expects one at all (check 4).
                              None (default) = this regulation has no
                              narrative expectation; zero findings,
                              correctly not a gap.
      narrative_flag_phrases -- substrings marking protected-
                              characteristic-adjacent language in a
                              narrative. Same case-insensitive substring
                              matching style as generic_phrases, but a
                              separate list -- boilerplate-reason
                              phrases and protected-characteristic-
                              adjacent phrases are not the same
                              vocabulary.
      consent_model         -- CONSENT_OPT_IN_REQUIRED (default) or
                              CONSENT_OPT_OUT_PERMITTED (check 5 /
                              dimension 4, statistical outcome-equity).
                              Governs how protected-characteristic data
                              collected into the sealed channel
                              (sealed_demographic_channel.py) is sourced
                              for THIS regulation -- BISG-by-default vs.
                              self-report-by-default-with-BISG-fallback.
                              Never read by check_statistical_outcome_
                              equity itself (that function only reads
                              already-collected sealed-channel data); it
                              governs collection, upstream of the check.
    """

    regulation: str
    generic_phrases: Tuple[str, ...] = ()
    placeholder_patterns: Tuple[str, ...] = _DEFAULT_PLACEHOLDER_PATTERNS
    value_reference_pattern: str = _DEFAULT_VALUE_REFERENCE_PATTERN
    extra_case_fields: Tuple[str, ...] = ()
    specific_score_threshold: float = 0.5
    proxy_variables: Mapping[str, str] = field(default_factory=dict)
    direct_protected_terms: Mapping[str, str] = field(default_factory=dict)
    authorized_inputs: Mapping[str, "TierDeclaration"] = field(default_factory=dict)
    tier_floor: str = T2_PERMITTED
    prohibited_inputs: Tuple[str, ...] = ()
    narrative_field: str | None = None
    narrative_flag_phrases: Tuple[str, ...] = ()
    consent_model: str = CONSENT_OPT_IN_REQUIRED

    def __post_init__(self):
        if self.consent_model not in VALID_CONSENT_MODELS:
            raise ValueError(
                f"consent_model must be one of {VALID_CONSENT_MODELS}, "
                f"got {self.consent_model!r}"
            )

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
            "authorized_inputs": {
                pattern: decl.as_dict()
                for pattern, decl in sorted(self.authorized_inputs.items())
            },
            "tier_floor": self.tier_floor,
            "prohibited_inputs": sorted(self.prohibited_inputs),
            "narrative_field": self.narrative_field,
            "narrative_flag_phrases": sorted(self.narrative_flag_phrases),
            "consent_model": self.consent_model,
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


# ---------------------------------------------------------------------------
# Check 3: input-authorization tier screen
# ---------------------------------------------------------------------------

def assess_input_authorization(name: str, profile: RegulationCheckProfile
                                ) -> Dict[str, Any]:
    """Assess ONE input variable's authorization-tier claim under a
    profile. Always returns the full assessment (tier, confidence,
    flagged, evidence) whether or not it would flag -- mirrors
    assess_reason_specificity's posture, so every tier claim resolves
    to a confidence label instead of one silent PASS, even for callers
    that only want to inspect a clean variable's claim.

    Value is never inspected -- this is a declared-name screen, same
    posture as check_proxy_variables.
    """
    if any(_name_matches(pattern, name)
           for pattern in profile.prohibited_inputs):
        return {
            "variable": name,
            "tier": T0_PROHIBITED,
            "confidence": None,
            "flagged": True,
            "classification": "prohibited_input",
            "evidence": {
                "detail": "input matches a pattern this regulation bars "
                          "outright; the prohibition applies regardless of "
                          "any declared authorization tier for this variable",
            },
        }

    declaration = next(
        (decl for pattern, decl in sorted(profile.authorized_inputs.items())
         if _name_matches(pattern, name)),
        None,
    )

    if declaration is None:
        return {
            "variable": name,
            "tier": T5_UNDECLARED,
            "confidence": CONFIDENCE_UNDECLARED,
            "flagged": True,
            "classification": "undeclared_input",
            "evidence": {
                "detail": "input variable is in use with no authorization "
                          "record of any kind on file for this regulation",
            },
        }

    tier = declaration.tier
    confidence = _confidence_for(declaration)
    if tier in _CATEGORICAL_FLAG_TIERS:
        flagged = True
        classification = ("opaque_input" if tier == T6_OPAQUE
                          else "prohibited_input" if tier == T0_PROHIBITED
                          else "undeclared_input")
        detail = {
            T6_OPAQUE: "input is vendor-supplied with an undisclosable list; "
                       "reports as a finding, never a silent pass",
            T0_PROHIBITED: "this variable's declared tier is itself "
                           "prohibited",
            T5_UNDECLARED: "this variable's declared tier is itself "
                           "undeclared",
        }[tier]
    else:
        rank = _TIER_RANK.get(tier, _TIER_RANK[T5_UNDECLARED])
        floor_rank = _TIER_RANK.get(profile.tier_floor,
                                     _TIER_RANK[T2_PERMITTED])
        flagged = rank > floor_rank
        classification = "below_tier_floor" if flagged else "tier_acceptable"
        detail = (f"declared tier '{tier}' is below this regulation's "
                  f"tier_floor '{profile.tier_floor}'") if flagged else (
                  f"declared tier '{tier}' meets this regulation's "
                  f"tier_floor '{profile.tier_floor}'")

    return {
        "variable": name,
        "tier": tier,
        "confidence": confidence,
        "flagged": flagged,
        "classification": classification,
        "evidence": {
            "detail": detail,
            "authorized_by": declaration.authorized_by,
            "approval_date": declaration.approval_date,
            "justification": declaration.justification,
            "last_reviewed": declaration.last_reviewed,
            "verified": declaration.verified,
        },
    }


def check_input_authorization_tier(material: DecisionMaterial,
                                   profile: RegulationCheckProfile,
                                   check_name: str = "input_authorization_tier_screen"
                                   ) -> List[RegulatoryFinding]:
    """Screen every decision input variable's authorization-tier claim
    under a profile; findings for the ones that flag (prohibited,
    opaque, undeclared, or below tier_floor). See
    assess_input_authorization for the full per-variable assessment,
    including confidence, whether or not it flags.

    KNOWN UNSOLVED, DISCLOSED, NOT ATTEMPTED: renaming a bad or
    undeclared-tier variable to an innocuous name defeats this screen,
    same as it defeats check_proxy_variables (see module docstring).
    """
    findings: List[RegulatoryFinding] = []
    for name in sorted(material.input_fields):
        assessment = assess_input_authorization(name, profile)
        if not assessment["flagged"]:
            continue
        score = {
            CONFIDENCE_UNDECLARED: 1.0,
            CONFIDENCE_ATTESTED_UNSUPPORTED: 0.75,
            CONFIDENCE_ATTESTED_ACCOUNTABLE_UNSUPPORTED: 0.5,
            CONFIDENCE_ATTESTED_ACCOUNTABLE_EVIDENCED: 0.25,
            CONFIDENCE_VERIFIED: 0.0,
        }.get(assessment["confidence"], 1.0)
        findings.append(RegulatoryFinding(
            check=check_name,
            subject_id=material.subject_id,
            regulation=profile.regulation,
            action=ACTION_FLAG,
            classification=assessment["classification"],
            score=score,
            evidence={
                "variable": assessment["variable"],
                "tier": assessment["tier"],
                "confidence": assessment["confidence"],
                "score_meaning": "0.0-1.0, how much review priority this "
                                 "finding carries (1.0 = no evidence behind "
                                 "the claim at all, 0.0 = independently "
                                 "verified but still below floor); never a "
                                 "compliance probability",
                **assessment["evidence"],
            },
        ))
    return findings


# ---------------------------------------------------------------------------
# Check 4: narrative-legitimacy screen
# ---------------------------------------------------------------------------

def check_narrative_legitimacy(material: DecisionMaterial,
                               profile: RegulationCheckProfile,
                               check_name: str = "narrative_legitimacy_screen"
                               ) -> List[RegulatoryFinding]:
    """Two-phase screen of a decision's free-text narrative, when this
    regulation expects one.

    PHASE A (always runs): if profile.narrative_field is None, this
    regulation has no narrative expectation -- zero findings, correctly
    not a gap. If it IS declared but the decision's input_fields don't
    carry it (missing or blank), that is itself reviewable: one
    "not_screened" finding, same posture as check_reason_specificity's
    "missing" classification for a mismatch with no reason on record.
    This closes the gap where DecisionMaterial has no dedicated
    narrative slot -- without Phase A this check would otherwise
    silently never fire whenever a narrative wasn't captured.

    PHASE B (only when narrative content exists): screens the text for
    profile.narrative_flag_phrases (same substring-match style as
    generic_phrases, different vocabulary), then cross-references
    against material.mismatched_fields as the "did the outcome deviate
    from what was requested" signal. A deviation + flagged language +
    none of the recorded reasons mentioning that flagged content is a
    possible "laundered" reason for human review -- a real motivation
    dressed up as a policy-sounding one.

    DISCLOSED LIMITATIONS: cannot catch a sufficiently disconnected
    fabricated reason (raises the bar, does not close the gap);
    English-only phrase matching to start (non-English support is a
    known follow-up, not attempted this session).
    """
    if profile.narrative_field is None:
        return []

    narrative_text = material.input_fields.get(profile.narrative_field)
    if not isinstance(narrative_text, str) or not narrative_text.strip():
        return [RegulatoryFinding(
            check=check_name,
            subject_id=material.subject_id,
            regulation=profile.regulation,
            action=ACTION_FLAG,
            classification="not_screened",
            score=0.0,
            evidence={
                "narrative_field": profile.narrative_field,
                "detail": "this regulation expects a narrative and none was "
                          "found to screen on this decision",
            },
        )]

    text_lower = narrative_text.lower()
    phrase_hits = sorted({p for p in profile.narrative_flag_phrases
                          if p.lower() in text_lower})
    if not phrase_hits:
        return []
    if not material.mismatched_fields:
        # Flagged language with no outcome deviation to cross-reference
        # against: nothing to launder a reason FOR. Correctly no finding.
        return []

    reasons_text = " ".join(material.reasons).lower()
    explained = any(p.lower() in reasons_text for p in phrase_hits)
    if explained:
        return []

    return [RegulatoryFinding(
        check=check_name,
        subject_id=material.subject_id,
        regulation=profile.regulation,
        action=ACTION_FLAG,
        classification="possible_laundered_reason",
        score=1.0,
        evidence={
            "narrative_field": profile.narrative_field,
            "flagged_phrases": phrase_hits,
            "mismatched_fields": list(material.mismatched_fields),
            "stated_reasons": list(material.reasons),
            "detail": "narrative contains protected-characteristic-adjacent "
                      "language, the outcome deviated from what was "
                      "requested, and none of the stated reasons mention "
                      "the flagged content -- possible mismatch between the "
                      "real and stated rationale",
            "score_meaning": "1.0 = pattern conditions all matched; not a "
                             "determination that the decision was actually "
                             "motivated by the flagged characteristic",
        },
    )]


# ---------------------------------------------------------------------------
# Check 5: statistical outcome-equity (dimension 4) -- COHORT-level.
#
# Structurally different from checks 1-4: those take a single
# DecisionMaterial and return findings for THAT decision. Disparate
# impact is not a property of one decision -- it is a comparison across
# a cohort. This runs out-of-band against a batch of decisions, the same
# shape regulatory_deck.RegulatoryDeck.observer_review already uses
# (reads a list, returns an aggregate report, writes nothing) -- not
# forced into review()/judge()/explain()'s single-episode interface.
#
# Protected-characteristic data comes ONLY from the sealed channel
# (sealed_demographic_channel.py) -- self-reported or BISG-estimated
# (bisg_estimator.py), sourced per the profile's consent_model. This
# function never touches a live episode or the ledger's decision path
# directly; it operates on already-assembled CohortDecision records, the
# caller's job to assemble from sealed-channel reads + whatever
# outcome/subject data their own decision system already has.
# ---------------------------------------------------------------------------

# A common statistical rule-of-thumb minimum for a comparison to mean
# anything at all (roughly where the normal approximation to a binomial
# proportion starts being reasonable) -- proposed as a floor, not a
# certainty; real EEOC enforcement practice often wants larger cohorts
# before treating a four-fifths finding as more than a screening signal.
# Flagged as a choice: raise this for a specific regulation via a
# subclass/wrapper if that regulation's own practice expects more.
MIN_COHORT_SIZE_FOR_STATISTICAL_TEST = 30

# EEOC's four-fifths (80%) rule (29 CFR 1607.4(D)): a group's favorable-
# outcome rate below 80% of the highest-rate group's is evidence of
# adverse impact. The most established starting point in real
# regulatory practice -- proposed here as A defensible choice, not
# invented, and not the only one: four-fifths assumes a binary/
# categorical favorable-outcome definition and is a screening heuristic,
# not a legal determination on its own (same SCREENING_DISCLAIMER
# posture as every other check in this module). A standardized-mean-
# difference test would be more appropriate for a continuous outcome
# measure; not built here because C2's own outcome shape (approved/
# denied, resolved/unresolved) is categorical throughout this codebase.
FOUR_FIFTHS_THRESHOLD = 0.8


@dataclass(frozen=True)
class CohortDecision:
    """One decision's outcome plus its protected-characteristic
    estimate, already assembled by the caller from the sealed channel
    (SealedDemographicChannel.get_estimates_for_cohort) -- the unit
    check_statistical_outcome_equity operates on.

    favorable_outcome  -- the caller's own domain definition of
                          "favorable" (approved, resolved, granted,
                          ...) for this one decision. Domain-blind by
                          design, same as every other checker in this
                          module -- this function never guesses what
                          counts as favorable.
    group_distribution -- race/ethnicity -> probability, straight from
                          a sealed-channel estimate. A self-report is a
                          single-category distribution ({"hispanic":
                          1.0}); a BISG estimate is a real posterior
                          across all six categories. The same
                          probability-weighted arithmetic below handles
                          both without a special case.
    """

    subject_id: str
    favorable_outcome: bool
    group_distribution: Mapping[str, float]


def check_statistical_outcome_equity(
        cohort: "List[CohortDecision]", profile: RegulationCheckProfile,
        check_name: str = "statistical_outcome_equity_four_fifths",
        ) -> List[RegulatoryFinding]:
    """Four-fifths-rule disparate-impact screen across a COHORT of
    decisions. Returns [] when clean, one FLAG finding per group whose
    weighted favorable-outcome rate falls below 80% of the
    highest-rate group's, or one INDETERMINATE finding when the cohort
    is too small to mean anything (see MIN_COHORT_SIZE_FOR_STATISTICAL_TEST)
    -- never a spurious PASS or FLAG from too few observations.

    PROBABILITY-WEIGHTED, not hard-assigned: each decision contributes
    to EVERY group's rate in proportion to that group's estimated
    probability for that decision (group_distribution), rather than
    assigning each decision to its single most-likely group. This is
    deliberate -- hard-thresholding a probabilistic BISG estimate into
    one category before comparing rates introduces bias the weighted
    approach avoids, and it means self-reported (single-category) and
    BISG-estimated (full posterior) records combine in the cohort with
    no special-casing: a self-report is simply a distribution with all
    its mass on one category.

    For group r: weighted_rate(r) = sum_i(distribution_i[r] * favorable_i)
    / sum_i(distribution_i[r]) -- a probability-weighted mean favorable
    rate. Groups with negligible total weight in this cohort (fewer than
    1.0 effective decisions) are excluded from the comparison entirely
    -- not enough signal to say anything about that group here.
    """
    if len(cohort) < MIN_COHORT_SIZE_FOR_STATISTICAL_TEST:
        return [RegulatoryFinding(
            check=check_name,
            subject_id=f"cohort:{len(cohort)}",
            regulation=profile.regulation,
            action=ACTION_FLAG,
            classification="indeterminate_insufficient_cohort",
            score=0.0,
            evidence={
                "cohort_size": len(cohort),
                "minimum_required": MIN_COHORT_SIZE_FOR_STATISTICAL_TEST,
                "detail": "cohort is too small for a statistical disparate-impact "
                          "comparison to mean anything -- reporting indeterminate "
                          "rather than a spurious pass or flag",
            },
        )]

    weighted_favorable: Dict[str, float] = {}
    weighted_total: Dict[str, float] = {}
    for decision in cohort:
        for race, prob in decision.group_distribution.items():
            if prob <= 0:
                continue
            weighted_total[race] = weighted_total.get(race, 0.0) + prob
            if decision.favorable_outcome:
                weighted_favorable[race] = weighted_favorable.get(race, 0.0) + prob

    rates = {
        race: weighted_favorable.get(race, 0.0) / total
        for race, total in weighted_total.items()
        if total >= 1.0  # negligible-weight groups excluded, see docstring
    }
    if len(rates) < 2:
        return [RegulatoryFinding(
            check=check_name,
            subject_id=f"cohort:{len(cohort)}",
            regulation=profile.regulation,
            action=ACTION_FLAG,
            classification="indeterminate_insufficient_group_coverage",
            score=0.0,
            evidence={
                "cohort_size": len(cohort),
                "groups_with_sufficient_weight": sorted(rates),
                "detail": "fewer than two groups have enough estimated weight in "
                          "this cohort to compare -- cannot compute a four-fifths "
                          "ratio between groups that don't both have signal here",
            },
        )]

    highest_rate = max(rates.values())
    findings: List[RegulatoryFinding] = []
    if highest_rate > 0:
        for race, rate in sorted(rates.items()):
            ratio = rate / highest_rate
            if ratio < FOUR_FIFTHS_THRESHOLD:
                findings.append(RegulatoryFinding(
                    check=check_name,
                    subject_id=f"cohort:{len(cohort)}",
                    regulation=profile.regulation,
                    action=ACTION_FLAG,
                    classification="four_fifths_adverse_impact",
                    score=round(1.0 - ratio, 4),
                    evidence={
                        "group": race,
                        "group_favorable_rate": round(rate, 4),
                        "highest_group_favorable_rate": round(highest_rate, 4),
                        "ratio": round(ratio, 4),
                        "threshold": FOUR_FIFTHS_THRESHOLD,
                        "cohort_size": len(cohort),
                        "all_group_rates": {r: round(v, 4) for r, v in sorted(rates.items())},
                        "detail": f"estimated favorable-outcome rate for '{race}' "
                                  f"is {ratio:.1%} of the highest-rate group's, "
                                  f"below the {FOUR_FIFTHS_THRESHOLD:.0%} four-fifths "
                                  "threshold -- a screening signal for human "
                                  "review, not a legal determination of "
                                  "disparate impact",
                        "score_meaning": "0.0-1.0, how far below the four-fifths "
                                        "threshold this group's rate falls "
                                        "(1.0 - ratio); never a compliance "
                                        "probability",
                    },
                ))
    return findings


# ---------------------------------------------------------------------------
# Build item 3: C2 rollup
# ---------------------------------------------------------------------------

DIMENSION_KNOWN_BAD_VARIABLE_NAMES = "known_bad_variable_names"
DIMENSION_INPUT_AUTHORIZATION_TIER = "input_authorization_tier"
DIMENSION_NARRATIVE_LEGITIMACY = "narrative_legitimacy"
DIMENSION_STATISTICAL_OUTCOME_EQUITY = "statistical_outcome_equity"

C2_DIMENSIONS = (
    DIMENSION_KNOWN_BAD_VARIABLE_NAMES,
    DIMENSION_INPUT_AUTHORIZATION_TIER,
    DIMENSION_NARRATIVE_LEGITIMACY,
    DIMENSION_STATISTICAL_OUTCOME_EQUITY,
)

C2_PASS = "PASS"
C2_FLAG = "FLAG"
C2_INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class C2Rollup:
    """One decision's combined C2 (bias identification, detection, and
    mitigation) status across whichever dimensions were applicable.

    status                  -- PASS / FLAG / INDETERMINATE (see
                               rollup_c2_bias_identification).
    evaluated_dimensions    -- applicable dimensions that WERE run.
    not_evaluated_dimensions -- applicable dimensions still pending
                               (e.g. statistical_outcome_equity, always
                               here until it is separately built).
    flagged_dimensions      -- evaluated dimensions that produced at
                               least one finding.
    findings                -- every finding from every evaluated
                               dimension, concatenated, for the human
                               reviewer this status is FOR.
    """

    status: str
    evaluated_dimensions: Tuple[str, ...]
    not_evaluated_dimensions: Tuple[str, ...]
    flagged_dimensions: Tuple[str, ...]
    findings: Tuple[RegulatoryFinding, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "evaluated_dimensions": list(self.evaluated_dimensions),
            "not_evaluated_dimensions": list(self.not_evaluated_dimensions),
            "flagged_dimensions": list(self.flagged_dimensions),
            "findings": [f.as_dict() for f in self.findings],
        }


def rollup_c2_bias_identification(
        dimension_findings: "Mapping[str, List[RegulatoryFinding] | None]"
        ) -> C2Rollup:
    """Combine per-dimension findings into one C2 status, per the
    AND-rollup rule: PASS only if every applicable dimension passes;
    FLAG if any applicable dimension flags; INDETERMINATE if any
    applicable dimension hasn't been evaluated. INDETERMINATE takes
    precedence over FLAG when both occur -- a dimension that hasn't
    run yet means C2 as a whole is honestly unknown, regardless of how
    clean the dimensions that DID run came back. That is correct and
    intentional, not a bug to route around (see module docstring).

    dimension_findings maps each APPLICABLE dimension name (typically
    from C2_DIMENSIONS) to either:
      - a list of findings (possibly empty) if that dimension WAS
        evaluated for this decision, or
      - None if the dimension is applicable but has not been
        evaluated (e.g. DIMENSION_STATISTICAL_OUTCOME_EQUITY, which
        should always be passed as None until it is separately built,
        so the rollup stays honestly INDETERMINATE rather than
        misleadingly green).

    A dimension name this mapping has no key for at all is treated as
    NOT APPLICABLE to this decision and excluded entirely -- it counts
    toward neither PASS nor INDETERMINATE. Callers decide applicability
    (e.g. a decision with no free-text field at all may reasonably omit
    DIMENSION_NARRATIVE_LEGITIMACY rather than pass it as evaluated
    with zero findings -- though check_narrative_legitimacy already
    returns zero findings correctly in that case via Phase A/None
    narrative_field, so either omitting the key or including it with
    its own empty result is honest).
    """
    evaluated: List[str] = []
    not_evaluated: List[str] = []
    flagged: List[str] = []
    all_findings: List[RegulatoryFinding] = []

    for name, value in dimension_findings.items():
        if value is None:
            not_evaluated.append(name)
            continue
        evaluated.append(name)
        findings_list = list(value)
        all_findings.extend(findings_list)
        if findings_list:
            flagged.append(name)

    if not_evaluated:
        status = C2_INDETERMINATE
    elif flagged:
        status = C2_FLAG
    else:
        status = C2_PASS

    return C2Rollup(
        status=status,
        evaluated_dimensions=tuple(sorted(evaluated)),
        not_evaluated_dimensions=tuple(sorted(not_evaluated)),
        flagged_dimensions=tuple(sorted(flagged)),
        findings=tuple(all_findings),
    )
