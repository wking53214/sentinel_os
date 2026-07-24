# observe/

Synthetic call-log generation and friction-signal derivation for testing
and the standalone simulator. Generates fake call traces that look like
real Twilio webhook events, and converts raw call-event traces into
per-hop friction signals.

## Files

- **`IngestAdapter.py`**
  - `generate_synthetic_call(call_id, journey, rng, friction_profile)` --
    builds a raw event trace (`call_start` / `menu_reached` / `hold_start`
    / `hold_end` / `call_end`) for one call walking a given node
    `journey`. `friction_profile` injects a specific failure shape:
    `"clean"` (no friction), `"hangup"` (truncates the journey early),
    `"overrun"` (one node's dwell time is 4x the expected value),
    `"revisit"` (backtracks to a previous node mid-journey, i.e. the
    caller got bounced back). Hold events are injected probabilistically
    (30% chance per node) regardless of profile.
  - `derive_stimuli(events, resolution_nodes, handoff_nodes, ...)` --
    the reverse direction: takes a raw event trace and derives, per
    menu node visited, whether that hop showed friction (`revisit` or
    `overrun` past `dwell_anomaly_seconds`), actual vs. expected wait,
    and whether the node counts as resolved. Returns a `DerivedCall`
    (frozen dataclass: `call_id`, `route`, `stimuli_by_hop`,
    `final_outcome_hint` -- `"success"` if the call's last node is in
    `resolution_nodes`/`handoff_nodes`, else `"abandonment"`).
    `hold_between()` correctly handles hold segments that only
    partially overlap a queried time window (clips to the window, not
    just an all-or-nothing overlap check).

- **`TwilioSyntheticLogGenerator.py`** -- wraps `IngestAdapter.generate_synthetic_call`
  to produce output shaped like real Twilio webhook events (`CallSid`,
  `AccountSid`, status-callback event types like `initiated`/`queued`/
  `dequeued`/`completed`) rather than the internal event format.
  `generate_twilio_population(n_calls, seed, journeys)` generates a
  full day's synthetic call population (default journeys: `billing`,
  `claims`, `pharmacy`, each a fixed node path) spread across a
  configurable business-hours window (`BUSINESS_OPEN_S`/`BUSINESS_CLOSE_S`,
  default 08:00-20:00), and returns it as JSONL sorted by
  `(timestamp, CallSid, SequenceNumber)`. Every ID (`CallSid`,
  `AccountSid`, phone number) is deterministically derived from the
  seed via SHA-256, not randomly generated -- same seed always
  reproduces the identical population, byte for byte.
  `load_twilio_jsonl(text)` is the matching reader: parses the JSONL
  back into a `{CallSid: [events]}` dict.

## Relationship to the two files

`TwilioSyntheticLogGenerator.py` imports and wraps `IngestAdapter.generate_synthetic_call`
directly (`from IngestAdapter import generate_synthetic_call`) -- it's a
presentation layer on top of `IngestAdapter`'s internal event format, not
an independent generator. `derive_stimuli` is the only consumer-facing
function that goes the other direction (events -> friction signals); it
is not currently wired to `TwilioSyntheticLogGenerator`'s output format
in this directory -- a caller wanting friction stimuli from realistic
Twilio-shaped JSONL would need to convert via `load_twilio_jsonl` first
and reconstruct `IngestAdapter`'s internal event shape, since the two
formats differ (Twilio event types vs. the internal `call_start`/
`menu_reached`/etc. vocabulary `derive_stimuli` expects).
