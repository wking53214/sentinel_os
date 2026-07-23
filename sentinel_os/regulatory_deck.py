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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from cassette_forensics import compute_cassette_code_hash, compute_cassette_hash
from episode import Episode, explain_episode, judge_episode
from regulatory_cassette_interface import (
    ACTION_BLOCK,
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

    def __init__(self, ledger, default_authorized_by: Optional[str] = None):
        if ledger is None:
            raise ValueError(
                "RegulatoryDeck requires a ledger: lens insertion events and "
                "live-mode disclosure logging are non-negotiable, and both "
                "need somewhere tamper-evident to land. There is no "
                "ledgerless mode."
            )
        self.ledger = ledger
        self.default_authorized_by = default_authorized_by
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

    def judge(self, domain_cassette, episode: Episode) -> GovernedJudgment:
        """The live-governed judgment entry point.

        Kernel validation and domain judgment run first (through
        episode.judge_episode -- no judgment path admits an
        unvalidated episode, this one included). Then every LIVE lens
        reviews the episode; EVERY finding is disclosed to the ledger
        as it is raised. Only after all findings from all lenses are
        on the record does a block take effect (RegulatoryBlock), so
        the chain always holds the complete picture of what the lenses
        saw, not just the first thing that stopped the music.
        """
        quality = judge_episode(domain_cassette, episode)
        all_findings: List[RegulatoryFinding] = []
        blocking: List[RegulatoryFinding] = []
        blocking_entry: Optional[InsertedLens] = None
        for entry in self._live_entries():
            for finding in entry.lens.review(material_from_episode(episode)):
                self._disclose(entry, finding)  # fail-closed: may raise
                all_findings.append(finding)
                if finding.action == ACTION_BLOCK:
                    blocking.append(finding)
                    if blocking_entry is None:
                        blocking_entry = entry
        if blocking:
            raise RegulatoryBlock(
                lens_identity=blocking_entry.identity,
                regulation=blocking_entry.regulation,
                findings=tuple(all_findings),
            )
        return GovernedJudgment(quality=quality, findings=tuple(all_findings))

    def explain(self, domain_cassette, episode: Episode) -> List[Dict[str, Any]]:
        """Kernel explanation plus regulatory findings as factors.

        Read-only, like the kernel's own explain: findings appear as
        "regulatory_finding" factor entries but are NOT disclosed to
        the ledger here, because explanation is a reporting surface --
        nothing about the episode's handling changes when it is
        explained. judge() is the decision path, and the decision path
        is where disclosure is owed. (Explaining an episode twice must
        not write two more ledger rows.)
        """
        factors = explain_episode(domain_cassette, episode)
        for entry in self._live_entries():
            for finding in entry.lens.review(material_from_episode(episode)):
                factors.append({
                    "factor": "regulatory_finding",
                    "lens": entry.identity,
                    **finding.as_dict(),
                })
        return factors
