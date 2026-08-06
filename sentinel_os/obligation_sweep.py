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

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import psycopg2.extras

# BISG (Bayesian Improved Surname Geocoding) demographic inference is currently
# RESEARCH MODE ONLY. IP/privacy attorney review pending before any production use.
# Defaults to False (disabled); only enabled if SENTINEL_BISG_RESEARCH_MODE=true.
BISG_RESEARCH_MODE_ENABLED = os.getenv("SENTINEL_BISG_RESEARCH_MODE", "false").lower() == "true"

from outcome_v1 import (
    OUTCOME_RESOLVED,
    OutcomeIntegrityError,
    OutcomeObligation,
    cohort_favorable,
)
from regulatory_cassette_interface import DecisionMaterial, material_from_ledger_row
from regulatory_checks import (
    CohortDecision,
    CohortInputDecision,
    GeographicCohortDecision,
    RegulationCheckProfile,
    check_correlation_based_proxy_detection,
    check_geographic_outcome_equity,
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
    """One (domain, obligation_kind) bucket, converted into the three
    typed shapes the C2 dimension-4, dimension-5, and dimension-6
    checks each need."""
    domain: str
    obligation_kind: str
    dimension_4_cohort: List[CohortDecision]
    dimension_5_cohort: List[CohortInputDecision]
    dimension_6_cohort: List[GeographicCohortDecision]
    skipped: List[SkippedObligation]
    total_resolved: int


def assemble_cohort(
        domain: str, obligation_kind: str,
        obligations: List[Mapping[str, Any]],
        group_distributions: Mapping[str, Mapping[str, float]],
        decision_materials: Mapping[str, DecisionMaterial],
        property_geography: Optional[Mapping[str, Mapping[str, Optional[str]]]] = None,
        ) -> AssembledCohort:
    """Turn one bucket of resolved obligations into the three cohort
    shapes check_statistical_outcome_equity (dimension 4),
    check_correlation_based_proxy_detection (dimension 5), and
    check_geographic_outcome_equity (dimension 6) each need.

    group_distributions, decision_materials, and property_geography are
    all pre-fetched by the caller, keyed by subject_of(obligation),
    decision_hash, and decision_hash respectively -- this function does
    no I/O, so it needs no live sealed channel, ledger connection, or
    geocoder to test. property_geography defaults to None (treated as
    empty): a caller not yet fetching geography still gets dimension
    4/5 exactly as before -- dimension 6 just comes back empty, not an
    error.

    The three dimensions have DIFFERENT data requirements and are
    evaluated INDEPENDENTLY: an obligation can enter dimension 4's
    cohort without entering dimension 5's or 6's (missing decision
    material or geography respectively), but 5 and 6 both also need a
    resolved dimension-4 entry first (favorable outcome known) -- so a
    per-obligation skip reason is specific to which dimension it
    affects, not a single pass/fail gate.
    """
    property_geography = property_geography or {}
    dim4: List[CohortDecision] = []
    dim5: List[CohortInputDecision] = []
    dim6: List[GeographicCohortDecision] = []
    skipped: List[SkippedObligation] = []
    for obligation in obligations:
        subject = subject_of(obligation)
        obligation_id = str(obligation.get("obligation_id", subject))
        decision_hash = str(obligation["decision_hash"])
        distribution = group_distributions.get(subject)

        favorable_outcome = None
        favorable_error: Optional[str] = None
        try:
            favorable_outcome = cohort_favorable(_to_outcome_obligation(obligation))
        except OutcomeIntegrityError as exc:
            favorable_error = "; ".join(exc.violations)

        # Dimension 4: needs BOTH a valid favorable call and a
        # demographic estimate.
        if favorable_error is not None:
            skipped.append(SkippedObligation(
                obligation_id, f"not usable for dimension 4: {favorable_error}"))
        elif distribution is None:
            skipped.append(SkippedObligation(
                obligation_id,
                "no protected-characteristic estimate on file for this subject"))
        else:
            dim4.append(CohortDecision(
                subject_id=subject, favorable_outcome=favorable_outcome,
                group_distribution=distribution))

        # Dimension 5: needs a demographic estimate + decision material,
        # NOT a favorable call -- it screens input VALUES, not outcomes.
        material = decision_materials.get(decision_hash)
        if distribution is not None:
            if material is not None:
                dim5.append(CohortInputDecision(
                    subject_id=subject, input_fields=material.input_fields,
                    group_distribution=distribution))
            else:
                skipped.append(SkippedObligation(
                    obligation_id,
                    "not usable for dimension 5: no decision material found "
                    "on the primary ledger for this obligation's decision_hash"))
        # else: the dimension-4 skip above already covers "no
        # protected-characteristic estimate" -- dimension 5 needs that
        # same estimate, so a second identical skip would be noise.

        # Dimension 6: needs a valid favorable call + geography, NOT a
        # demographic estimate. Geography is a known fact hard-assigned
        # from the property address, not a probability distribution like
        # dimensions 4/5's group estimate -- coupling it to a missing
        # demographic estimate was a real gap (fixed 2026-08-01): it
        # silently zeroed dimension 6 even when geography was on file
        # and the outcome was known.
        if favorable_error is None:
            geography = property_geography.get(decision_hash)
            zip_code = (geography or {}).get("zip")
            county_fips = (geography or {}).get("county_fips")
            if zip_code or county_fips:
                dim6.append(GeographicCohortDecision(
                    subject_id=subject, favorable_outcome=favorable_outcome,
                    zip_code=zip_code, county_fips=county_fips))
            else:
                skipped.append(SkippedObligation(
                    obligation_id,
                    "not usable for dimension 6: no property ZIP or county "
                    "resolved for this obligation's decision_hash"))
        # else: the dimension-4 skip above already covers why -- dimension
        # 6 needs the exact same favorable call dimension 4 does.
    return AssembledCohort(
        domain=domain, obligation_kind=obligation_kind,
        dimension_4_cohort=dim4, dimension_5_cohort=dim5,
        dimension_6_cohort=dim6,
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
    dimension_6_cohort_size: int
    dimension_4_findings: List[Dict[str, Any]]
    dimension_5_findings: List[Dict[str, Any]]
    dimension_6_findings: List[Dict[str, Any]]
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
            "dimension_6_cohort_size": self.dimension_6_cohort_size,
            "dimension_4_findings": self.dimension_4_findings,
            "dimension_5_findings": self.dimension_5_findings,
            "dimension_6_findings": self.dimension_6_findings,
            "skipped": [{"obligation_id": s.obligation_id, "reason": s.reason}
                       for s in self.skipped],
        }


def review_cohort(assembled: AssembledCohort,
                  profile: RegulationCheckProfile) -> CohortEquityReview:
    """Run all three cohort-level C2 checks against one assembled cohort.

    All three checks are called regardless of cohort size -- each
    reports its own INDETERMINATE finding when its cohort is below the
    relevant minimum rather than this function pre-filtering small
    cohorts out. A domain/obligation_kind pair with too few resolved
    obligations to say anything yet is itself reportable state, not
    silence.
    """
    dim4_findings = check_statistical_outcome_equity(
        assembled.dimension_4_cohort, profile)
    dim5_findings = check_correlation_based_proxy_detection(
        assembled.dimension_5_cohort, profile)
    dim6_findings = check_geographic_outcome_equity(
        assembled.dimension_6_cohort, profile)
    return CohortEquityReview(
        domain=assembled.domain,
        obligation_kind=assembled.obligation_kind,
        total_resolved=assembled.total_resolved,
        dimension_4_cohort_size=len(assembled.dimension_4_cohort),
        dimension_5_cohort_size=len(assembled.dimension_5_cohort),
        dimension_6_cohort_size=len(assembled.dimension_6_cohort),
        dimension_4_findings=[f.as_dict() for f in dim4_findings],
        dimension_5_findings=[f.as_dict() for f in dim5_findings],
        dimension_6_findings=[f.as_dict() for f in dim6_findings],
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


def fetch_property_geography(
        geocoder, decision_materials: Mapping[str, DecisionMaterial],
        address_field: str = "loan_property_address",
        ) -> Dict[str, Dict[str, Optional[str]]]:
    """Resolve each decision's property address to ZIP + county FIPS,
    keyed by decision_hash. Reuses the SAME CensusGeocoder call the
    BISG race estimate already makes for this address
    (bisg_estimator.CensusGeocoder.geocode_county_fips) plus a plain
    text parse for ZIP (bisg_estimator.extract_zip) -- no new data
    source, one geocode per address.

    BISG RESEARCH MODE: This function is currently disabled by default
    (SENTINEL_BISG_RESEARCH_MODE must be explicitly set to "true").
    IP/privacy attorney review is pending before production deployment.
    When disabled, returns all-None results for all addresses, which
    the cohort assembly properly reports as skips (no silent drops).

    address_field is a parameter, not a hardcoded import of
    cassettes.mortgage_cassette.PROPERTY_ADDRESS_FIELD: this module
    stays domain-agnostic, same posture as every other cohort-assembly
    function here, so a future loan-type cassette that declares its
    property address under a different input_fields key can reuse this
    without a code change here.

    A decision_hash with no value under address_field at all is simply
    absent from the result (nothing to geocode). A decision_hash WITH
    an address is always present in the result, even if neither zip nor
    county_fips resolves -- assemble_cohort's skip handling is what
    turns "present but both None" into a reported skip, not this
    function silently omitting it.
    """
    result: Dict[str, Dict[str, Optional[str]]] = {}

    # If BISG research mode is not explicitly enabled, return all-None results.
    # Cohort assembly will report these as skips (no silent drops).
    if not BISG_RESEARCH_MODE_ENABLED:
        for decision_hash, material in decision_materials.items():
            address = material.input_fields.get(address_field)
            if not address:
                continue
            result[decision_hash] = {"zip": None, "county_fips": None}
        return result

    # BISG research mode enabled: perform actual geocoding.
    from bisg_estimator import extract_zip

    for decision_hash, material in decision_materials.items():
        address = material.input_fields.get(address_field)
        if not address:
            continue
        result[decision_hash] = {
            "zip": extract_zip(str(address)),
            "county_fips": geocoder.geocode_county_fips(str(address)),
        }
    return result


def sweep(twin_client, replica_id: str, ledger_conn, sealed_channel,
          profile: RegulationCheckProfile, geocoder=None,
          ) -> List[CohortEquityReview]:
    """The whole sweep, wired to real I/O: fetch resolved obligations
    from the twin, bucket them, fetch what each bucket needs, and
    return one CohortEquityReview per bucket -- including buckets too
    small to test (see review_cohort).

    BISG RESEARCH MODE: Geographic equity testing is currently disabled by
    default (SENTINEL_BISG_RESEARCH_MODE must be explicitly set to "true").
    IP/privacy attorney review is pending before production deployment.
    When disabled, geocoder stays None and fetch_property_geography() returns
    all-None results, which the cohort assembly properly reports as skips.

    geocoder defaults to a real bisg_estimator.CensusGeocoder() when not
    supplied (and BISG research mode is enabled) -- the same live, key-free
    geocoding service dimension 4's BISG estimate already depends on. Pass a
    stub/mock in tests to avoid a real network call, same posture as
    sealed_channel and twin_client.
    """
    if geocoder is None and BISG_RESEARCH_MODE_ENABLED:
        from bisg_estimator import CensusGeocoder
        geocoder = CensusGeocoder()
    obligations = fetch_resolved_obligations(twin_client, replica_id)
    buckets = bucket_resolved_obligations(obligations)
    reviews: List[CohortEquityReview] = []
    for (domain, obligation_kind), bucket_obligations in sorted(buckets.items()):
        subjects = {subject_of(o) for o in bucket_obligations}
        decision_hashes = {str(o["decision_hash"]) for o in bucket_obligations}
        group_distributions = fetch_group_distributions(sealed_channel, subjects)
        decision_materials = fetch_decision_materials(ledger_conn, decision_hashes)
        property_geography = fetch_property_geography(geocoder, decision_materials)
        assembled = assemble_cohort(domain, obligation_kind, bucket_obligations,
                                    group_distributions, decision_materials,
                                    property_geography)
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


def fetch_latest_cohort_review(twin_client, replica_id: str, domain: str,
                               obligation_kind: str) -> Optional[Dict[str, Any]]:
    """The most recently recorded cohort_equity_review for one (domain,
    obligation_kind) cohort, or None if the sweep has never run for it
    yet. Reads the twin's existing GET /cohort-reviews (already ordered
    oldest-first) and takes the last entry -- no new twin-side
    mechanism, just a convenience read over what's already there.

    NOT called anywhere in the live judgment path today (Wm, 2026-07-29:
    deliberately deferred -- regulatory_deck.judge()/explain() make NO
    external network call today, and reaching out to the twin on every
    live decision would be the first; that's a real latency/reliability
    trade worth its own decision once real sweep data exists, not
    something to wire in as a side effect of building the sweep
    itself). This function exists so that FUTURE wiring has something
    ready to call -- it is the fetch, not the wiring.
    """
    resp = twin_client.get(f"/replica/{replica_id}/cohort-reviews",
                           params={"domain": domain, "obligation_kind": obligation_kind})
    resp.raise_for_status()
    reviews = resp.json()["reviews"]
    return reviews[-1] if reviews else None


# ---------------------------------------------------------------------------
# CLI entry point -- triggers one sweep run. Deliberately a standalone
# script, not an HTTP endpoint: this runs on the PRIMARY (see module
# docstring) and needs both a real ledger connection and a real twin
# client, and the codebase's own precedent for exactly this shape of job
# (primary-side, needs the twin, periodic/on-demand) is a script --
# twin_sync_worker.py and twin_migrate.py both work this way, not as
# endpoints. Whatever actually calls this on a schedule (cron, a k8s
# CronJob, or a human running it by hand) is a deployment decision this
# script deliberately doesn't make for you.
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    import json as _json
    import os

    import httpx
    import psycopg2 as _psycopg2

    from regulatory_cassettes.cfpb_reg_b import CFPB_REG_B_PROFILE
    from sealed_demographic_channel import SealedDemographicChannel

    ap = argparse.ArgumentParser(
        description="Run one C2 cohort-fairness sweep (dimensions 4/5/6) "
                    "and record the results on the twin.")
    ap.add_argument("--ledger-dsn", required=True,
                    help="psycopg2 DSN for the primary's own ledger")
    ap.add_argument("--receiver-url", required=True,
                    help="Base URL of the twin's receiver API")
    ap.add_argument("--replica-id", required=True)
    ap.add_argument("--ship-token", default=os.environ.get("SENTINEL_SHIP_TOKEN"),
                    help="Bearer token for the twin's ship-token auth "
                         "(falls back to the SENTINEL_SHIP_TOKEN env var). "
                         "Required -- every twin route this script calls is "
                         "auth-gated (see AC-13).")
    ap.add_argument("--sealed-channel-host", default="localhost")
    ap.add_argument("--sealed-channel-port", type=int, default=5432)
    ap.add_argument("--sealed-channel-dbname", default="iceberg")
    ap.add_argument("--sealed-channel-user", default="iceberg")
    ap.add_argument("--sealed-channel-password", default="iceberg")
    ap.add_argument("--domain", default=None,
                    help="Restrict the sweep to one domain's cohorts "
                         "(default: every domain with resolved obligations)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and print reviews but do not record "
                         "them on the twin")
    args = ap.parse_args()
    if not args.ship_token:
        ap.error("--ship-token is required (or set SENTINEL_SHIP_TOKEN) -- "
                 "every twin route this script calls is auth-gated (AC-13)")

    ledger_conn = _psycopg2.connect(args.ledger_dsn)
    twin_client = httpx.Client(
        base_url=args.receiver_url, timeout=30.0,
        headers={"Authorization": f"Bearer {args.ship_token}"})
    channel = SealedDemographicChannel(
        host=args.sealed_channel_host, port=args.sealed_channel_port,
        dbname=args.sealed_channel_dbname, user=args.sealed_channel_user,
        password=args.sealed_channel_password)

    try:
        reviews = sweep(twin_client, args.replica_id, ledger_conn, channel,
                        CFPB_REG_B_PROFILE)
        if args.domain is not None:
            reviews = [r for r in reviews if r.domain == args.domain]
        summary = {
            "cohorts_swept": len(reviews),
            "reviews": [r.as_dict() for r in reviews],
        }
        if not args.dry_run:
            import time as _time
            recorded = record_reviews(twin_client, args.replica_id, reviews,
                                      swept_at=_time.time())
            summary["recorded"] = recorded
        print(_json.dumps(summary, indent=2, default=str))
    finally:
        ledger_conn.close()
        twin_client.close()


if __name__ == "__main__":
    main()
