#!/usr/bin/env python3
"""Real-key proof that the Claude governor circuit breaker RECOVERS.

Run this yourself, on a machine that has your Anthropic key. The key is read
from the environment variable CLAUDE_API_KEY (or ANTHROPIC_API_KEY) and is
never printed, logged or written to any file by this script.

What it proves, with real calls to the real API (a handful of tiny requests,
a few cents at most):
  1. With a working key and a normal time limit, the governor answers
     (breaker stays CLOSED).
  2. Real failures open the breaker: five real calls are made to the real
     API with a deliberately invalid key, which the API genuinely rejects.
     The breaker goes OPEN.
  3. While OPEN, calls are refused instantly with no network traffic.
  4. After the reset wait (30s), the working decider is put back. The breaker
     goes HALF_OPEN, lets probe calls through, and after 2 real successes
     returns to CLOSED.

No database is needed. Exit code 0 = every step proved, 1 = something failed.

Usage (from the sentinel_os folder):
    read -rs -p "Anthropic key: " CLAUDE_API_KEY; export CLAUDE_API_KEY; echo
    python3 scripts/prove_claude_breaker_recovery.py
    unset CLAUDE_API_KEY
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cassettes.mortgage_cassette import MortgageCassette  # noqa: E402
from circuit_breaker import CircuitState  # noqa: E402
from episode import make_episode  # noqa: E402
from governance_decider import GovernanceDecider  # noqa: E402
from governance_harness import GovernanceHarness  # noqa: E402

KEY = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
results = []


def check(label, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def episode(n):
    return make_episode(f"PROVE-{int(time.time())}-{n}", "mortgage",
                        requested={"granted": True, "amount": 500.0},
                        actual={"granted": True, "amount": 350.0},
                        outcome_reasons=("amount capped by program ceiling",))


def main():
    if not KEY:
        print("No key found. Set CLAUDE_API_KEY first (see usage at the top of this file).")
        return 1
    os.environ["GOVERNOR_TIMEOUT_S"] = "20"
    h = GovernanceHarness({"postgres_host": None, "claude_api_key": None}, MortgageCassette(),
                          require_cassette_binding=False)
    good = GovernanceDecider(api_key=KEY)
    h.decider = good

    # 1. healthy
    r = h.process(episode(0), issue_count=1)
    reasoning_ok = r.get("governed") is True and isinstance(r.get("governance_approved"), bool)
    check("1. real key answers the governor call", reasoning_ok and
          h.claude_breaker.snapshot()["state"] == CircuitState.CLOSED.value,
          f"approved={r.get('governance_approved')}")
    if not reasoning_ok:
        print("   The key did not get a normal answer; stopping so nothing else is misread.")
        return 1

    # 2. real failures open it (real API, deliberately invalid key -> real rejection)
    h.decider = GovernanceDecider(api_key="sk-ant-this-key-is-invalid-on-purpose")
    for i in range(5):
        h.process(episode(i + 1), issue_count=1)
    s = h.claude_breaker.snapshot()
    check("2. five real failed calls open the breaker", s["state"] == CircuitState.OPEN.value,
          f"state={s['state']} failures={s['total_failures']}")

    # 3. open = instant refusal
    t0 = time.monotonic()
    r = h.process(episode(9), issue_count=1)
    dt = time.monotonic() - t0
    check("3. open breaker refuses instantly, still fails closed",
          dt < 0.5 and r["governance_approved"] is False, f"{dt*1000:.0f} ms")

    # 4. recovery
    h.decider = good
    print("   waiting 31s for the reset timer ...")
    time.sleep(31)
    seen = []
    for i in range(3):
        r = h.process(episode(20 + i), issue_count=1)
        seen.append(h.claude_breaker.snapshot()["state"])
        if seen[-1] == CircuitState.CLOSED.value:
            break
    check("4. after the wait, real successful probes close the breaker again",
          seen and seen[-1] == CircuitState.CLOSED.value, f"states seen: {seen}")
    r = h.process(episode(30), issue_count=1)
    check("5. normal service resumes (real approval/denial returned)",
          isinstance(r.get("governance_approved"), bool) and
          h.claude_breaker.snapshot()["state"] == CircuitState.CLOSED.value)
    print("\nAll steps proved." if all(results) else "\nSOMETHING FAILED: see the FAIL lines above.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
