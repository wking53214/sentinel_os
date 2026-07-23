# Domain Cassettes

Same boom box, different tapes. The engine (harnesses, ledger,
governor) is domain-blind; everything an industry actually believes —
scoring rules, tier cutoffs, queue topology, reward signals, governance
thresholds — lives in that industry's cassette. Swap the cassette,
same machine governs a different world.

## The contract (as of the 2.0.0 kernel/capability split)

Every cassette implements the **kernel**
(`cassette_interface.Cassette`): identity (`get_config`), a typed
governance-parameter declaration (`get_governance_parameters`,
validated by `cassette_schema` — fail-loud, no defaults, nothing
repaired), judgment (`judge(episode)` → score **and** tier; the
cassette owns its own cutoffs), explanation (`explain(episode)` →
factor-level reasons), a self-check (`validate`), and a **CAPABILITIES
manifest** declaring which opt-in surfaces the domain genuinely has:

- `telephony_ingest` — ingests phone calls; owns the Twilio thresholds
  and the call-shaped scoring surface.
- `routing_topology` — routes work through named queues.
- `rl` — trains against a reward signal.
- `self_healing` — allows governed self-adjustment inside declared
  bounds.

The manifest decides the parameter contract in both directions:
required = kernel ∪ enabled capabilities, and declaring a parameter
owned by a capability the cassette did *not* enable is refused (the
anti-placeholder rule — born from a real incident where banking carried
three flagged fake Twilio thresholds just to load). An empty manifest
is a legitimate, explicit declaration: a kernel-only domain loads,
validates, and judges episodes with zero call-center surface.

Judgment reads `episode.actual` (the observed record) and
`episode.attributes` — never `episode.actor_report`, which is the
acting system's unverified story about itself. All judgment paths go
through `episode.judge_episode`, so no path admits an unvalidated
episode, and the kernel refuses any episode whose outcome differs from
what was requested without a reason on file (any mismatch — including
approved-but-reduced — not just formal denials).

## The cassettes in this directory

- `ivr_cassette.py` — the **reference implementation**: the domain
  that happens to enable all four capabilities. A reference, not a
  template other domains contort into.
- `banking_cassette.py` — first cassette to use the split honestly:
  routing + rl + self_healing, **no** telephony (no real banking call
  data exists yet, so the capability is off rather than faked).
  Judges the same episodes as IVR by different rules — that
  difference is the point of the system, not a bug. One documented
  open decision: a correct fraud escalation still scores unresolved in
  judgment even though the reward signal treats it as a win; needs an
  explicit call before an escalation carve-out is added.

## Loading and tamper evidence

Every load path — loader, registry, harness construction, direct
injection — runs `cassette_schema.validate_cassette`; there is no
partial load. At harness load the cassette's parameter snapshot and its
**code hash** (its own module plus the shared governance modules in
`cassette_forensics._GOVERNANCE_CODE_MODULES`) are bound into the
ledger chain (`bind_cassette_version`). A version string is a content
commitment: changed parameters *or* changed governance code require a
new version string, or the load is refused. That is why cassette
versions bump when shared governance code changes even though the
cassette's own behavior didn't (see the 2.0.1 note in each cassette).

`CassetteLoader` auto-discovers `*_cassette.py` in this directory,
fail-loud by default. **Regulatory lenses do not live here** — they are
review lenses, not operational policy, and have their own directory,
registry, and contract: see `../regulatory_cassettes/README.md`.
