"""Retention obligations (Part 3) -- pure logic, no Postgres needed.

The central property under test is that absence is never read as
compliance, in every direction it could be: missing ingest, missing
deletion, unparseable timestamps.
"""
from datetime import datetime, timedelta, timezone

from contract_cassette import (
    TERM_EGRESS_PROHIBITED,
    TERM_RETENTION_MAX_DAYS,
    ContractCassette,
    ContractTerm,
)
from contract_retention import (
    INDET_NO_INGEST_RECORD,
    INDET_NO_RETENTION_TERM,
    INDET_UNPARSEABLE_DELETION_TIME,
    INDET_UNPARSEABLE_INGEST_TIME,
    SCOPE_ACTIVE,
    SCOPE_BACKUP,
    STATUS_DELETED_ON_TIME,
    STATUS_INDETERMINATE,
    STATUS_OVERDUE,
    STATUS_WITHIN_TERM,
    assess_counterparty,
    retention_status,
    summarize,
)

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _contract(counterparty="acme-bank", max_days=90, backup_max_days=None,
              retention=True):
    params = {"max_days": max_days}
    if backup_max_days is not None:
        params["backup_max_days"] = backup_max_days
    terms = ([ContractTerm(TERM_RETENTION_MAX_DAYS, params)] if retention
             else [ContractTerm(TERM_EGRESS_PROHIBITED, {"purpose": "resale"})])

    class _Lens(ContractCassette):
        def get_counterparty_id(self):
            return counterparty

        def get_contract_reference(self):
            return f"DPA-{counterparty}"

        def get_contract_version(self):
            return "1.0.0"

        def get_terms(self):
            return tuple(terms)

    return _Lens()


def _ingest(ingest_id="ING-1", days_ago=10):
    received = (NOW - timedelta(days=days_ago)).isoformat()
    return {"record_kind": "contract_ingest", "current_hash": f"h-{ingest_id}",
            "data": {"ingest_id": ingest_id, "received_at": received,
                     "counterparty": "acme-bank", "data_scope": "records"}}


def _deletion(ingest_id="ING-1", days_ago=1, scope=SCOPE_ACTIVE,
              method="hard_delete_from_object_store"):
    return {"record_kind": "contract_deletion",
            "current_hash": f"d-{ingest_id}-{scope}",
            "data": {"ingest_id": ingest_id, "scope": scope, "method": method,
                     "stamp": "attested", "counterparty": "acme-bank",
                     "deleted_at": (NOW - timedelta(days=days_ago)).isoformat()}}


# -- the four statuses -------------------------------------------------

def test_young_record_with_no_deletion_is_within_term():
    result = retention_status(_ingest(days_ago=10), [], 90, None, NOW)
    assert result["status"] == STATUS_WITHIN_TERM


def test_deletion_before_the_horizon_is_deleted_on_time():
    result = retention_status(_ingest(days_ago=30), [_deletion(days_ago=5)],
                              90, None, NOW)
    assert result["status"] == STATUS_DELETED_ON_TIME


def test_horizon_passed_with_no_deletion_is_overdue():
    result = retention_status(_ingest(days_ago=200), [], 90, None, NOW)
    assert result["status"] == STATUS_OVERDUE
    assert result["reason"] == "horizon_passed_with_no_deletion_attested"


def test_deletion_after_the_horizon_is_overdue_not_a_pass():
    """Attested, but late. The horizon is the term; doing it eventually
    is not doing it on time."""
    result = retention_status(_ingest(days_ago=200),
                              [_deletion(days_ago=1)], 90, None, NOW)
    assert result["status"] == STATUS_OVERDUE
    assert result["reason"] == "deleted_after_the_contract_horizon"


# -- absence is never compliance --------------------------------------

def test_missing_ingest_record_is_indeterminate_not_compliant():
    result = retention_status(None, [_deletion()], 90, None, NOW)
    assert result["status"] == STATUS_INDETERMINATE
    assert result["reason"] == INDET_NO_INGEST_RECORD


def test_contract_with_no_retention_term_is_indeterminate_not_unlimited():
    result = retention_status(_ingest(), [], None, None, NOW)
    assert result["status"] == STATUS_INDETERMINATE
    assert result["reason"] == INDET_NO_RETENTION_TERM


def test_unparseable_ingest_time_is_indeterminate():
    row = _ingest()
    row["data"]["received_at"] = "sometime in June"
    result = retention_status(row, [], 90, None, NOW)
    assert result["status"] == STATUS_INDETERMINATE
    assert result["reason"] == INDET_UNPARSEABLE_INGEST_TIME


def test_unparseable_deletion_time_is_indeterminate_not_on_time():
    deletion = _deletion()
    deletion["data"]["deleted_at"] = "recently"
    result = retention_status(_ingest(days_ago=30), [deletion], 90, None, NOW)
    assert result["status"] == STATUS_INDETERMINATE
    assert result["reason"] == INDET_UNPARSEABLE_DELETION_TIME


def test_deletion_for_an_ingest_that_was_never_recorded_surfaces_not_dropped():
    """A deletion referencing nothing is a discrepancy worth showing,
    not a row to skip."""
    findings = assess_counterparty(_contract(), [], [_deletion("ING-GHOST")],
                                   now=NOW)
    assert [f.status for f in findings] == [STATUS_INDETERMINATE]
    assert findings[0].ingest_id == "ING-GHOST"


# -- deletion provenance ----------------------------------------------

def test_a_deletion_is_stamped_attested_never_verified():
    result = retention_status(_ingest(days_ago=30), [_deletion(days_ago=5)],
                              90, None, NOW)
    assert result["stamp"] == "attested"


def test_the_deletion_method_is_carried_into_the_finding():
    """An attestation that will not describe its own mechanism is the
    failure event_v1 already refuses for an unnamed estimate."""
    result = retention_status(_ingest(days_ago=30), [_deletion(days_ago=5)],
                              90, None, NOW)
    assert result["evidence"]["method"] == "hard_delete_from_object_store"


# -- backups -----------------------------------------------------------

def test_backups_fall_under_the_main_clock_when_the_contract_is_silent():
    """Archived is retained. A contract that says nothing about backups
    gets the strict reading."""
    findings = assess_counterparty(_contract(max_days=90),
                                   [_ingest(days_ago=200)], [], now=NOW)
    assert [f.scope for f in findings] == [SCOPE_ACTIVE]
    assert findings[0].status == STATUS_OVERDUE


def test_declared_backup_carve_out_gets_its_own_longer_clock():
    contract = _contract(max_days=90, backup_max_days=365)
    findings = assess_counterparty(contract, [_ingest(days_ago=200)], [],
                                   now=NOW)
    by_scope = {f.scope: f for f in findings}
    assert by_scope[SCOPE_ACTIVE].status == STATUS_OVERDUE
    assert by_scope[SCOPE_BACKUP].status == STATUS_WITHIN_TERM
    assert by_scope[SCOPE_BACKUP].max_days == 365


def test_backup_deletion_is_measured_against_the_backup_clock():
    result = retention_status(_ingest(days_ago=300),
                              [_deletion(days_ago=1, scope=SCOPE_BACKUP)],
                              90, 365, NOW, scope=SCOPE_BACKUP)
    assert result["status"] == STATUS_DELETED_ON_TIME


def test_an_active_scope_deletion_does_not_satisfy_the_backup_scope():
    """Deleting the working copy says nothing about the archived one."""
    contract = _contract(max_days=90, backup_max_days=100)
    findings = assess_counterparty(
        contract, [_ingest(days_ago=200)],
        [_deletion(days_ago=150, scope=SCOPE_ACTIVE)], now=NOW)
    by_scope = {f.scope: f for f in findings}
    assert by_scope[SCOPE_BACKUP].status == STATUS_OVERDUE


# -- assembly ----------------------------------------------------------

def test_earliest_deletion_in_a_scope_is_the_one_that_retires_the_record():
    result = retention_status(
        _ingest(days_ago=100),
        [_deletion(days_ago=1), _deletion(days_ago=50)], 90, None, NOW)
    assert result["status"] == STATUS_DELETED_ON_TIME


def test_summary_reports_every_status_including_zeros():
    """A missing key would read as 'none overdue' when it may mean
    'never computed'."""
    counts = summarize(assess_counterparty(_contract(), [_ingest()], [],
                                           now=NOW))
    assert counts[STATUS_OVERDUE] == 0
    assert counts[STATUS_WITHIN_TERM] == 1
    assert set(counts) >= {STATUS_WITHIN_TERM, STATUS_DELETED_ON_TIME,
                           STATUS_OVERDUE, STATUS_INDETERMINATE}


def test_age_is_arithmetic_on_the_recorded_times_not_stored_state():
    result = retention_status(_ingest(days_ago=45), [], 90, None, NOW)
    assert result["age_days"] == 45


def test_findings_carry_the_screening_disclaimer():
    findings = assess_counterparty(_contract(), [_ingest()], [], now=NOW)
    assert "disclaimer" in findings[0].as_dict()


def test_findings_are_scoped_to_the_contracts_own_counterparty():
    findings = assess_counterparty(_contract("beta-corp"), [_ingest()], [],
                                   now=NOW)
    assert findings[0].counterparty_id == "beta-corp"
