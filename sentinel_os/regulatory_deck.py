"""
Regulatory Deck -- where an auditor's lens is inserted and runs.

The deck is the machine the regulatory cassettes go into. It owns the
three guarantees the framework makes to an examiner:

1. INSERTION IS ON THE RECORD. Inserting (or removing) a lens writes a
   first-class hash-chained ledger event
   (regulatory_cassette_inserted / regulatory_cassette_removed: who,
   when, which lens identity, which mode, which content + code hash)
   via governance/ledger_postgres.record_regulatory_cassette_event.
   "When was the CFPB lens active" is a direct ledger query, not an
   inference from an overloaded field.

2. THE LENS IS WHAT IT SAYS IT IS. Insertion content-binds the lens's
   full configuration snapshot through the SAME
   bind_cassette_version machinery domain cassettes already use --
   same tripwire, same guarantee: a lens whose configuration changed
   under an unchanged version string is refused loud at insertion.

3. NO SILENT LIVE ACTION, EVER. When a LIVE-mode lens flags or blocks
   an episode's judgment, that action is itself written to the ledger
   as a regulatory_disclosure event -- naming the regulation and the
   specific check -- BEFORE the action takes effect, on the same hash
   chain as everything else (no parallel logging path). If the
   disclosure write fails, the action does not happen quietly: the
   failure propagates and judgment does not proceed. This is the
   framework's non-negotiable safeguard: fairness- or
   compliance-driven steering of outputs that the record cannot see
   is treated here as forbidden, full stop -- which is also the
   posture the FTC's July 2026 Section 5 proposal takes toward
   undisclosed output-steering. Disclosure is not hardening; it is
   the condition for live mode existing at all.

Judge, not actor: in live mode a "block" means the deck refuses to
return judgment for the episode until a human reviews the disclosed
findings (RegulatoryBlock). The deck never reaches into the deciding
system and never rewrites an outcome. There is no adjustment path in
this deck; the disclosure vocabulary reserves "adjust" so that if one
is ever built, it cannot be built without a disclosure event type
already waiting for it.

Observer mode is exactly as safe as it sounds: review of decisions
already in the immutable ledger, producing a report. The only ledger
writes an observer-mode lens ever causes are its own insertion and
removal events.

CROSS-LENS TIER CONFLICTS (live mode only): when two or more LIVE
lenses each flag the same input variable with a DIFFERENT declared
authorization tier (regulatory_checks.check_input_authorization_tier,
C2 dimension 2), that disagreement is itself a finding an examiner
needs to see -- silently letting whichever lens happened to run first
"win" would hide a real jurisdictional conflict. Detection is
STRUCTURAL, not tied to any one check's name: any finding carrying
both a "variable" and a "tier" key in its evidence is treated as a
tier claim, from whichever lens and whichever check produced it.
resolve_tier_conflict's stricter-tier-wins rule (regulatory_checks)
decides which tier stands; a single cross_lens_tier_conflict finding
is raised, disclosed like any other live finding, and attributed to
whichever lens already held the resolved (stricter) tier -- the
other lens's claim is superseded on the record, never silently
dropped. Always ACTION_FLAG: a conflict between two lenses' own
claims is a fact for a human to resolve, not grounds for this deck to
unilaterally block judgment on either lens's behalf.

COHORT EQUITY ESCALATION (live mode only, opt-in): when this deck is
constructed with both twin_client and replica_id, judge()/explain()
check whether the domain cassette being judged declares
CAPABILITY_OUTCOME_OBLIGATION (cassette_capabilities) and, if so,
fetch the most recently recorded cohort_equity_review for that
episode's (domain, obligation_kind) from the twin. A FLAGGED review
produces one c2_cohort_equity_rollup finding per live lens that
exposes c2_rollup -- always ACTION_FLAG, never ACTION_BLOCK (Wm,
2026-07-31: escalate for human review, never touch the automated
score -- the deck's existing "findings ride NEXT TO the judgment"
guarantee, see GovernedJudgment, already enforces this). Today this
only fires for the mortgage cassette: it is the only cassette
declaring CAPABILITY_OUTCOME_OBLIGATION, so it is the only one with
any cohort data to flag. The mechanism itself stays domain-agnostic --
any future cassette that enables outcome_obligation gets this for
free, no new code here. When twin_client/replica_id are not supplied
(the default), this step never runs and judge()/explain() make no
network call, exactly as before this was added -- reaching the twin
on every live decision is the real latency/reliability trade
obligation_sweep.fetch_latest_cohort_review's own docstring already
flagged, so it stays opt-in rather than always-on. A fetch failure
(twin unreachable, etc.) is caught and treated the same as "no review
recorded yet" -- fails OPEN, so a twin outage degrades this one
optional signal, never live judgment itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from cassette_capabilities import CAPABILITY_OUTCOME_OBLIGATION
from cassette_forensics import compute_cassette_code_hash, compute_cassette_hash
from episode import Episode, explain_episode, judge_episode
from obligation_sweep import fetch_latest_cohort_review
from regulatory_cassette_interface import (
    ACTION_BLOCK,
    ACTION_FLAG,
    MODE_LIVE,
    MODE_OBSERVER,
    REGULATORY_MODES,
    RegulatoryBlock,
    RegulatoryCassette,
    RegulatoryFinding,
    SCREENING_DISCLAIMER,
    material_from_episode,
    material_from_ledger_row,
    regulatory_cassette_version_of,
    validate_regulatory_cassette,
)
from regulatory_checks import C2_FLAG, resolve_tier_conflict


@dataclass(frozen=True)
class InsertedLens:
    """One lens as inserted: the object plus the recorded facts of its
    insertion (mode, hashes, who). What active() reports and what
    disclosure events cite."""

    identity: str
    lens: RegulatoryCassette
    mode: str
    regulation: str
    cassette_hash: str
    cassette_code_hash: str
    inserted_by: str


@dataclass(frozen=True)
class GovernedJudgment:
    """What RegulatoryDeck.judge returns when judgment proceeds: the
    domain cassette's own QualityResult, untouched, plus every
    regulatory finding that was raised (and disclosed) along the way.
    The findings ride NEXT TO the judgment, never inside it -- a lens
    reviews the decision, it does not move the score."""

    quality: Any
    findings: Tuple[RegulatoryFinding, ...]


class RegulatoryDeck:
    """Insertion point and runtime for regulatory lenses.

    A ledger is REQUIRED, not optional: without one, insertion cannot
    be evidenced and live-mode disclosure cannot exist, and both are
    the point. There is deliberately no offline construction path --
    a deck that cannot write its record is not a deck, in exactly the
    way an unbound cassette is not policy. (Tests exercising failure
    behavior inject a stub ledger; production passes the real
    PostgreSQLLedger.)
    """

    def __init__(self, ledger, default_authorized_by: Optional[str] = None,
                 twin_client=None, replica_id: Optional[str] = None):
        if ledger is None:
            raise ValueError(
                "RegulatoryDeck requires a ledger: lens insertion events and "
                "live-mode disclosure logging are non-negotiable, and both "
                "need somewhere tamper-evident to land. There is no "
                "ledgerless mode."
            )
        self.ledger = ledger
        self.default_authorized_by = default_authorized_by
        # Both required together, opt-in, default off -- see module
        # docstring's COHORT EQUITY ESCALATION section. Neither alone is
        # enough to make a real fetch_latest_cohort_review call.
        self._twin_client = twin_client
        self._replica_id = replica_id
        self._active: Dict[str, InsertedLens] = {}

    # ------------------------------------------------------------------
    # Insertion / removal -- the auditor-facing surface
    # ------------------------------------------------------------------

    def insert(self, lens: RegulatoryCassette, mode: str,
               inserted_by: Optional[str] = None) -> Dict[str, Any]:
        """Insert a lens in the given mode.

        Order matters and is deliberate: validate (fail-loud), hash,
        BIND (the content tripwire fires here, before anything is
        active), then write the insertion event, then activate. A lens
        that fails any step is never active and never was.
        """
        if mode not in REGULATORY_MODES:
            raise ValueError(
                f"Unknown regulatory mode '{mode}'; known: {list(REGULATORY_MODES)}"
            )

        snapshot = validate_regulatory_cassette(lens)
        if mode not in lens.modes():
            raise ValueError(
                f"Lens '{regulatory_cassette_version_of(lens)}' does not "
                f"support mode '{mode}' (declared MODES: {list(lens.modes())}). "
                f"A lens attaches only in a mode it explicitly declared."
            )

        identity = regulatory_cassette_version_of(lens)
        if identity in self._active:
            raise ValueError(
                f"Lens '{identity}' is already inserted (mode "
                f"'{self._active[identity].mode}'). Remove it first; a second "
                f"concurrent insertion of the same identity would make the "
                f"active-window record ambiguous."
            )

        who = inserted_by or self.default_authorized_by
        if not who or not str(who).strip():
            raise ValueError(
                "Insertion requires an identity (inserted_by): the insertion "
                "event records WHO inserted the lens, and an anonymous "
                "insertion is exactly the record an examiner cannot accept."
            )

        cassette_hash = compute_cassette_hash(snapshot)
        cassette_code_hash = compute_cassette_code_hash(lens)
        regulation = snapshot["regulation"]

        # Same tamper-evidence machinery domain cassettes use -- the
        # version string becomes a content commitment the first time
        # this identity is bound, and a changed lens presenting the
        # same string is refused loud right here.
        self.ledger.bind_cassette_version(
            identity, cassette_hash,
            cassette_code_hash=cassette_code_hash,
            authorized_by=str(who),
        )

        event = self.ledger.record_regulatory_cassette_event(
            event="regulatory_cassette_inserted",
            cassette_version=identity,
            cassette_hash=cassette_hash,
            cassette_code_hash=cassette_code_hash,
            mode=mode,
            regulation=regulation,
            authorized_by=str(who),
        )

        entry = InsertedLens(
            identity=identity, lens=lens, mode=mode, regulation=regulation,
            cassette_hash=cassette_hash, cassette_code_hash=cassette_code_hash,
            inserted_by=str(who),
        )
        self._active[identity] = entry
        return {
            "identity": identity,
            "mode": mode,
            "regulation": regulation,
            "cassette_hash": cassette_hash,
            "cassette_code_hash": cassette_code_hash,
            "inserted_by": str(who),
            "insertion_event_hash": event.get("current_hash"),
        }

    def remove(self, identity: str, removed_by: Optional[str] = None) -> Dict[str, Any]:
        """Remove an inserted lens, on the record. The removal event is
        what lets 'when was this lens active' have an end as well as a
        beginning."""
        if identity not in self._active:
            raise KeyError(
                f"No inserted lens '{identity}'; active: {sorted(self._active)}"
            )
        who = removed_by or self.default_authorized_by
        if not who or not str(who).strip():
            raise ValueError(
                "Removal requires an identity (removed_by): the active-window "
                "record needs who closed it, not just who opened it."
            )
        entry = self._active[identity]
        event = self.ledger.record_regulatory_cassette_event(
            event="regulatory_cassette_removed",
            cassette_version=identity,
            cassette_hash=entry.cassette_hash,
            cassette_code_hash=entry.cassette_code_hash,
            mode=entry.mode,
            regulation=entry.regulation,
            authorized_by=str(who),
        )
        del self._active[identity]
        return {
            "identity": identity,
            "removed_by": str(who),
            "removal_event_hash": event.get("current_hash"),
        }

    def active(self) -> List[Dict[str, Any]]:
        """The currently inserted lenses, as recorded facts."""
        return [
            {
                "identity": e.identity, "mode": e.mode,
                "regulation": e.regulation, "cassette_hash": e.cassette_hash,
                "inserted_by": e.inserted_by,
            }
            for e in self._active.values()
        ]

    # ------------------------------------------------------------------
    # Observer mode -- read-only review of the recorded ledger
    # ------------------------------------------------------------------

    def observer_review(self, limit: int = 100,
                        decision_cassette_version: Optional[str] = None
                        ) -> Dict[str, Any]:
        """Run every inserted lens over recorded governance decisions.

        Read-only by construction: decisions are fetched through the
        ledger's existing read path and findings are RETURNED, never
        written. (Live-mode disclosure exists because live findings
        alter what happens next; an observer report alters nothing, so
        writing it into the chain would only manufacture noise.) Both
        observer- and live-inserted lenses participate -- reading the
        record is harmless in either mode.
        """
        decisions = self.ledger.get_decisions(
            cassette_version=decision_cassette_version, limit=limit,
        )
        report_lenses: List[Dict[str, Any]] = []
        for entry in self._active.values():
            findings: List[RegulatoryFinding] = []
            flagged_subjects = set()
            for row in decisions:
                material = material_from_ledger_row(row)
                for finding in entry.lens.review(material):
                    findings.append(finding)
                    flagged_subjects.add(finding.subject_id)
            report_lenses.append({
                "identity": entry.identity,
                "mode": entry.mode,
                "regulation": entry.regulation,
                "cassette_hash": entry.cassette_hash,
                "decisions_reviewed": len(decisions),
                "decisions_flagged": len(flagged_subjects),
                "findings": [f.as_dict() for f in findings],
            })
        return {
            "review_mode": MODE_OBSERVER,
            "disclaimer": SCREENING_DISCLAIMER,
            "lenses": report_lenses,
        }

    # ------------------------------------------------------------------
    # Live mode -- the kernel judgment path, with disclosure-first
    # ------------------------------------------------------------------

    def _live_entries(self) -> List[InsertedLens]:
        return [e for e in self._active.values() if e.mode == MODE_LIVE]

    def _disclose(self, entry: InsertedLens, finding: RegulatoryFinding) -> None:
        """Write one finding's disclosure event. Deliberately NO
        try/except: a failed disclosure write must abort the action it
        would have disclosed. Silence is the one outcome this method
        is not allowed to produce."""
        self.ledger.record_regulatory_disclosure(
            cassette_version=entry.identity,
            regulation=entry.regulation,
            check=finding.check,
            action=finding.action,
            subject_id=finding.subject_id,
            finding=finding.as_dict(),
            cassette_hash=entry.cassette_hash,
            authorized_by=entry.inserted_by,
        )

    def _cross_lens_tier_conflicts(
            self, entries_and_findings: List[Tuple[InsertedLens, RegulatoryFinding]],
            ) -> List[Tuple[InsertedLens, RegulatoryFinding]]:
        """Structural cross-lens tier-conflict detection over one
        episode's already-collected (lens, finding) pairs -- see the
        module docstring. Any finding carrying both "variable" and
        "tier" in its evidence is a tier claim, regardless of which
        check or lens produced it. When two or more DIFFERENT lenses
        hold conflicting tier claims for the SAME variable, folds them
        pairwise through resolve_tier_conflict to find the resolved
        (stricter) tier, and returns one cross_lens_tier_conflict
        finding per conflicting variable, paired with the lens whose
        own claim matches that resolved tier (the finding is disclosed
        under that lens's identity). Two lenses agreeing on a variable
        is not a conflict and produces nothing.
        """
        by_variable: Dict[str, Dict[str, Tuple[InsertedLens, RegulatoryFinding]]] = {}
        for entry, finding in entries_and_findings:
            variable = finding.evidence.get("variable")
            tier = finding.evidence.get("tier")
            if variable is None or tier is None:
                continue
            # Keyed by lens identity: a lens flagging the same variable
            # via more than one check reports the same tier every time
            # (assess_input_authorization is deterministic per name),
            # so last-write-wins here is harmless, not a data loss.
            by_variable.setdefault(variable, {})[entry.identity] = (entry, finding)

        conflicts: List[Tuple[InsertedLens, RegulatoryFinding]] = []
        for variable, claims in sorted(by_variable.items()):
            if len(claims) < 2:
                continue
            ordered = [claims[identity] for identity in sorted(claims)]
            resolved_tier = ordered[0][1].evidence["tier"]
            winner_entry, winner_finding = ordered[0]
            tiers_seen = {resolved_tier}
            for entry, finding in ordered[1:]:
                tier = finding.evidence["tier"]
                tiers_seen.add(tier)
                new_resolved = resolve_tier_conflict(resolved_tier, tier)
                if new_resolved != resolved_tier:
                    winner_entry, winner_finding = entry, finding
                resolved_tier = new_resolved
            if len(tiers_seen) < 2:
                continue  # every lens claiming this variable agrees
            conflicts.append((winner_entry, RegulatoryFinding(
                check="cross_lens_tier_conflict",
                subject_id=winner_finding.subject_id,
                regulation=winner_entry.regulation,
                action=ACTION_FLAG,
                classification="cross_lens_tier_conflict",
                score=1.0,
                evidence={
                    "variable": variable,
                    "resolved_tier": resolved_tier,
                    "conflicting_claims": [
                        {"lens": entry.identity, "regulation": entry.regulation,
                         "tier": finding.evidence["tier"]}
                        for entry, finding in ordered
                    ],
                    "detail": f"live lenses disagree on input variable "
                              f"'{variable}''s authorization tier; the "
                              f"stricter tier ('{resolved_tier}') governs "
                              "per the cross-jurisdiction conflict rule "
                              "(resolve_tier_conflict) and is attributed to "
                              "the lens that already held it -- the other "
                              "lens's claim is superseded on the record, "
                              "not silently dropped",
                    "score_meaning": "1.0 = a real disagreement was "
                                     "detected between live lenses; not a "
                                     "measure of how severe it is",
                },
            )))
        return conflicts

    def _cohort_equity_escalations(
            self, domain_cassette, material,
            ) -> List[Tuple[InsertedLens, RegulatoryFinding]]:
        """Cohort-level equity escalation for one live decision -- see
        the module docstring's COHORT EQUITY ESCALATION section for
        the design (Wm, 2026-07-31): always ACTION_FLAG, never
        ACTION_BLOCK, and a complete no-op unless this deck was built
        with both twin_client and replica_id. Returns one
        (entry, finding) pair per live lens that exposes c2_rollup,
        and only when the fetched review's status is C2_FLAG -- a
        PASS or INDETERMINATE review is not escalation-worthy.
        """
        if self._twin_client is None or self._replica_id is None:
            return []
        if CAPABILITY_OUTCOME_OBLIGATION not in getattr(
                domain_cassette, "CAPABILITIES", ()):
            return []
        get_rule = getattr(domain_cassette, "get_maturation_rule", None)
        if get_rule is None:
            return []
        obligation_kind = get_rule().kind
        try:
            review = fetch_latest_cohort_review(
                self._twin_client, self._replica_id, material.domain,
                obligation_kind)
        except Exception:
            # Fail OPEN -- see module docstring: a twin-reachability
            # problem degrades this one optional signal, never live
            # judgment itself. Same posture as "no review recorded yet".
            return []
        if review is None:
            return []
        dim4 = [RegulatoryFinding(**f)
                for f in review.get("dimension_4_findings", [])]
        dim5 = [RegulatoryFinding(**f)
                for f in review.get("dimension_5_findings", [])]
        escalations: List[Tuple[InsertedLens, RegulatoryFinding]] = []
        for entry in self._live_entries():
            rollup_fn = getattr(entry.lens, "c2_rollup", None)
            if rollup_fn is None:
                continue
            rollup = rollup_fn(
                material, statistical_outcome_equity_findings=dim4,
                correlation_proxy_findings=dim5)
            if rollup.status != C2_FLAG:
                continue
            escalations.append((entry, RegulatoryFinding(
                check="c2_cohort_equity_rollup",
                subject_id=material.subject_id,
                regulation=entry.regulation,
                action=ACTION_FLAG,
                classification="cohort_equity_flag",
                score=1.0,
                evidence={
                    "domain": material.domain,
                    "obligation_kind": obligation_kind,
                    "flagged_dimensions": list(rollup.flagged_dimensions),
                    "detail": f"this decision's cohort ({material.domain}, "
                              f"{obligation_kind}) carries a FLAGGED C2 "
                              "equity finding from the most recent sweep; "
                              "routed for human review, the automated "
                              "judgment score is unaffected",
                    "score_meaning": "1.0 = a flagged cohort review was "
                                     "found for this decision's cohort; "
                                     "not a measure of severity",
                },
            )))
        return escalations

    def judge(self, domain_cassette, episode: Episode) -> GovernedJudgment:
        """The live-governed judgment entry point.

        Kernel validation and domain judgment run first (through
        episode.judge_episode -- no judgment path admits an
        unvalidated episode, this one included). Then every LIVE lens
        reviews the episode; EVERY finding is disclosed to the ledger
        as it is raised. Cross-lens tier conflicts (see module
        docstring) are detected and disclosed next, once every lens's
        own findings are already on the record. Cohort equity
        escalation (see module docstring) runs after that, opt-in and
        a no-op unless this deck was built with twin_client/replica_id.
        Only after ALL of that -- lens findings, any conflict findings,
        and any cohort escalation alike -- is on the chain does a block
        take effect (RegulatoryBlock); note that cohort escalation
        findings are always ACTION_FLAG, so they never themselves
        trigger a block. The chain always holds the complete picture of
        what was seen, not just the first thing that stopped the music.
        """
        quality = judge_episode(domain_cassette, episode)
        all_findings: List[RegulatoryFinding] = []
        entries_and_findings: List[Tuple[InsertedLens, RegulatoryFinding]] = []
        blocking: List[RegulatoryFinding] = []
        blocking_entry: Optional[InsertedLens] = None
        for entry in self._live_entries():
            for finding in entry.lens.review(material_from_episode(episode)):
                self._disclose(entry, finding)  # fail-closed: may raise
                all_findings.append(finding)
                entries_and_findings.append((entry, finding))
                if finding.action == ACTION_BLOCK:
                    blocking.append(finding)
                    if blocking_entry is None:
                        blocking_entry = entry
        for entry, conflict in self._cross_lens_tier_conflicts(entries_and_findings):
            self._disclose(entry, conflict)  # fail-closed, same as any other finding
            all_findings.append(conflict)
        for entry, escalation in self._cohort_equity_escalations(
                domain_cassette, material_from_episode(episode)):
            self._disclose(entry, escalation)  # fail-closed, same as any other finding
            all_findings.append(escalation)
        if blocking:
            raise RegulatoryBlock(
                lens_identity=blocking_entry.identity,
                regulation=blocking_entry.regulation,
                findings=tuple(all_findings),
            )
        return GovernedJudgment(quality=quality, findings=tuple(all_findings))

    def explain(self, domain_cassette, episode: Episode) -> List[Dict[str, Any]]:
        """Kernel explanation plus regulatory findings as factors.

        Read-only, like the kernel's own explain: findings (including
        any cross-lens tier-conflict finding and any cohort equity
        escalation) appear as "regulatory_finding" factor entries but
        are NOT disclosed to the ledger here, because explanation is a
        reporting surface -- nothing about the episode's handling
        changes when it is explained. judge() is the decision path, and
        the decision path is where disclosure is owed. (Explaining an
        episode twice must not write two more ledger rows -- and, for
        cohort equity escalation specifically, must not make two more
        twin fetches disclose anything either; explain() never calls
        _disclose at all, so this falls out for free.)
        """
        factors = explain_episode(domain_cassette, episode)
        material = material_from_episode(episode)
        entries_and_findings: List[Tuple[InsertedLens, RegulatoryFinding]] = []
        for entry in self._live_entries():
            for finding in entry.lens.review(material):
                factors.append({
                    "factor": "regulatory_finding",
                    "lens": entry.identity,
                    **finding.as_dict(),
                })
                entries_and_findings.append((entry, finding))
        for entry, conflict in self._cross_lens_tier_conflicts(entries_and_findings):
            factors.append({
                "factor": "regulatory_finding",
                "lens": entry.identity,
                **conflict.as_dict(),
            })
        for entry, escalation in self._cohort_equity_escalations(
                domain_cassette, material):
            factors.append({
                "factor": "regulatory_finding",
                "lens": entry.identity,
                **escalation.as_dict(),
            })
        return factors
