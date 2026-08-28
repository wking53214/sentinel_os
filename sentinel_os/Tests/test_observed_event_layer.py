"""The persisted observation-event stream: EventV1 rows written in the same
transaction as the governance_decision they were assembled from, and
reconstruct_decision replaying them through assemble_episode.

Decision C of docs/EVENT_STATE_TRANSITION_IMPROVEMENT_INVESTIGATION.md (in the
GSA-815 repo). Kernel side: event_v1 envelope, canonical_fields shared form,
ledger_postgres.append_decision(observed_events=...), verify_chain +
twin_custody recompute, ledger_postgres.reconstruct_decision.

Tested against a real ledger, same convention as test_ai_cost_ledger.py /
test_outcome_chain_records.py.
"""

import json

import psycopg2
import pytest

from canonical_fields import (OBSERVED_EVENT_CANONICAL_FIELDS,
                              event_v1_to_body, observed_event_canonical)
from event_v1 import (EVENT_SCHEMA_VERSION, REDUCER_VERSION, EventIntegrityError,
                      assemble_episode, make_event, validate_event)
from governance.ledger_postgres import GovernanceDecisionRecord

PG = dict(host="localhost", port=5432, dbname="iceberg", user="iceberg",
          password="iceberg")


@pytest.fixture(autouse=True)
def _drop_ledger_after_each_test():
    """Several tests here deliberately tamper rows to prove verify_chain /
    the twin catch it. `test_ledger` drops the table BEFORE each of its own
    tests, but test files that share the `iceberg` DB without that fixture
    (test_regulatory_cassettes.py) would otherwise inherit a corrupted
    chain. Drop it after every test here so nothing leaks out."""
    yield
    try:
        conn = psycopg2.connect(connect_timeout=2, **PG)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("ALTER TABLE IF EXISTS ledger_entries DISABLE TRIGGER USER;")
        cur.execute("DROP TABLE IF EXISTS ledger_entries CASCADE;")
        conn.close()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _params():
    from cassette_schema import validate_cassette
    from cassettes.ivr_cassette import IvrCassette
    return validate_cassette(IvrCassette())


def _events(episode_id="EPX001", n_waits=2):
    base = 1_000.0
    evs = [
        make_event(event_id=f"{episode_id}:route", episode_id=episode_id,
                   domain="ivr", kind="route_selected", occurred_at=base,
                   observed_at=base + 5, source="twilio_log_ingestion",
                   provenance="estimated", method="phone-digit rule",
                   fields={"route": "billing_queue"}),
    ]
    for i in range(n_waits):
        evs.append(make_event(
            event_id=f"{episode_id}:wait:n{i}", episode_id=episode_id,
            domain="ivr", kind="wait_observed", occurred_at=base + 10 + i,
            observed_at=base + 20, source="twilio_log_ingestion",
            provenance="estimated", method="duration*0.4",
            fields={f"wait_n{i}": 12.0 + i}))
    evs.append(make_event(
        event_id=f"{episode_id}:ended", episode_id=episode_id, domain="ivr",
        kind="call_ended", occurred_at=base + 40, observed_at=base + 40,
        source="twilio:call_log", provenance="verified",
        fields={"resolved": True, "duration": 40.0}))
    return evs


def _record(episode_id="EPX001", events=None, call_sid=None):
    call_sid = call_sid or f"CA{episode_id}"
    assembly = assemble_episode(episode_id, "ivr", {"resolved": True},
                                events or _events(episode_id))
    return GovernanceDecisionRecord(
        action_type="governance_decision", node="billing_queue",
        cassette_version="ivr:standard-ivr:2.0.2",
        input_data={
            "call_sid": call_sid,
            "kernel": {
                "judged": True,
                "tier": "acceptable",
                "score": 0.5,
                "field_provenance": assembly.provenance,
                "estimated_fields": list(assembly.estimated_fields),
                "source_events": list(assembly.source_events),
                "episode_inputs": {"requested": {"resolved": True},
                                   "outcome_reasons": [],
                                   "attributes": {}},
            },
        },
        policy_parameters={"governance_trigger": 2},
        reasoning="AI safety check",
        output={"safe": True, "risk_level": "low", "confidence": 0.9})


def _fetch_shipped_rows(where):
    from twin_custody import SHIPPED_COLUMNS
    conn = psycopg2.connect(connect_timeout=2, **PG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(  # nosec B608 -- SHIPPED_COLUMNS is a fixed code-defined list
        f"SELECT {', '.join(SHIPPED_COLUMNS)} FROM ledger_entries "
        f"WHERE {where} ORDER BY id ASC")
    rows = [dict(zip(SHIPPED_COLUMNS, r)) for r in cur.fetchall()]
    conn.close()
    return rows


# --------------------------------------------------------------------------- #
# Phase 1: the event envelope
# --------------------------------------------------------------------------- #
def test_event_carries_schema_and_reducer_version_by_default():
    e = make_event(event_id="e1", episode_id="ep", domain="ivr", kind="k",
                   occurred_at=1.0, observed_at=1.0, source="s",
                   provenance="verified")
    assert e.schema_version == EVENT_SCHEMA_VERSION
    assert e.reducer_version == REDUCER_VERSION
    validate_event(e)


def test_validate_event_rejects_bad_schema_version():
    e = make_event(event_id="e1", episode_id="ep", domain="ivr", kind="k",
                   occurred_at=1.0, observed_at=1.0, source="s",
                   provenance="verified", schema_version=0)
    with pytest.raises(EventIntegrityError):
        validate_event(e)


def test_validate_event_rejects_empty_reducer_version():
    e = make_event(event_id="e1", episode_id="ep", domain="ivr", kind="k",
                   occurred_at=1.0, observed_at=1.0, source="s",
                   provenance="verified", reducer_version="")
    with pytest.raises(EventIntegrityError):
        validate_event(e)


def test_assembly_reports_the_reducer_version_that_folded_it():
    a = assemble_episode("EPX001", "ivr", {"resolved": True}, _events())
    assert a.reducer_version == REDUCER_VERSION


# --------------------------------------------------------------------------- #
# Phase 2 (property): projected provenance is always a real contributor's
# --------------------------------------------------------------------------- #
def test_projected_field_provenance_is_always_some_contributing_events():
    """The sound version of the §10 invariant: for each field F in the
    assembled provenance map, provenance[F] equals the provenance of at
    least one event that actually wrote F. Holds by construction
    (last-writer-wins), so this guards against a future regression -- it is
    NOT a check inside validate_episode."""
    events = _events("EPMIX", n_waits=1) + [
        # a VERIFIED wait that supersedes the ESTIMATED wait_n0 above --
        # legitimate: the projected field is then verified, and that is a
        # real contributor's stamp, not a promotion.
        make_event(event_id="EPMIX:wait:n0:real", episode_id="EPMIX",
                   domain="ivr", kind="wait_observed", occurred_at=2_000.0,
                   observed_at=2_000.0, source="twilio:ivr_events",
                   provenance="verified", fields={"wait_n0": 9.0}),
    ]
    a = assemble_episode("EPMIX", "ivr", {"resolved": True}, events)
    contributors: dict = {}
    for ev in events:
        for name in ev.fields:
            contributors.setdefault(name, set()).add(ev.provenance)
    for field_name, stamp in a.provenance.items():
        assert stamp in contributors[field_name], (
            f"{field_name}: projected {stamp!r} not among contributors "
            f"{contributors[field_name]!r}")
    assert a.provenance["wait_n0"] == "verified"  # supersession is honest


# --------------------------------------------------------------------------- #
# Phase 3: persistence + hash agreement across all three recompute sites
# --------------------------------------------------------------------------- #
def test_canonical_form_covers_every_body_field():
    body = event_v1_to_body(_events()[0])
    for key in OBSERVED_EVENT_CANONICAL_FIELDS:
        assert key in body
    c = observed_event_canonical(body, "prev")
    assert c["record_kind"] == "observed_event"
    assert c["previous_hash"] == "prev"


def test_append_decision_persists_the_stream_in_one_transaction(test_ledger):
    events = _events("EPTX1")
    assert test_ledger.append_decision(_record("EPTX1", events),
                                       governance_params=_params(),
                                       observed_events=events)
    rows = _fetch_shipped_rows("record_kind = 'observed_event'")
    assert len(rows) == len(events)
    # chained immediately after the decision row
    dec = _fetch_shipped_rows("record_kind = 'governance_decision'")[-1]
    assert rows[0]["previous_hash"] == dec["current_hash"]
    for a, b in zip(rows, rows[1:]):
        assert b["previous_hash"] == a["current_hash"]


def test_legacy_append_decision_without_events_is_unchanged(test_ledger):
    assert test_ledger.append_decision(_record("EPLEG"),
                                       governance_params=_params())
    assert not _fetch_shipped_rows("record_kind = 'observed_event'")
    assert test_ledger.verify_chain()["ok"]


def test_verify_chain_passes_over_the_extended_chain(test_ledger):
    events = _events("EPVC1")
    test_ledger.append_decision(_record("EPVC1", events),
                                governance_params=_params(),
                                observed_events=events)
    # a second decision after the event rows -- proves the chain resumes
    test_ledger.append_decision(_record("EPVC2", _events("EPVC2")),
                                governance_params=_params(),
                                observed_events=_events("EPVC2"))
    assert test_ledger.verify_chain(mode="strict")["ok"]


def test_twin_recomputes_observed_event_rows_identically(test_ledger):
    """Recompute site 3. If the witness disagrees with the writer every
    honest observed_event row reads as DIVERGE -- the highest-risk failure
    mode of this change (canonical_fields.py says so explicitly)."""
    from twin_custody import recompute_current_hash
    events = _events("EPTWIN")
    test_ledger.append_decision(_record("EPTWIN", events),
                                governance_params=_params(),
                                observed_events=events)
    rows = _fetch_shipped_rows("record_kind = 'observed_event'")
    assert len(rows) == len(events)
    for row in rows:
        assert recompute_current_hash(row) == row["current_hash"]


def test_tampering_an_event_field_is_caught_by_verify_and_twin(test_ledger):
    from twin_custody import deep_verify_row
    events = _events("EPTAM")
    test_ledger.append_decision(_record("EPTAM", events),
                                governance_params=_params(),
                                observed_events=events)
    conn = psycopg2.connect(connect_timeout=2, **PG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE ledger_entries DISABLE TRIGGER USER;")
    cur.execute(
        "UPDATE ledger_entries "
        "SET input_data = jsonb_set(input_data, '{fields,route}', '\"tampered\"') "
        "WHERE record_kind = 'observed_event' "
        "AND input_data->>'event_id' = 'EPTAM:route';")
    cur.execute("ALTER TABLE ledger_entries ENABLE TRIGGER USER;")
    conn.close()

    result = test_ledger.verify_chain(mode="tolerant")
    assert not result["ok"]
    assert any("content hash mismatch" in v for v in result["violations"])

    row = _fetch_shipped_rows("input_data->>'event_id' = 'EPTAM:route'")[0]
    ok, detail = deep_verify_row(row)
    assert not ok


def test_duplicate_event_id_in_one_batch_is_rejected(test_ledger):
    dup = _events("EPDUP")
    dup.append(dup[0])
    with pytest.raises(ValueError, match="duplicate observed event_id"):
        test_ledger.append_decision(_record("EPDUP", _events("EPDUP")),
                                    governance_params=_params(),
                                    observed_events=dup)
    assert not _fetch_shipped_rows("record_kind = 'observed_event'")


def test_events_from_two_episodes_in_one_batch_is_rejected(test_ledger):
    mixed = _events("EPA") + _events("EPB")
    with pytest.raises(ValueError, match="span multiple"):
        test_ledger.append_decision(_record("EPA"),
                                    governance_params=_params(),
                                    observed_events=mixed)


def test_a_malformed_event_rejects_the_whole_decision(test_ledger):
    events = _events("EPBAD")
    bad = make_event(event_id="EPBAD:bad", episode_id="EPBAD", domain="ivr",
                     kind="x", occurred_at=1.0, observed_at=1.0, source="s",
                     provenance="estimated")  # estimated w/ no method
    object.__setattr__(bad, "method", None)
    events.append(bad)
    with pytest.raises(EventIntegrityError):
        test_ledger.append_decision(_record("EPBAD"),
                                    governance_params=_params(),
                                    observed_events=events)
    assert not _fetch_shipped_rows("record_kind = 'observed_event'")
    assert not _fetch_shipped_rows(
        "record_kind = 'governance_decision' "
        "AND input_data->>'call_sid' = 'CAEPBAD'")


def test_partial_unique_index_rejects_a_redelivered_event(test_ledger):
    events = _events("EPIDX")
    test_ledger.append_decision(_record("EPIDX", events),
                                governance_params=_params(),
                                observed_events=events)
    # same event_ids again, on a different decision -> DB unique index bites
    with pytest.raises(psycopg2.errors.UniqueViolation):
        test_ledger.append_decision(_record("EPIDX2", _events("EPIDX2")),
                                    governance_params=_params(),
                                    observed_events=_events("EPIDX"))
    # the rollback left the chain consistent and the ledger usable
    assert test_ledger.append_decision(_record("EPIDX3", _events("EPIDX3")),
                                       governance_params=_params(),
                                       observed_events=_events("EPIDX3"))
    assert test_ledger.verify_chain(mode="strict")["ok"]


# --------------------------------------------------------------------------- #
# Phase 4: reconstruct_decision
# --------------------------------------------------------------------------- #
def _decision_row_id():
    conn = psycopg2.connect(connect_timeout=2, **PG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT id FROM ledger_entries WHERE record_kind = "
                "'governance_decision' ORDER BY id DESC LIMIT 1")
    (rid,) = cur.fetchone()
    conn.close()
    return rid


def test_reconstruct_decision_replays_a_clean_decision(test_ledger):
    events = _events("EPR1")
    test_ledger.append_decision(_record("EPR1", events),
                                governance_params=_params(),
                                observed_events=events)
    out = test_ledger.reconstruct_decision(_decision_row_id())
    assert out["ok"], out
    assert out["event_count"] == len(events)
    assert out["checks"]["field_provenance"]["match"]
    assert out["checks"]["estimated_fields"]["match"]
    assert not out["reducer_drift"]


def test_reconstruct_decision_flags_a_tampered_summary(test_ledger):
    events = _events("EPR2")
    test_ledger.append_decision(_record("EPR2", events),
                                governance_params=_params(),
                                observed_events=events)
    rid = _decision_row_id()
    conn = psycopg2.connect(connect_timeout=2, **PG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE ledger_entries DISABLE TRIGGER USER;")
    cur.execute(
        "UPDATE ledger_entries SET input_data = "
        "jsonb_set(input_data, '{kernel,estimated_fields}', '[\"nonsense\"]') "
        "WHERE id = %s;", (rid,))
    cur.execute("ALTER TABLE ledger_entries ENABLE TRIGGER USER;")
    conn.close()

    out = test_ledger.reconstruct_decision(rid)
    assert not out["ok"]
    assert "estimated_fields" in out["reason"]


def test_reconstruct_decision_flags_a_missing_event(test_ledger):
    events = _events("EPR3")
    test_ledger.append_decision(_record("EPR3", events),
                                governance_params=_params(),
                                observed_events=events)
    rid = _decision_row_id()
    conn = psycopg2.connect(connect_timeout=2, **PG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("ALTER TABLE ledger_entries DISABLE TRIGGER USER;")
    cur.execute("DELETE FROM ledger_entries WHERE record_kind = "
                "'observed_event' AND input_data->>'event_id' = 'EPR3:route';")
    cur.execute("ALTER TABLE ledger_entries ENABLE TRIGGER USER;")
    conn.close()

    out = test_ledger.reconstruct_decision(rid)
    assert not out["ok"]
    assert "incomplete" in out["reason"] or "missing" in out["reason"]


def test_reconstruct_decision_wont_pass_on_the_id_list_alone(test_ledger):
    """A decision whose kernel summary carries source_events but neither
    field_provenance nor estimated_fields cannot have its event bodies
    checked -- reconstruct must NOT report ok just because the ids are all
    present."""
    events = _events("EPR4")
    rec = _record("EPR4", events)
    rec.input_data["kernel"].pop("field_provenance", None)
    rec.input_data["kernel"].pop("estimated_fields", None)
    test_ledger.append_decision(rec, governance_params=_params(),
                                observed_events=events)
    out = test_ledger.reconstruct_decision(_decision_row_id())
    assert not out["ok"]
    assert not out["content_verified"]
    assert out["checks"]["source_events"]["match"]  # ids alone did match


def test_reconstruct_decision_on_a_legacy_row_says_no_source_events(test_ledger):
    test_ledger.append_decision(
        GovernanceDecisionRecord(
            action_type="governance_decision", node="q",
            cassette_version="ivr:standard-ivr:2.0.2",
            input_data={"call_sid": "CANOEV"},
            policy_parameters={"governance_trigger": 2},
            reasoning="x", output={"safe": True}),
        governance_params=_params())
    out = test_ledger.reconstruct_decision(_decision_row_id())
    assert not out["ok"]
    assert "source_events" in out["reason"]
