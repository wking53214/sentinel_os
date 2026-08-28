"""
Recommendation Impact Testing (predictive-accuracy scope) -- Wm's July 28
roadmap item 4, third piece, 2026-07-31.

SCOPE (Wm's decision, 2026-07-31): predictive-accuracy testing, not true
A/B impact testing. Every recommendation this module generates is a SHADOW
RUN -- computed from real ledger data, recorded to the chain via
governance.ledger_postgres.PostgreSQLLedger.record_recommendation_shadow_run,
and NEVER acted on. True impact testing (actually applying a recommendation
and comparing outcomes) would require wiring decide_healing_bounds /
decide_queue_reordering into the live decision path first -- a separate,
much bigger product decision. Only safety_check runs live today (see
claude_governance_api.py's module docstring and production_harness.py).

Two recommendation kinds are shadow-run here:
  - healing_bounds: ClaudeGovernanceDecider.decide_healing_bounds, real
    current/baseline wait pulled from the ledger.
  - queue_reordering: ClaudeGovernanceDecider.decide_queue_reordering, real
    success rates + caller-volume distribution pulled from the ledger.

decide_staffing_adjustment is DELIBERATELY EXCLUDED. It needs
current_agents -- real-time headcount -- and this system has no data
source for that anywhere. Fabricating a number to feed it would violate
the same "never guess, say why it's unknown" discipline the rest of this
codebase holds to (see event_v1.py's Provenance Rule). If a real staffing
data source is ever wired in, this module is the natural place to add a
third shadow-run function alongside these two.

Every pull is REAL, from
governance.ledger_postgres.PostgreSQLLedger.get_decisions_by_node_in_window
-- never simulated. A window with too little real data to trust returns
None (skip that queue this round), not a recommendation computed from thin
air, and not an exception that would abort every other queue's run too.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# Below this many real calls in a window, there isn't enough signal to
# trust an average -- skip rather than recommend from noise. Same
# "honestly insufficient, not a bug to route around" posture
# check_geographic_outcome_equity and friends already use for small
# cohorts (see regulatory_checks.py).
MIN_CALLS_FOR_A_WINDOW = 5

# quality_tier values (sentinel_core.OutcomeQuality) counted as a
# successful outcome for a success-rate computation. excellent/good are
# both "the call ended well, just with more or less friction"; poor/failed
# both indicate an outcome that wasn't right.
SUCCESS_TIERS = frozenset({"excellent", "good"})


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _wait_samples(ledger, node: str, since: datetime, until: datetime) -> List[float]:
    """Real per-call wait_time values for one node/queue in [since, until)."""
    rows = ledger.get_decisions_by_node_in_window(node, _iso(since), _iso(until))
    out = []
    for r in rows:
        inp = r.get("input_data")
        if isinstance(inp, dict) and isinstance(inp.get("wait_time"), (int, float)):
            out.append(float(inp["wait_time"]))
    return out


def _success_rate(ledger, node: str, since: datetime, until: datetime,
                  ) -> Optional[Dict[str, Any]]:
    """Real success rate for one node/queue in [since, until), or None if
    fewer than MIN_CALLS_FOR_A_WINDOW real calls exist there."""
    rows = ledger.get_decisions_by_node_in_window(node, _iso(since), _iso(until))
    tiers = []
    for r in rows:
        inp = r.get("input_data")
        if isinstance(inp, dict) and inp.get("quality_tier"):
            tiers.append(inp["quality_tier"])
    if len(tiers) < MIN_CALLS_FOR_A_WINDOW:
        return None
    successes = sum(1 for t in tiers if t in SUCCESS_TIERS)
    return {"success_rate": successes / len(tiers), "sample_size": len(tiers)}


def _caller_distribution(ledger, nodes: List[str], since: datetime, until: datetime,
                         ) -> Dict[str, int]:
    """Real call volume per queue -- decide_queue_reordering's
    caller_distribution parameter."""
    return {node: len(ledger.get_decisions_by_node_in_window(node, _iso(since), _iso(until)))
            for node in nodes}


# --------------------------------------------------------- healing bounds --
def run_healing_bounds_shadow(
        ledger, decider, node: str, cassette_version: str,
        now: Optional[datetime] = None,
        recent_window_s: float = 3600.0, baseline_window_s: float = 86400.0,
        ) -> Optional[Dict[str, Any]]:
    """Shadow-run decide_healing_bounds for one queue from real recent vs
    real baseline wait data.

    baseline_window_s is measured back from (now - recent_window_s), i.e.
    the two windows are adjacent, not overlapping -- "baseline" means the
    period just before the recent one, not a fixed historical reference.

    Returns None (skip, never a fabricated recommendation) if either
    window has fewer than MIN_CALLS_FOR_A_WINDOW real calls.
    """
    now = now or datetime.now(timezone.utc)
    recent = _wait_samples(ledger, node, now - timedelta(seconds=recent_window_s), now)
    baseline = _wait_samples(
        ledger, node, now - timedelta(seconds=recent_window_s + baseline_window_s),
        now - timedelta(seconds=recent_window_s))
    if len(recent) < MIN_CALLS_FOR_A_WINDOW or len(baseline) < MIN_CALLS_FOR_A_WINDOW:
        return None

    current_wait = sum(recent) / len(recent)
    baseline_wait = sum(baseline) / len(baseline)
    drift_magnitude = (
        abs(current_wait - baseline_wait) / baseline_wait if baseline_wait else 0.0)

    inputs = {"current_wait": current_wait, "baseline_wait": baseline_wait,
              "drift_magnitude": drift_magnitude,
              "recent_sample_size": len(recent), "baseline_sample_size": len(baseline)}
    recommendation = decider.decide_healing_bounds(
        node, current_wait, baseline_wait, drift_magnitude)
    return ledger.record_recommendation_shadow_run(
        recommendation_kind="healing_bounds", subject=node,
        cassette_version=cassette_version, inputs=inputs,
        recommendation=recommendation)


def score_healing_bounds_run(
        ledger, shadow_run: Dict[str, Any], now: Optional[datetime] = None,
        outcome_window_s: float = 3600.0) -> Optional[Dict[str, Any]]:
    """Compare a healing_bounds shadow run's predicted target_wait against
    the real wait data in the window AFTER it was made.

    Returns None (skip -- not enough real outcome data yet, or the
    governor's fail-closed path recommended nothing scoreable) rather than
    force a score out of too little or the wrong kind of data.
    """
    recommendation = shadow_run.get("recommendation")
    if not isinstance(recommendation, dict) or recommendation.get("target_wait") is None:
        return None
    made_at = datetime.fromisoformat(shadow_run["timestamp"])
    actual_samples = _wait_samples(ledger, shadow_run["subject"], made_at,
                                   made_at + timedelta(seconds=outcome_window_s))
    if len(actual_samples) < MIN_CALLS_FOR_A_WINDOW:
        return None

    actual_wait = sum(actual_samples) / len(actual_samples)
    predicted_wait = float(recommendation["target_wait"])
    error = abs(actual_wait - predicted_wait)
    # Directional check: did the wait actually move toward the predicted
    # target rather than staying at/beyond where it started? A cruder but
    # more honest signal than exact-value matching -- an Erlang-C-style
    # target is a reasonable estimate, not a guaranteed outcome, so
    # "closer to the target than before" is what a useful recommendation
    # should achieve even when it doesn't land exactly.
    current_wait = shadow_run.get("inputs", {}).get("current_wait")
    moved_toward_target = (
        None if current_wait is None else
        abs(actual_wait - predicted_wait) < abs(current_wait - predicted_wait))

    actual = {"actual_wait": actual_wait, "sample_size": len(actual_samples)}
    score = {"predicted_wait": predicted_wait, "actual_wait": actual_wait,
             "error_seconds": error, "moved_toward_target": moved_toward_target}
    return ledger.record_recommendation_shadow_score(
        shadow_run_hash=shadow_run["shadow_run_hash"], actual=actual, score=score)


# ------------------------------------------------------- queue reordering --
def run_queue_reordering_shadow(
        ledger, decider, nodes: List[str], cassette_version: str,
        now: Optional[datetime] = None, window_s: float = 86400.0,
        ) -> Optional[Dict[str, Any]]:
    """Shadow-run decide_queue_reordering across a set of queues from real
    success rates + real call-volume distribution.

    Returns None if fewer than 2 queues have enough real data to compare
    -- reordering is meaningless for zero or one queue.
    """
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(seconds=window_s)
    success_rates: Dict[str, float] = {}
    sample_sizes: Dict[str, int] = {}
    for node in nodes:
        stats = _success_rate(ledger, node, since, now)
        if stats is not None:
            success_rates[node] = stats["success_rate"]
            sample_sizes[node] = stats["sample_size"]
    if len(success_rates) < 2:
        return None
    caller_distribution = _caller_distribution(ledger, list(success_rates), since, now)

    inputs = {"success_rates": success_rates, "sample_sizes": sample_sizes,
              "caller_distribution": caller_distribution,
              "current_order": list(success_rates)}
    recommendation = decider.decide_queue_reordering(
        list(success_rates), success_rates, caller_distribution)
    return ledger.record_recommendation_shadow_run(
        recommendation_kind="queue_reordering",
        subject=",".join(sorted(success_rates)),
        cassette_version=cassette_version, inputs=inputs,
        recommendation=recommendation)


def score_queue_reordering_run(
        ledger, shadow_run: Dict[str, Any], now: Optional[datetime] = None,
        outcome_window_s: float = 86400.0) -> Optional[Dict[str, Any]]:
    """Compare a queue_reordering shadow run's prediction against real
    success-rate movement afterward, per queue.

    Reports what actually happened for every queue named in the
    recommendation rather than forcing a single pass/fail verdict onto an
    inherently multi-queue outcome -- a caller that wants a summary
    number can average `deltas` itself, exactly like `average_delta` does
    here, but the per-queue detail is not thrown away to produce it.
    """
    recommendation = shadow_run.get("recommendation")
    if not isinstance(recommendation, dict) or not recommendation.get("proposed_order"):
        return None
    made_at = datetime.fromisoformat(shadow_run["timestamp"])
    before_rates = shadow_run.get("inputs", {}).get("success_rates", {})
    after_rates: Dict[str, Any] = {}
    for node in before_rates:
        stats = _success_rate(ledger, node, made_at,
                              made_at + timedelta(seconds=outcome_window_s))
        after_rates[node] = stats["success_rate"] if stats else None

    deltas = {node: (after_rates[node] - before_rates[node])
             for node in before_rates if after_rates.get(node) is not None}
    if not deltas:
        return None  # no queue had enough real outcome data yet

    actual = {"success_rates_after": after_rates, "outcome_window_s": outcome_window_s}
    score = {"success_rates_before": before_rates, "deltas": deltas,
             "expected_impact": recommendation.get("expected_impact"),
             "average_delta": sum(deltas.values()) / len(deltas)}
    return ledger.record_recommendation_shadow_score(
        shadow_run_hash=shadow_run["shadow_run_hash"], actual=actual, score=score)


def main() -> None:
    """CLI: generate new shadow runs for a set of queues, or score
    whichever due ones (get_unscored_shadow_runs) have real outcome data
    now. Meant to run on a schedule (e.g. cron/systemd timer) -- same
    dry-run/JSON-summary shape as obligation_sweep.py's own CLI.
    """
    import argparse
    import json as _json
    import os as _os

    try:
        from claude_governance_api import ClaudeGovernanceDecider
    except ImportError as exc:  # IVR governor moved to the GSA-815 repo
        raise SystemExit(
            "recommendation_impact's `run` subcommand needs "
            "claude_governance_api.ClaudeGovernanceDecider, which now lives in "
            "the GSA-815 repo (the IVR island left the kernel). Run this CLI "
            "from a checkout that has it on PYTHONPATH. The `score` subcommand "
            "and every function in this module still work kernel-only."
        ) from exc
    from governance.ledger_postgres import PostgreSQLLedger

    ap = argparse.ArgumentParser(
        description="Recommendation shadow-run generator/scorer "
                    "(predictive-accuracy testing, never acts on anything)")
    sub = ap.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser(
        "run", help="Generate new shadow runs from real recent ledger data")
    p_run.add_argument("--queue", action="append", required=True, dest="queues",
                       help="Queue/node name to shadow-run (repeatable)")
    p_run.add_argument("--cassette-version", required=True)
    p_run.add_argument("--dry-run", action="store_true",
                       help="Compute and print recommendations but do not "
                            "record them on the ledger")

    p_score = sub.add_parser(
        "score", help="Score due shadow runs against real outcome data")
    p_score.add_argument("--min-age-s", type=float, default=3600.0,
                         help="Only score shadow runs at least this old "
                              "(default 1 hour) -- the caller's job to pick "
                              "a value that guarantees the outcome window "
                              "has actually elapsed")
    p_score.add_argument("--dry-run", action="store_true",
                         help="Compute and print scores but do not record "
                              "them on the ledger")

    ap.add_argument("--postgres-host", default=_os.getenv("POSTGRES_HOST", "localhost"))
    ap.add_argument("--postgres-port", type=int,
                    default=int(_os.getenv("POSTGRES_PORT", "5432")))
    ap.add_argument("--postgres-db", default=_os.getenv("POSTGRES_DB", "iceberg"))
    ap.add_argument("--postgres-user", default=_os.getenv("POSTGRES_USER", "iceberg"))
    ap.add_argument("--postgres-password",
                    default=_os.getenv("POSTGRES_PASSWORD", "iceberg"))
    args = ap.parse_args()

    ledger = PostgreSQLLedger(
        host=args.postgres_host, port=args.postgres_port,
        dbname=args.postgres_db, user=args.postgres_user,
        password=args.postgres_password)

    if args.command == "run":
        decider = ClaudeGovernanceDecider(api_key=_os.getenv("CLAUDE_API_KEY"))
        results = []
        for node in args.queues:
            if args.dry_run:
                continue
            r = run_healing_bounds_shadow(ledger, decider, node, args.cassette_version)
            if r is not None:
                results.append(r)
        if not args.dry_run:
            r = run_queue_reordering_shadow(ledger, decider, args.queues,
                                            args.cassette_version)
            if r is not None:
                results.append(r)
        summary = {"shadow_runs_recorded": len(results), "results": results,
                  "dry_run": args.dry_run}
        print(_json.dumps(summary, indent=2, default=str))
    else:
        older_than = (datetime.now(timezone.utc)
                      - timedelta(seconds=args.min_age_s)).isoformat()
        due = ledger.get_unscored_shadow_runs(older_than_iso=older_than)
        scored = []
        for shadow_run in due:
            if args.dry_run:
                continue
            if shadow_run["recommendation_kind"] == "healing_bounds":
                r = score_healing_bounds_run(ledger, shadow_run)
            elif shadow_run["recommendation_kind"] == "queue_reordering":
                r = score_queue_reordering_run(ledger, shadow_run)
            else:
                r = None
            if r is not None:
                scored.append(r)
        summary = {"due_shadow_runs": len(due), "scored": len(scored),
                  "skipped_insufficient_outcome_data": len(due) - len(scored),
                  "dry_run": args.dry_run, "results": scored}
        print(_json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
