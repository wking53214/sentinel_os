"""
test_regulatory_cassettes -- regulatory-cassette framework proof suite.

Proves the framework's contract end to end, against the real ledger:

1. BASE INTERFACE: a lens that cannot state its modes, checks, or a
   strictly-JSON-safe profile does not validate; lens and domain
   registries refuse each other's citizens.
2. LEDGER EVENTS: lens insertion/removal and live disclosures are
   first-class hash-chained record kinds -- written, chain-verified,
   and recomputed identically by the twin. "When was this lens active"
   is a direct query.
3. SPECIFICITY CHECKER: a generic adverse-action reason flags, a
   case-specific one does not, a bare code classifies placeholder --
   and a CMS-style profile reuses the SAME checker unmodified
   (configuration, not code, is the extension point).
4. DISCLOSURE SAFEGUARD (non-negotiable): every live flag/block is on
   the chain, naming regulation and check, BEFORE it takes effect;
   a failed disclosure write aborts the action instead of proceeding
   silently; explain() never writes.
5. PROXY SCREEN: declared proxy variables (zip code) and direct
   protected characteristics flag; clean inputs do not. Name
   screening only -- no statistics, on purpose.
6. FORENSICS: the new modules sit inside the shared code-hash tamper
   surface, and removing one from the surface moves the hash.

Run: pytest Tests/test_regulatory_cassettes.py -q
Requires the same live Postgres the rest of the suite uses.
"""

import json
import os
import sys
import uuid

import psycopg2
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import cassette_forensics
from cassette_forensics import (
    _GOVERNANCE_CODE_MODULES,
    compute_cassette_code_hash,
    compute_cassette_hash,
)
from cassette_interface import CassetteRegistry
from cassette_schema import CassetteValidationError
from cassettes.banking_cassette import BankingCassette
from episode import judge_episode, make_episode
from governance.ledger_postgres import GovernanceDecisionRecord, PostgreSQLLedger
from regulatory_cassette_interface import (
    ACTION_BLOCK,
    ACTION_FLAG,
    MODE_LIVE,
    MODE_OBSERVER,
    DecisionMaterial,
    RegulatoryBlock,
    RegulatoryCassette,
    RegulatoryCassetteConfig,
    RegulatoryCassetteRegistry,
    RegulatoryValidationError,
    SCREENING_DISCLAIMER,
    material_from_episode,
    material_from_ledger_row,
    regulatory_cassette_version_of,
    validate_regulatory_cassette,
)
from regulatory_checks import (
    RegulationCheckProfile,
    assess_reason_specificity,
    check_proxy_variables,
    check_reason_specificity,
)
from regulatory_deck import GovernedJudgment, RegulatoryDeck
from regulatory_cassettes.cfpb_reg_b import (
    CFPB_REG_B_PROFILE,
    CHECK_PROHIBITED_BASIS,
    CHECK_REASON_SPECIFICITY,
    CFPBRegBLens,
)
from twin_custody import SHIPPED_COLUMNS, recompute_current_hash

DSN = dict(
    host=os.environ.get("PGHOST", "127.0.0.1"),
    port=int(os.environ.get("PGPORT", "5432")),
    dbname=os.environ.get("PGDATABASE", "iceberg"),
    user=os.environ.get("PGUSER", "iceberg"),
    password=os.environ.get("PGPASSWORD", "iceberg"),
)


def _conn():
    """Autocommit read connection. Plain SELECT connections left in an
    open transaction hold ACCESS SHARE on ledger_entries, and the
    ledger constructor's idempotent ALTER TABLE migrations need ACCESS
    EXCLUSIVE -- an idle-in-transaction reader therefore stalls the
    next PostgreSQLLedger() forever (found the hard way; see the
    session buildlog). Autocommit means a SELECT never leaves a
    transaction open, so read helpers here can never block a
    constructor."""
    c = psycopg2.connect(**DSN)
    c.autocommit = True
    return c


def _ledger():
    return PostgreSQLLedger(**DSN)


def _rows(conn):
    cur = conn.cursor()
    cur.execute(f"SELECT {', '.join(SHIPPED_COLUMNS)} FROM ledger_entries ORDER BY id ASC")
    out = []
    for r in cur.fetchall():
        d = dict(zip(SHIPPED_COLUMNS, r))
        for k in ("data", "input_data", "policy_parameters", "decision_output",
                  "cassette_snapshot"):
            v = d.get(k)
            if isinstance(v, str):
                try:
                    d[k] = json.loads(v)
                except Exception:
                    pass
        out.append(d)
    return out


def _row_count(conn):
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM ledger_entries")
    return cur.fetchone()[0]


def _fresh_lens(**kwargs):
    """A CFPB lens with a uuid version so binding never collides across
    runs of this suite on a persistent database (a version string is a
    content commitment; a reused one with today's code hash would trip
    yesterday's binding, which is correct behavior and exactly why we
    don't reuse strings in tests)."""
    kwargs.setdefault("version", f"1.0.0-t{uuid.uuid4().hex[:8]}")
    return CFPBRegBLens(**kwargs)


def _lending_episode(reason, episode_id=None, inputs=None):
    """A lending-shaped episode banking can judge: requested approval,
    actual decline -- reason on file (the kernel refuses it otherwise),
    the lens screens whether the reason says anything."""
    attributes = {"duration": 200.0, "friction_count": 0}
    attributes.update(inputs or {})
    return make_episode(
        episode_id or f"EP-{uuid.uuid4().hex[:8]}", "banking",
        requested={"approved": True, "amount": 25000.0},
        actual={"approved": False, "amount": 0.0, "resolved": False},
        outcome_reasons=(reason,),
        attributes=attributes,
    )


GENERIC_REASON = "Applicant does not meet our minimum credit standards."
SPECIFIC_REASON = ("Credit score 574 is below the 620 required for the "
                   "requested amount of $25,000; debt-to-income 52% exceeds "
                   "the 43% ceiling.")


# ==========================================================================
# 1. Base interface
# ==========================================================================

class _NoModesLens(RegulatoryCassette):
    MODES = None  # type: ignore[assignment]

    def get_config(self):
        return RegulatoryCassetteConfig("x", "1", "d", "reg", "auth")

    def get_checks(self):
        return ("c",)

    def get_profile(self):
        return {}

    def review(self, material):
        return []

    def validate(self):
        return True


def test_lens_without_modes_refused():
    lens = _NoModesLens()
    with pytest.raises(RegulatoryValidationError, match="MODES"):
        validate_regulatory_cassette(lens)


def test_lens_with_unknown_mode_refused():
    lens = _NoModesLens()
    lens.MODES = ("observer", "supervisor")
    with pytest.raises(RegulatoryValidationError, match="unknown mode 'supervisor'"):
        validate_regulatory_cassette(lens)


def test_lens_with_empty_checks_refused():
    lens = _NoModesLens()
    lens.MODES = (MODE_OBSERVER,)
    lens.get_checks = lambda: ()
    with pytest.raises(RegulatoryValidationError, match="non-empty tuple of check"):
        validate_regulatory_cassette(lens)


def test_lens_with_non_json_profile_refused():
    """The profile is content-hashed as the lens's configuration; an
    object that only serializes via repr would let 'identical config'
    hash differently -- refused at validation, not discovered at bind."""
    lens = _NoModesLens()
    lens.MODES = (MODE_OBSERVER,)
    lens.get_profile = lambda: {"threshold": object()}
    with pytest.raises(RegulatoryValidationError, match="JSON-serializable"):
        validate_regulatory_cassette(lens)


def test_valid_lens_snapshot_and_identity():
    lens = _fresh_lens()
    snapshot = validate_regulatory_cassette(lens)
    identity = regulatory_cassette_version_of(lens)
    assert identity.startswith("regulatory:cfpb-ecoa-reg-b:")
    assert snapshot["cassette_version"] == identity
    assert snapshot["kind"] == "regulatory_lens"
    assert snapshot["modes"] == ["live", "observer"]
    assert set(snapshot["checks"]) == {CHECK_REASON_SPECIFICITY,
                                       CHECK_PROHIBITED_BASIS}
    # Deterministic content hash over the full configuration.
    assert compute_cassette_hash(snapshot) == compute_cassette_hash(lens.snapshot())


def test_registries_refuse_each_others_citizens():
    """Lens is not policy and policy is not a lens: the domain registry
    refuses a regulatory lens (it has no kernel contract), and the
    regulatory registry refuses a domain cassette (it has no lens
    contract). Two questions, two registries -- enforced, not styled."""
    reg_registry = RegulatoryCassetteRegistry()
    identity = reg_registry.register(_fresh_lens())
    assert identity in reg_registry.list_all()

    # A lens config deliberately has no .domain (its identity namespace
    # is the reserved "regulatory" prefix instead) -- so the domain
    # registry's key construction fails before schema validation would.
    # Either exception is the refusal working; there is no path where a
    # lens lands in the domain registry.
    with pytest.raises((CassetteValidationError, AttributeError)):
        CassetteRegistry().register(_fresh_lens())
    with pytest.raises(RegulatoryValidationError):
        reg_registry.register(BankingCassette())


def test_material_from_episode_excludes_actor_report():
    ep = make_episode(
        "EP-MAT", "banking",
        requested={"approved": True},
        actual={"approved": False, "resolved": False},
        actor_report={"self_praise": "flawless"},
        outcome_reasons=("declined for stated reasons",),
        attributes={"duration": 100.0, "friction_count": 0, "zip_code": "60601"},
    )
    material = material_from_episode(ep)
    assert material.source == "episode"
    assert material.mismatched_fields == ("approved",)
    assert "zip_code" in material.input_fields
    assert "approved" in material.input_fields  # the request is decision input
    assert "self_praise" not in material.input_fields  # actor story never enters


def test_material_from_ledger_row_shapes():
    row = {"id": 42, "reasoning": "  recorded reason  ",
           "output": {"approved": False, "reasons": ["second reason", " "]},
           "input_data": {"income": 50000}, "cassette_version": "banking:x:1"}
    material = material_from_ledger_row(row)
    assert material.subject_id == "42"
    assert material.reasons == ("recorded reason", "second reason")
    assert material.input_fields == {"income": 50000}
    assert material.mismatched_fields == ()
    assert material.source == "ledger"


# ==========================================================================
# 3. Specificity checker (the case that SHOULD flag, and the one that
#    shouldn't) + configuration-not-code reuse
# ==========================================================================

def test_generic_reason_flags():
    material = material_from_episode(_lending_episode(GENERIC_REASON))
    findings = check_reason_specificity(material, CFPB_REG_B_PROFILE)
    assert len(findings) == 1
    f = findings[0]
    assert f.classification == "generic"
    assert f.action == ACTION_FLAG
    assert f.score < CFPB_REG_B_PROFILE.specific_score_threshold
    # The evidence shows the mechanism: which phrases fired.
    assert "does not meet" in f.evidence["generic_phrase_hits"]
    assert f.evidence["cites_concrete_value"] is False


def test_specific_reason_passes():
    material = material_from_episode(_lending_episode(SPECIFIC_REASON))
    findings = check_reason_specificity(material, CFPB_REG_B_PROFILE)
    assert findings == []
    # And the assessment shows WHY it passed: concrete values plus a
    # named case field outweigh the "credit score" boilerplate hit.
    assessment = assess_reason_specificity(SPECIFIC_REASON, material,
                                           CFPB_REG_B_PROFILE)
    assert assessment["flagged"] is False
    assert assessment["evidence"]["cites_concrete_value"] is True
    assert "amount" in assessment["evidence"]["case_fields_referenced"]


def test_placeholder_code_flags_at_zero():
    material = material_from_episode(_lending_episode("DECLINE_001"))
    findings = check_reason_specificity(material, CFPB_REG_B_PROFILE)
    assert len(findings) == 1
    assert findings[0].classification == "placeholder"
    assert findings[0].score == 0.0


def test_missing_reason_on_ledger_material_flags():
    """Reachable only for ledger rows: the kernel refuses to validate a
    mismatched episode with no reason, so live material can never
    arrive here reasonless -- but a recorded row might, and an
    observer review must say so."""
    material = DecisionMaterial(
        subject_id="7", domain="banking:x:1", reasons=(),
        input_fields={"income": 1}, mismatched_fields=("approved",),
        outcome={}, source="ledger",
    )
    findings = check_reason_specificity(material, CFPB_REG_B_PROFILE)
    assert [f.classification for f in findings] == ["missing"]


def test_cms_style_profile_reuses_same_checker():
    """THE reusability proof: a CMS-shaped lens ('denial notices must
    cite specific current criteria, not generic algorithmic output') is
    a RegulationCheckProfile -- pure configuration -- run through the
    unmodified checker. No new code, and the same reason text flips
    verdicts appropriately under the different profile."""
    cms = RegulationCheckProfile(
        regulation="CMS coverage-denial notice specificity (illustrative)",
        generic_phrases=("not medically necessary", "medical necessity",
                         "plan guidelines", "algorithmic output",
                         "clinical criteria", "internal review"),
    )
    material = DecisionMaterial(
        subject_id="CLM-1", domain="health-plan", reasons=(),
        input_fields={"procedure_code": "99214"}, mismatched_fields=("coverage",),
        outcome={}, source="ledger",
    )
    generic = material_from_ledger_row({
        "id": 1, "reasoning": "Denied: not medically necessary per plan guidelines.",
        "input_data": {"procedure_code": "99214"}, "output": {},
    })
    specific = material_from_ledger_row({
        "id": 2, "reasoning": ("Denied under LCD L34567 criterion 3: submitted "
                               "HbA1c 9.2 exceeds the 8.0 ceiling current as of "
                               "2026-01."),
        "input_data": {"procedure_code": "99214"}, "output": {},
    })
    assert len(check_reason_specificity(generic, cms)) == 1
    assert check_reason_specificity(specific, cms) == []
    # Same texts under the CFPB profile behave differently -- the
    # CMS phrases aren't CFPB phrases -- which is the point: the
    # regulation lives in the profile, not the checker.
    assert check_reason_specificity(generic, CFPB_REG_B_PROFILE) == []
    del material  # (constructed above only to document the shape)


# ==========================================================================
# 5. Proxy-variable / direct-protected screen (C2, scoped down)
# ==========================================================================

def test_proxy_zip_code_flags():
    material = material_from_episode(_lending_episode(
        SPECIFIC_REASON, inputs={"zip_code": "60601"}))
    findings = check_proxy_variables(material, CFPB_REG_B_PROFILE)
    flagged = {f.evidence["variable"]: f for f in findings}
    assert "zip_code" in flagged
    assert flagged["zip_code"].classification == "proxy_variable"
    assert "race" in flagged["zip_code"].evidence["proxies_for"]


def test_clean_inputs_produce_no_proxy_findings():
    material = material_from_episode(_lending_episode(
        SPECIFIC_REASON, inputs={"income": 88000, "debt_to_income": 0.52,
                                 "requested_term_months": 60}))
    # amount/approved from the request are also inputs; none match.
    assert check_proxy_variables(material, CFPB_REG_B_PROFILE) == []


def test_direct_protected_characteristic_flags():
    material = material_from_episode(_lending_episode(
        SPECIFIC_REASON, inputs={"applicant_race": "declined-to-state"}))
    findings = check_proxy_variables(material, CFPB_REG_B_PROFILE)
    assert len(findings) == 1
    assert findings[0].classification == "direct_protected_characteristic"
    assert findings[0].evidence["variable"] == "applicant_race"


def test_cfpb_lens_review_combines_both_checks():
    lens = _fresh_lens()
    material = material_from_episode(_lending_episode(
        GENERIC_REASON, inputs={"zip_code": "60601"}))
    findings = lens.review(material)
    checks = sorted({f.check for f in findings})
    assert checks == sorted({CHECK_REASON_SPECIFICITY, CHECK_PROHIBITED_BASIS})
    assert all(f.action == ACTION_FLAG for f in findings)
    for f in findings:
        json.dumps(f.as_dict())  # every finding is ledger-safe


# ==========================================================================
# 2. Ledger event types -- chain + twin, against real Postgres
# ==========================================================================

def test_insertion_and_removal_events_verify_in_chain_and_twin():
    conn = _conn()
    L = _ledger()
    ver = f"regulatory:test-lens:{uuid.uuid4().hex[:8]}"
    L.record_regulatory_cassette_event(
        event="regulatory_cassette_inserted", cassette_version=ver,
        cassette_hash="h-ins", cassette_code_hash="c-ins", mode="observer",
        regulation="Test Regulation 1", authorized_by="auditor:test")
    L.record_regulatory_cassette_event(
        event="regulatory_cassette_removed", cassette_version=ver,
        cassette_hash="h-ins", cassette_code_hash="c-ins", mode="observer",
        regulation="Test Regulation 1", authorized_by="auditor:test")
    rows = [r for r in _rows(conn) if r["cassette_version"] == ver]
    assert [r["record_kind"] for r in rows] == ["regulatory_cassette_inserted",
                                               "regulatory_cassette_removed"]
    for row in rows:
        assert recompute_current_hash(row) == row["current_hash"]
        assert row["data"]["mode"] == "observer"
        assert row["data"]["regulation"] == "Test Regulation 1"
    assert L.verify_chain(mode="lenient")["ok"] is True


def test_disclosure_event_verifies_in_chain_and_twin():
    conn = _conn()
    L = _ledger()
    ver = f"regulatory:test-lens:{uuid.uuid4().hex[:8]}"
    finding = {"check": "reason_specificity", "score": 0.25,
               "evidence": {"generic_phrase_hits": ["does not meet"]}}
    L.record_regulatory_disclosure(
        cassette_version=ver, regulation="Test Regulation 2",
        check="reason_specificity", action="flag", subject_id="EP-9",
        finding=finding, cassette_hash="h-d", authorized_by="auditor:test")
    row = [r for r in _rows(conn) if r["cassette_version"] == ver][-1]
    assert row["record_kind"] == "regulatory_disclosure"
    assert recompute_current_hash(row) == row["current_hash"]
    # The disclosure names the regulation AND the specific check.
    assert row["data"]["regulation"] == "Test Regulation 2"
    assert row["data"]["check"] == "reason_specificity"
    assert row["data"]["action"] == "flag"
    assert row["decision_output"]["score"] == 0.25
    assert L.verify_chain(mode="lenient")["ok"] is True


def test_disclosure_requires_the_specific_check_and_action():
    L = _ledger()
    with pytest.raises(ValueError, match="specific"):
        L.record_regulatory_disclosure(
            cassette_version="regulatory:x:1", regulation="R", check="  ",
            action="flag", subject_id="s", finding={})
    with pytest.raises(ValueError, match="action"):
        L.record_regulatory_disclosure(
            cassette_version="regulatory:x:1", regulation="R", check="c",
            action="veto", subject_id="s", finding={})


def test_insertion_event_requires_identity_and_known_event():
    L = _ledger()
    with pytest.raises(ValueError, match="authorized_by"):
        L.record_regulatory_cassette_event(
            event="regulatory_cassette_inserted", cassette_version="regulatory:x:1",
            cassette_hash="h", cassette_code_hash=None, mode="observer",
            regulation="R", authorized_by="  ")
    with pytest.raises(ValueError, match="Unknown regulatory cassette event"):
        L.record_regulatory_cassette_event(
            event="regulatory_cassette_paused", cassette_version="regulatory:x:1",
            cassette_hash="h", cassette_code_hash=None, mode="observer",
            regulation="R", authorized_by="a")


# ==========================================================================
# Deck: insertion binding, observer review, live path -- real Postgres
# ==========================================================================

def test_deck_requires_a_ledger():
    with pytest.raises(ValueError, match="requires a ledger"):
        RegulatoryDeck(None)


def test_insert_binds_records_and_reports_history():
    L = _ledger()
    deck = RegulatoryDeck(L)
    lens = _fresh_lens()
    receipt = deck.insert(lens, MODE_OBSERVER, inserted_by="auditor:cfpb-1")
    identity = receipt["identity"]
    assert receipt["mode"] == MODE_OBSERVER
    assert receipt["cassette_hash"] == compute_cassette_hash(lens.snapshot())
    assert deck.active()[0]["identity"] == identity

    deck.remove(identity, removed_by="auditor:cfpb-1")
    assert deck.active() == []

    # The examiner query: the active window read straight off the chain.
    history = L.get_regulatory_cassette_history(cassette_version=identity)
    assert [h["event"] for h in history] == ["regulatory_cassette_inserted",
                                            "regulatory_cassette_removed"]
    assert all(h["mode"] == MODE_OBSERVER for h in history)
    assert all(h["authorized_by"] == "auditor:cfpb-1" for h in history)
    assert history[0]["cassette_hash"] == receipt["cassette_hash"]


def test_changed_lens_config_same_version_refused_at_insertion():
    """The tamper tripwire, reused from domain cassettes (decision 5):
    a lens whose configuration changed -- here, the blocking switch --
    under an UNCHANGED version string is refused at bind. Changed
    content requires a new version, not a silent re-bind."""
    L = _ledger()
    deck = RegulatoryDeck(L)
    shared_version = f"1.0.0-t{uuid.uuid4().hex[:8]}"
    deck.insert(CFPBRegBLens(version=shared_version), MODE_OBSERVER,
                inserted_by="auditor:a")
    altered = CFPBRegBLens(block_on_placeholder=True, version=shared_version)
    with pytest.raises(ValueError, match="binding conflict"):
        RegulatoryDeck(L).insert(altered, MODE_OBSERVER, inserted_by="auditor:b")


def test_insert_refuses_mode_the_lens_does_not_declare():
    class ObserverOnly(CFPBRegBLens):
        MODES = (MODE_OBSERVER,)

    L = _ledger()
    with pytest.raises(ValueError, match="does not support mode 'live'"):
        RegulatoryDeck(L).insert(ObserverOnly(version=f"1.0.0-t{uuid.uuid4().hex[:8]}"),
                                 MODE_LIVE, inserted_by="auditor:a")


def test_insert_requires_inserted_by():
    L = _ledger()
    with pytest.raises(ValueError, match="inserted_by"):
        RegulatoryDeck(L).insert(_fresh_lens(), MODE_OBSERVER)


def test_observer_review_flags_recorded_generic_decision_and_writes_nothing():
    conn = _conn()
    L = _ledger()
    domain_ver = f"banking:banking-v1:t{uuid.uuid4().hex[:8]}"
    L.append_decision(GovernanceDecisionRecord(
        action_type="governance_decision", node="loan_queue",
        cassette_version=domain_ver,
        input_data={"call_sid": f"CA-{uuid.uuid4().hex[:10]}",
                    "zip_code": "60601", "income": 41000},
        policy_parameters={"program_max_dti": 0.43}, reasoning=GENERIC_REASON,
        output={"approved": False}))
    L.append_decision(GovernanceDecisionRecord(
        action_type="governance_decision", node="loan_queue",
        cassette_version=domain_ver,
        input_data={"call_sid": f"CA-{uuid.uuid4().hex[:10]}",
                    "income": 92000},
        policy_parameters={"program_max_dti": 0.43}, reasoning=SPECIFIC_REASON,
        output={"approved": False}))

    deck = RegulatoryDeck(L)
    deck.insert(_fresh_lens(), MODE_OBSERVER, inserted_by="auditor:cfpb-1")

    before = _row_count(conn)
    report = deck.observer_review(decision_cassette_version=domain_ver)
    assert _row_count(conn) == before  # observer review writes NOTHING

    assert report["disclaimer"] == SCREENING_DISCLAIMER
    lens_report = report["lenses"][0]
    assert lens_report["decisions_reviewed"] == 2
    assert lens_report["decisions_flagged"] == 1  # only the generic+zip row
    classifications = sorted(f["classification"] for f in lens_report["findings"])
    assert classifications == ["generic", "proxy_variable"]


def test_live_flag_is_disclosed_before_judgment_returns():
    conn = _conn()
    L = _ledger()
    deck = RegulatoryDeck(L)
    lens = _fresh_lens()
    receipt = deck.insert(lens, MODE_LIVE, inserted_by="auditor:cfpb-live")
    cassette = BankingCassette()
    episode = _lending_episode(GENERIC_REASON, inputs={"zip_code": "60601"})

    governed = deck.judge(cassette, episode)
    assert isinstance(governed, GovernedJudgment)
    # The domain judgment is untouched by the lens: byte-identical to
    # the plain kernel path. A lens reviews; it never moves the score.
    plain = judge_episode(cassette, episode)
    assert governed.quality == plain
    assert len(governed.findings) == 2

    disclosures = [r for r in _rows(conn)
                   if r["record_kind"] == "regulatory_disclosure"
                   and r["cassette_version"] == receipt["identity"]]
    assert len(disclosures) == 2
    for row in disclosures:
        assert row["data"]["regulation"].startswith("ECOA / Regulation B")
        assert row["data"]["check"] in (CHECK_REASON_SPECIFICITY,
                                        CHECK_PROHIBITED_BASIS)
        assert row["data"]["subject"] == episode.episode_id
        assert recompute_current_hash(row) == row["current_hash"]
    assert L.verify_chain(mode="lenient")["ok"] is True


def test_live_clean_episode_no_findings_no_disclosures():
    conn = _conn()
    L = _ledger()
    deck = RegulatoryDeck(L)
    deck.insert(_fresh_lens(), MODE_LIVE, inserted_by="auditor:cfpb-live")
    before = _row_count(conn)
    governed = deck.judge(BankingCassette(),
                          _lending_episode(SPECIFIC_REASON,
                                           inputs={"income": 92000}))
    assert governed.findings == ()
    assert _row_count(conn) == before  # nothing fired, nothing written


def test_live_block_is_disclosed_then_raised():
    conn = _conn()
    L = _ledger()
    deck = RegulatoryDeck(L)
    blocking = CFPBRegBLens(block_on_placeholder=True,
                            version=f"1.0.0-t{uuid.uuid4().hex[:8]}")
    receipt = deck.insert(blocking, MODE_LIVE, inserted_by="auditor:cfpb-block")
    episode = _lending_episode("DECLINE_001")

    with pytest.raises(RegulatoryBlock) as exc:
        deck.judge(BankingCassette(), episode)
    assert exc.value.lens_identity == receipt["identity"]
    assert any(f.action == ACTION_BLOCK for f in exc.value.findings)

    row = [r for r in _rows(conn)
           if r["record_kind"] == "regulatory_disclosure"
           and r["cassette_version"] == receipt["identity"]][-1]
    assert row["data"]["action"] == "block"  # on the chain BEFORE the raise
    assert row["data"]["check"] == CHECK_REASON_SPECIFICITY


def test_failed_disclosure_aborts_the_action_never_silent():
    """THE safeguard's fail-closed half: if the disclosure write fails,
    the flag does not quietly proceed -- judgment does not return.
    A live lens that cannot disclose does not act."""

    class _DisclosureDownLedger:
        def __init__(self):
            self.bound = []

        def bind_cassette_version(self, *a, **k):
            return {"status": "created"}

        def record_regulatory_cassette_event(self, **k):
            return {"status": "created", "current_hash": "x"}

        def record_regulatory_disclosure(self, **k):
            raise RuntimeError("ledger unavailable for disclosure")

    deck = RegulatoryDeck(_DisclosureDownLedger())
    deck.insert(_fresh_lens(), MODE_LIVE, inserted_by="auditor:t")
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        deck.judge(BankingCassette(), _lending_episode(GENERIC_REASON))


def test_explain_includes_findings_but_writes_nothing():
    conn = _conn()
    L = _ledger()
    deck = RegulatoryDeck(L)
    deck.insert(_fresh_lens(), MODE_LIVE, inserted_by="auditor:cfpb-live")
    before = _row_count(conn)
    factors = deck.explain(BankingCassette(),
                           _lending_episode(GENERIC_REASON,
                                            inputs={"zip_code": "60601"}))
    assert _row_count(conn) == before  # explanation is a reporting surface
    regulatory = [f for f in factors if f.get("factor") == "regulatory_finding"]
    assert len(regulatory) == 2
    # Kernel verification factors still ride first (outcome mismatch).
    assert any(f.get("factor") == "outcome_mismatch" for f in factors)


# ==========================================================================
# 6. Forensics: the new modules are inside the tamper surface
# ==========================================================================

def test_regulatory_modules_in_code_hash_surface():
    for module in ("regulatory_cassette_interface", "regulatory_checks",
                   "regulatory_deck"):
        assert module in _GOVERNANCE_CODE_MODULES


def test_code_hash_covers_regulatory_checks_source(monkeypatch):
    """Removing regulatory_checks from the declared surface moves the
    lens's code hash -- proving its source genuinely participates in
    the hash rather than merely being listed."""
    lens = _fresh_lens()
    full = compute_cassette_code_hash(lens)
    assert full == compute_cassette_code_hash(lens)  # deterministic
    reduced = tuple(m for m in _GOVERNANCE_CODE_MODULES
                    if m != "regulatory_checks")
    monkeypatch.setattr(cassette_forensics, "_GOVERNANCE_CODE_MODULES", reduced)
    assert compute_cassette_code_hash(lens) != full
