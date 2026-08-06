"""
report.py -- render the record as something a room can read.

The JSON in realignment.py is the artifact of record. This is the
version people actually sit around a table with. It is generated from
the record rather than written alongside it, so the document in the
meeting and the document in the archive can never disagree.

Two renderings:

  monthly_drift_report -- the between-meeting check. Short. Its job is
      to be read in two minutes and either reassure or interrupt.
  annual_realignment_report -- the meeting document. Leads with the
      decision that needs making, then the evidence behind it.

Both put unknowns and flagged zones ABOVE the healthy ones. A report
that opens with everything that is fine trains people to stop reading
before the part that is not.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .drift import DriftReport, ZoneDrift, calibration_suggestion
from .realignment import RealignmentRecord, RealignmentTrail

_STATE_MARK = {
    "OK": "[ OK ]",
    "WATCH": "[WATCH]",
    "BREACH": "[BREACH]",
    "UNKNOWN": "[ ?? ]",
}


def _pct(value: Optional[float]) -> str:
    return f"{value:.1%}" if value is not None else "no data"


def _delta(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1%}"


def _zone_rows(zones: Sequence[ZoneDrift]) -> List[str]:
    # Flagged first. See module docstring.
    ordered = sorted(zones, key=lambda z: (not z.flagged, z.zone))
    lines = [
        "| Zone | State | Alignment | Change | Decided | Declined | Errors |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for z in ordered:
        lines.append(
            f"| {z.zone} | {_STATE_MARK.get(z.state, z.state)} | {_pct(z.alignment)} | "
            f"{_delta(z.delta)} | {z.decided} | {z.indeterminate} | {z.errored} |"
        )
    return lines


def monthly_drift_report(report: DriftReport) -> str:
    lines: List[str] = [
        f"# Drift check: {report.regulation_id}",
        "",
        f"Interpretation {report.interpretation_version} | run {report.run_id} | {report.generated_at}",
        "",
        f"**Risk: {report.risk_level}** | Overall alignment: {_pct(report.overall_alignment)}",
        "",
    ]

    flagged = report.flagged_zones
    if flagged:
        lines += [
            f"## Needs attention ({len(flagged)})",
            "",
        ]
        for z in flagged:
            if z.state == "UNKNOWN":
                lines.append(
                    f"- **{z.zone}**: no decided scenarios this run "
                    f"({z.indeterminate} declined, {z.errored} errored). "
                    f"This is not a pass. The zone has no evidence behind it."
                )
            else:
                lines.append(
                    f"- **{z.zone}**: {_pct(z.alignment)} ({_delta(z.delta)}), "
                    f"{z.mismatched} scenario(s) answered differently than locked. "
                    f"Examples: {', '.join(z.mismatch_examples) or 'n/a'}"
                )
        lines.append("")
    else:
        lines += ["All zones within tolerance. No action required this month.", ""]

    lines += ["## All zones", ""]
    lines += _zone_rows(report.zones)
    lines += [
        "",
        "---",
        "",
        "*Alignment is measured against scenarios Sentinel took a position on. "
        "Declined scenarios are counted separately and are never scored as "
        "passes or failures. This report shows whether Sentinel still applies "
        "the reading Legal locked in; it does not assess whether that reading "
        "is correct.*",
    ]
    return "\n".join(lines)


def annual_realignment_report(
    record: RealignmentRecord,
    trail: Optional[RealignmentTrail] = None,
    history: Optional[Sequence[DriftReport]] = None,
) -> str:
    quick = record.quick_view()
    lines: List[str] = [
        f"# Annual realignment: {record.regulation_id}",
        "",
        f"Record {record.record_id} | Interpretation {record.interpretation_version} "
        f"| Meeting {record.realignment_date} | Trigger {record.trigger}",
        "",
        "## Decision",
        "",
        f"| | |",
        f"| --- | --- |",
        f"| Legal sign-off | **{quick['legal_sign_off']}** |",
        f"| Decision | **{quick['decision']}** |",
        f"| Risk level | **{quick['risk_level']}** |",
        f"| Flagged zones | {', '.join(quick['flagged_zones']) or 'none'} |",
        f"| Interpretation active from | {record.activation_date} |",
        f"| Record sealed | {'yes' if record.verify() else 'NO - hash does not verify'} |",
        "",
        f"{record.decision_rationale}",
        "",
    ]

    if record.version_change:
        vc = record.version_change
        lines += [
            f"## Interpretation change: {vc.from_version} to {vc.to_version}",
            "",
            f"Active from {vc.activation_date}. Reason: {vc.reason}",
            "",
            "What changed:",
        ]
        lines += [f"- {c}" for c in vc.changes] or ["- (not itemized)"]
        lines += [
            "",
            f"Decisions made under {vc.from_version}: "
            f"{vc.decisions_affected if vc.decisions_affected is not None else 'not counted'} "
            f"({vc.affected_date_range or 'range not recorded'}). "
            f"Those decisions remain governed by {vc.from_version}.",
            "",
            f"Retroactive re-test: **{vc.retroactive_retest}**"
            + (f" ({vc.retroactive_scope})" if vc.retroactive_scope else ""),
            "",
        ]
        if vc.known_deltas:
            lines += ["Known behavioral differences:", ""]
            lines += [f"- {d}" for d in vc.known_deltas]
            lines.append("")

    lines += [
        "## Why (narrative)",
        "",
        "**Context.** " + record.context,
        "",
        "**Business rationale.** " + record.business_rationale,
        "",
        "**Legal assessment.** " + record.legal_assessment,
        "",
    ]

    if record.regulatory_events:
        lines += ["## Regulatory events since last realignment", ""]
        lines += [
            "| Date | Agency | Event | Impact |",
            "| --- | --- | --- | --- |",
        ]
        for e in record.regulatory_events:
            lines.append(f"| {e.date} | {e.agency} | {e.event} | {e.impact_on_interpretation} |")
        lines.append("")

    if record.drift:
        lines += [
            "## Evidence: zone alignment",
            "",
            f"Overall alignment: {_pct(record.drift.overall_alignment)}",
            "",
        ]
        lines += _zone_rows(record.drift.zones)
        lines.append("")

    if trail:
        trend_lines = _multi_year_block(record, trail)
        if trend_lines:
            lines += trend_lines

    if record.outcome_correlation:
        lines += ["## Outcome correlation", ""]
        for key, value in sorted(record.outcome_correlation.items()):
            lines.append(f"- **{key}**: {value}")
        lines.append("")

    if history:
        calib = calibration_suggestion(list(history))
        status = calib.get("__status__", {})
        lines += ["## Tolerance calibration", ""]
        if not status.get("ready"):
            lines += [
                f"Not enough history to suggest thresholds ({status.get('reason', 'unknown')}). "
                "Zones remain on the conservative default, where any mismatch flags.",
                "",
            ]
        else:
            lines += [
                f"Based on {status.get('periods_observed')} observation periods. "
                "These are descriptions of past behavior, not a judgment that past "
                "behavior was acceptable. Setting a threshold is a business decision "
                "and is recorded with an owner and a rationale.",
                "",
                "| Zone | Observed floor | Observed mean | Suggested watch | Suggested breach |",
                "| --- | --- | --- | --- | --- |",
            ]
            for zone, data in sorted(calib.items()):
                if zone == "__status__":
                    continue
                lines.append(
                    f"| {zone} | {data['observed_min']:.1%} | {data['observed_mean']:.1%} | "
                    f"{data['suggested_watch_below']:.1%} | {data['suggested_breach_below']:.1%} |"
                )
            lines.append("")

    if record.open_questions:
        lines += ["## Open questions carried forward", ""]
        lines += [f"- {q}" for q in record.open_questions]
        lines.append("")

    lines += ["## Approvals", "", "| Name | Title | Date |", "| --- | --- | --- |"]
    for a in record.approved_by:
        lines.append(f"| {a.name} | {a.title} | {a.date} |")
    lines += [
        "",
        f"Record hash: `{record.record_hash or 'UNSEALED'}`",
        "",
        "---",
        "",
        "*This record states whether Sentinel still applies the reading this "
        "organization chose. It is not a compliance determination and does not "
        "assess whether that reading satisfies the regulation.*",
    ]
    return "\n".join(lines)


def _multi_year_block(record: RealignmentRecord, trail: RealignmentTrail) -> List[str]:
    """The five-year view. Any single year can look fine while the line
    slopes down, which is the specific thing the annual meeting exists to
    catch."""
    history = trail.history(record.regulation_id)
    if len(history) < 2:
        return []

    lines = [
        "## Multi-year trend",
        "",
        "| Date | Version | Decision | Overall alignment | Risk | Sealed |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in history:
        lines.append(
            f"| {row['realignment_date'][:10]} | {row['interpretation_version']} | "
            f"{row['decision']} | {_pct(row['overall_alignment'])} | {row['risk_level']} | "
            f"{'yes' if row['sealed'] else 'NO'} |"
        )
    lines.append("")

    zones = sorted({z.zone for z in record.drift.zones}) if record.drift else []
    slipping: List[str] = []
    for zone in zones:
        trend = [t for t in trail.zone_trend(record.regulation_id, zone) if t["alignment"] is not None]
        if len(trend) >= 2 and trend[-1]["alignment"] < trend[0]["alignment"]:
            slipping.append(
                f"- **{zone}**: {_pct(trend[0]['alignment'])} "
                f"({trend[0]['date'][:10]}) to {_pct(trend[-1]['alignment'])} "
                f"({trend[-1]['date'][:10]})"
            )
    if slipping:
        lines += [
            "Zones lower now than at their first recorded realignment. Each may sit "
            "inside its yearly tolerance while still sliding across years:",
            "",
        ]
        lines += slipping
        lines.append("")

    unsealed = trail.unsealed()
    if unsealed:
        lines += [
            f"**Integrity warning:** {len(unsealed)} record(s) no longer verify against "
            f"their seal: {', '.join(unsealed)}. Resolve before relying on this trend.",
            "",
        ]
    return lines
