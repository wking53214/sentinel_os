"""
obligation_sweep.py -- the return path for resolved obligations.

WHERE THIS RUNS, AND WHY
-------------------------
This runs on the PRIMARY, not the twin. Two different things a cohort
review needs live in two different places:

  - Obligation RESOLUTION STATE (favorable/not, when it resolved) lives
    on the twin, per outcome_v1's own design: obligations are the
    twin's independent record of what is owed and how it turned out.
  - Decision INPUT FIELDS (needed for dimension 5's proxy-correlation
    screen) already live durably on the primary's own ledger, via
    regulatory_cassette_interface.material_from_ledger_row.

The twin never computes a cassette's judgment itself anywhere else in
this codebase -- it receives and independently verifies. Shipping raw
decision input fields to the twin in the clear just to let it compute
dimension 5 there would be a much bigger, more sensitive data-sharing
change than domain ever was (input fields can be real applicant data,
not a business-line label), and would break that pattern. So: this
module reads resolved obligations FROM the twin's existing read API,
reads input fields from the primary's own ledger, computes both C2
findings here, and the result is handed to the twin's cohort-review
endpoint afterward purely for tamper-evident storage -- the twin still
never computes anything, only stores and can independently re-verify.

WHAT COUNTS AS A COHORT
------------------------
(domain, obligation_kind), not obligation_kind alone. Two unrelated
cassettes can legitimately choose the same obligation_kind string
("loan_performance" means something different to two different
lenders using two different cassettes); domain is what keeps their
cohorts from silently merging. See twin_receiver.py's domain field and
twin_custody.domain_from_cassette_version for how domain gets to the
twin in the first place.

NO SILENT SKIPS
----------------
An obligation that can't enter a cohort test -- no protected-
characteristic estimate on file, resolved but genuinely ambiguous, no
decision material found -- is reported in the review's `skipped` list
with why, never quietly dropped from the count. A cohort too small to
test (see MIN_COHORT_SIZE_FOR_STATISTICAL_TEST) is not filtered out
before the checks run; check_statistical_outcome_equity and
check_correlation_based_proxy_detection each report their own
INDETERMINATE finding for that case, which is itself the honest answer
and belongs in the review, not a bucket that silently never appears.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Set, Tuple

import psycopg2.extras

from outcome_v1 import (
    OUTCOME_RESOLVED,
    OutcomeIntegrityError,
    OutcomeObligation,
    to_cohort_decision,
)
from regulatory_cassette_interface import DecisionMaterial, material_from_ledger_row
from regulatory_checks import (
    CohortDecision,
    CohortInputDecision,
    RegulationCheckProfile,
    check_correlation_based_proxy_detection,
    check_statistical_outcome_equity,
)

# ---------------------------------------------------------------------------
# Bucketing: pure, no I/O.
# ---------------------------------------------------------------------------


def cohort_key(obligation: Mapping[str, Any]) -> Tuple[str, str]:
    """The bucket a resolved obligation belongs to."""
    return (str(obligation.get("domain") or "unknown"),
            str(obligation.get("obligation_kind") or "unknown"))


def bucket_resolved_obligations(
        obligations: List[Mapping[str, Any]],
        ) -> Dict[Tuple[str, str], List[Mapping[str, Any]]]:
    """Group RESOLVED obligations by (domain, obligation_kind).

    Refuses (does not silently filter) an obligation that isn't
    RESOLVED -- the caller is expected to have already filtered to
    RESOLVED (fetch_resolved_obligations does this against the twin);
    receiving a non-RESOLVED one here means the caller's filter is
    broken, and that is cheaper to catch here than downstream where
    to_cohort_decision would refuse it with a less specific error.
    """
    buckets: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    for obligation in obligations:
        if obligation.get("state") != OUTCOME_RESOLVED:
            raise ValueError(
                f"bucket_resolved_obligations received a non-RESOLVED "
                f"obligation ({obligation.get('obligation_id')!r}, state="
                f"{obligation.get('state')!r}) -- filter to RESOLVED before "
                f"calling this")
        buckets.setdefault(cohort_key(obligation), []).append(obligation)
    return buckets


def _to_outcome_obligation(obligation: Mapping[str, Any]) -> OutcomeObligation:
    """Reconstruct the typed OutcomeObligation the outcome_v1 functions
    expect from the plain dict the twin's read API returns."""
    return OutcomeObligation(
        obligation_id=str(obligation["obligation_id"]),
        decision_hash=str(obligation["decision_hash"]),
        domain=str(obligation["domain"]),
        obligation_kind=str(obligation["obligation_kind"]),
        opened_at=float(obligation["opened_at"]),
        expected_by=float(obligation["expected_by"]),
        state=obligation["state"],
        reason_code=obligation.get("reason_code"),
        resolved_at=obligation.get("resolved_at"),
        resolved_value=obligation.get("resolved_value"),
        resolution_provenance=obligation.get("resolution_provenance"),
        resolution_method=obligation.get("resolution_method"),
        favorable=obligation.get("favorable"),
        subject_id=obligation.get("subject_id"),
        detail=dict(obligation.get("detail") or {}),
    )


def subject_of(obligation: Mapping[str, Any]) -> str:
    """The subject identity a cohort test keys on: the obligation's own
    subject_id if it has one (the twin does not derive one today --
    see twin_receiver.derive_obligations), else its decision_hash. Same
    fallback to_cohort_decision itself uses."""
    return str(obligation.get("subject_id") or obligation["decision_hash"])


# ---------------------------------------------------------------------------
# Assembly: pure, no I/O -- takes already-fetched lookups as plain dicts
# so this stays testable without a live sealed channel or ledger.
# ---------------------------------------------------------------------------


@dataclass
class SkippedObligation:
    """One obligation that could not enter a cohort test, with why.
    Reported, never silently dropped."""
    obligation_id: str
    reason: str


@dataclass
class AssembledCohort:
    """One (domain, obligation_kind) bucket, converted into the two
    typed shapes the C2 dimension-4 and dimension-5 checks each need."""
    domain: str
    obligation_kind: str
    dimension_4_cohort: List[CohortDecision]
    dimension_5_cohort: List[CohortInputDecision]
    skipped: List[SkippedObligation]
    total_resolved: int


def assemble_cohort(
        domain: str, obligation_kind: str,
        obligations: List[Mapping[str, Any]],
        group_distributions: Mapping[str, Mapping[str, float]],
        decision_materials: Mapping[str, DecisionMaterial],
        ) -> AssembledCohort:
    """Turn one bucket of resolved obligations into the two cohort
    shapes check_statistical_outcome_equity (dimension 4) and
    check_correlation_based_proxy_detection (dimension 5) each need.

    group_distributions and decision_materials are both pre-fetched by
    the caller, keyed by subject_of(obligation) and decision_hash
    respectively -- this function does no I/O, so it needs no live
    sealed channel or ledger connection to test.

    The two dimensions have DIFFERENT data requirements and are
    evaluated INDEPENDENTLY: an obligation can enter dimension 4's
    cohort without entering dimension 5's (missing decision material),
    but never the reverse (dimension 5 also needs a group_distribution,
    same as dimension 4) -- so a per-obligation skip reason is specific
    to which dimension it affects, not a single pass/fail gate.
    """
    dim4: List[CohortDecision] = []
    dim5: List[CohortInputDecision] = []
    skipped: List[SkippedObligation] = []
    for obligation in obligations:
        subject = subject_of(obligation)
        obligation_id = str(obligation.get("obligation_id", subject))
        distribution = group_distributions.get(subject)
        if distribution is None:
            skipped.append(SkippedObligation(
                obligation_id,
                "no protected-characteristic estimate on file for this subject"))
            continue
        try:
            dim4.append(to_cohort_decision(
                _to_outcome_obligation(obligation), distribution))
        except OutcomeIntegrityError as exc:
            skipped.append(SkippedObligation(
                obligation_id,
                f"not usable for dimension 4: {'; '.join(exc.violations)}"))
        material = decision_materials.get(str(obligation["decision_hash"]))
        if material is not None:
            dim5.append(CohortInputDecision(
                subject_id=subject,
                input_fields=material.input_fields,
                group_distribution=distribution,
            ))
        else:
            skipped.append(SkippedObligation(
                obligation_id,
                "not usable for dimension 5: no decision material found on "
                "the primary ledger for this obligation's decision_hash"))
    return AssembledCohort(
        domain=domain, obligation_kind=obligation_kind,
        dimension_4_cohort=dim4, dimension_5_cohort=dim5,
        skipped=skipped, total_resolved=len(obligations),
    )


@dataclass
class CohortEquityReview:
    """The full result of sweeping one (domain, obligation_kind)
    cohort -- what a caller records on the twin's chain as a
    cohort_equity_review."""
    domain: str
    obligation_kind: str
    total_resolved: int
    dimension_4_cohort_size: int
    dimension_5_cohort_size: int
    dimension_4_findings: List[Dict[str, Any]]
    dimension_5_findings: List[Dict[str, Any]]
    skipped: List[SkippedObligation] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe form -- what the twin's cohort-review endpoint
        stores and hashes."""
        return {
            "domain": self.domain,
            "obligation_kind": self.obligation_kind,
            "total_resolved": self.total_resolved,
            "dimension_4_cohort_size": self.dimension_4_cohort_size,
            "dimension_5_cohort_size": self.dimension_5_cohort_size,
            "dimension_4_findings": self.dimension_4_findings,
            "dimension_5_findings": self.dimension_5_findings,
            "skipped": [{"obligation_id": s.obligation_id, "reason": s.reason}
                       for s in self.skipped],
        }


def review_cohort(assembled: AssembledCohort,
                  profile: RegulationCheckProfile) -> CohortEquityReview:
    """Run both cohort-level C2 checks against one assembled cohort.

    Both checks are called regardless of cohort size -- each reports
    its own INDETERMINATE finding when its cohort is below
    MIN_COHORT_SIZE_FOR_STATISTICAL_TEST rather than this function
    pre-filtering small cohorts out. A domain/obligation_kind pair with
    too few resolved obligations to say anything yet is itself
    reportable state, not silence.
    """
    dim4_findings = check_statistical_outcome_equity(
        assembled.dimension_4_cohort, profile)
    dim5_findings = check_correlation_based_proxy_detection(
        assembled.dimension_5_cohort, profile)
    return CohortEquityReview(
        domain=assembled.domain,
        obligation_kind=assembled.obligation_kind,
        total_resolved=assembled.total_resolved,
        dimension_4_cohort_size=len(assembled.dimension_4_cohort),
        dimension_5_cohort_size=len(assembled.dimension_5_cohort),
        dimension_4_findings=[f.as_dict() for f in dim4_findings],
        dimension_5_findings=[f.as_dict() for f in dim5_findings],
        skipped=assembled.skipped,
    )


# ---------------------------------------------------------------------------
# I/O wrappers -- thin, and each independently swappable in a test.
# ---------------------------------------------------------------------------


def fetch_resolved_obligations(twin_client, replica_id: str) -> List[Dict[str, Any]]:
    """Pull the current obligation set from the twin's own read API and
    return only the RESOLVED ones. The twin is the sole record of
    resolution state (decision 4: obligations live on the twin, not the
    primary) -- this never reads a primary-side obligation table
    because there isn't one. twin_client is anything with an httpx-
    shaped .get(path) -> Response (a real httpx.Client against the
    twin's base_url, or a FastAPI TestClient in tests)."""
    resp = twin_client.get(f"/replica/{replica_id}/obligations")
    resp.raise_for_status()
    return [o for o in resp.json()["obligations"] if o["state"] == OUTCOME_RESOLVED]


def fetch_group_distributions(sealed_channel, subject_ids: Set[str],
                              ) -> Dict[str, Dict[str, float]]:
    """One lookup per subject via the sealed channel's per-subject
    read (not the cohort-batch read, which is keyed by the channel's
    OWN cohort_key at estimate time -- a concept this sweep's
    domain/obligation_kind bucketing does not share). A subject with no
    recorded estimate is simply absent from the result; the caller
    reports that as a skip, not an error."""
    result: Dict[str, Dict[str, float]] = {}
    for subject_id in subject_ids:
        estimate = sealed_channel.get_estimate_for_subject(subject_id)
        if estimate is not None:
            result[subject_id] = dict(estimate.estimate)
    return result


def fetch_decision_materials(ledger_conn, decision_hashes: Set[str],
                             ) -> Dict[str, DecisionMaterial]:
    """Look up decisions by hash on the primary's own ledger and adapt
    each into DecisionMaterial for dimension 5's input_fields. A hash
    with no matching row (e.g. retention/deletion since the decision
    was made) is simply absent from the result; the caller reports that
    as a skip, not a fabricated empty input_fields."""
    if not decision_hashes:
        return {}
    result: Dict[str, DecisionMaterial] = {}
    with ledger_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, current_hash, reason, decision_output, input_data,
                      cassette_version, record_kind
               FROM ledger_entries
               WHERE current_hash = ANY(%s) AND record_kind = 'governance_decision'""",
            (list(decision_hashes),))
        for row in cur.fetchall():
            result[row["current_hash"]] = material_from_ledger_row(dict(row))
    return result


def sweep(twin_client, replica_id: str, ledger_conn, sealed_channel,
          profile: RegulationCheckProfile) -> List[CohortEquityReview]:
    """The whole sweep, wired to real I/O: fetch resolved obligations
    from the twin, bucket them, fetch what each bucket needs, and
    return one CohortEquityReview per bucket -- including buckets too
    small to test (see review_cohort)."""
    obligations = fetch_resolved_obligations(twin_client, replica_id)
    buckets = bucket_resolved_obligations(obligations)
    reviews: List[CohortEquityReview] = []
    for (domain, obligation_kind), bucket_obligations in sorted(buckets.items()):
        subjects = {subject_of(o) for o in bucket_obligations}
        decision_hashes = {str(o["decision_hash"]) for o in bucket_obligations}
        group_distributions = fetch_group_distributions(sealed_channel, subjects)
        decision_materials = fetch_decision_materials(ledger_conn, decision_hashes)
        assembled = assemble_cohort(domain, obligation_kind, bucket_obligations,
                                    group_distributions, decision_materials)
        reviews.append(review_cohort(assembled, profile))
    return reviews


def record_reviews(twin_client, replica_id: str,
                   reviews: List[CohortEquityReview], swept_at: float,
                   ) -> List[Dict[str, Any]]:
    """POST each computed review to the twin's cohort-reviews endpoint
    for tamper-evident storage. Deliberately separate from sweep()
    itself: computing a review and recording it are two different
    steps with two different failure modes (a network error recording
    review 3 of 5 should not mean reviews 1-2 were never computed), and
    a caller may want to inspect reviews before deciding to record them
    at all -- e.g. a dry run."""
    results = []
    for review in reviews:
        body = review.as_dict()
        body["swept_at"] = swept_at
        resp = twin_client.post(f"/replica/{replica_id}/cohort-reviews", json=body)
        resp.raise_for_status()
        results.append(resp.json())
    return results
