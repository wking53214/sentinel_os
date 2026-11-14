#!/usr/bin/env python3
"""Prove the circuit breaker trips and RECOVERS against a real remote API,
using a provider you already have a key for (Groq or Gemini).

HONEST SCOPE: this proves circuit_breaker.CircuitBreaker (same class and the
same thresholds as the claude_governor breaker: 5 failures, 30s reset, 2
successes to close) against real network failures and real recovery. It does
NOT exercise the Claude governor path; that needs an Anthropic key and
scripts/prove_claude_breaker_recovery.py. Keep Claude recovery listed as
unproven until that one has run.

The calls are free read-only "list models" requests, so there is no model
cost. The key is read from the environment (GROQ_API_KEY or GEMINI_API_KEY),
is never printed or written anywhere, and the failure steps use a deliberately
invalid key so the provider genuinely rejects them.

Usage (from the sentinel_os folder):
    read -rs -p "Groq key: " GROQ_API_KEY; export GROQ_API_KEY; echo
    python3 scripts/prove_breaker_recovery_remote_api.py
    unset GROQ_API_KEY
(or GEMINI_API_KEY the same way). Exit 0 = every step proved.
"""
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState  # noqa: E402

PROVIDERS = {
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1/models",
             lambda k: {"Authorization": f"Bearer {k}"}),
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/models",
               lambda k: {"x-goog-api-key": k}),
}
results = []


def check(label, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def main():
    name = next((n for n, (env, _, _) in PROVIDERS.items() if os.environ.get(env)), None)
    if name is None:
        print("No key found. Set GROQ_API_KEY or GEMINI_API_KEY first (see the top of this file).")
        return 1
    env, url, headers = PROVIDERS[name]
    url = os.environ.get("BREAKER_PROOF_BASE_URL", url)
    good = os.environ[env]
    bad = "invalid-on-purpose-" + "x" * 20

    def remote_call(key):
        r = httpx.get(url, headers=headers(key), timeout=15.0)
        if r.status_code != 200:
            raise RuntimeError(f"remote API said {r.status_code}")
        return r.status_code

    br = CircuitBreaker(name="remote_proof", failure_threshold=5, reset_timeout_s=30, half_open_success_threshold=2)
    print(f"provider: {name}")

    try:
        ok = br.call(remote_call, good) == 200
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"   first call failed: {type(exc).__name__}")
    check("1. a real call succeeds and the breaker stays CLOSED",
          ok and br.snapshot()["state"] == CircuitState.CLOSED.value)
    if not ok:
        print("   The key did not work; stopping so nothing else is misread.")
        return 1

    rejected = 0
    for _ in range(5):
        try:
            br.call(remote_call, bad)
        except RuntimeError:
            rejected += 1
    s = br.snapshot()
    check("2. five real rejections from the provider open the breaker",
          rejected == 5 and s["state"] == CircuitState.OPEN.value, f"state={s['state']} failures={s['total_failures']}")

    t0 = time.monotonic()
    try:
        br.call(remote_call, good)
        refused = False
    except CircuitOpenError:
        refused = True
    dt = time.monotonic() - t0
    check("3. while OPEN, even a good call is refused instantly with no network traffic",
          refused and dt < 0.5, f"{dt * 1000:.0f} ms")

    print("   waiting 31s for the reset timer ...")
    time.sleep(31)
    seen = []
    for _ in range(3):
        try:
            br.call(remote_call, good)
        except Exception as exc:  # noqa: BLE001
            print(f"   probe failed: {type(exc).__name__}")
        seen.append(br.snapshot()["state"])
        if seen[-1] == CircuitState.CLOSED.value:
            break
    check("4. after the wait, real successful probes (HALF_OPEN) close the breaker",
          bool(seen) and seen[-1] == CircuitState.CLOSED.value and CircuitState.HALF_OPEN.value in seen,
          f"states seen: {seen}")

    check("5. normal service resumes", br.call(remote_call, good) == 200
          and br.snapshot()["state"] == CircuitState.CLOSED.value)
    print("\nAll steps proved (breaker only; Claude governor path still unproven)."
          if all(results) else "\nSOMETHING FAILED: see the FAIL lines above.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
