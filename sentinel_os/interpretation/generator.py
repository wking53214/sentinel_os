"""
generator.py -- AI authorship of interpretation probes.

THE ONE THING THIS MODULE MUST NOT DO
--------------------------------------
It must not decide anything. Every scenario it produces lands as
PROPOSED with expected=None. The model may suggest which answer it
thinks is right; that suggestion is carried as commentary and is never
written into the scenario's expected field. Only a human approval call
in scenarios.py can do that.

This is the whole reason AI is allowed in here at all. Generation is a
creativity problem (invent the awkward case nobody thought of) and
creativity is where models are strong. Judgment is a liability
problem, and liability does not delegate.

WHAT THE MODEL IS ASKED FOR
----------------------------
Regulation text, the business's current reading, and the named
ambiguity zones go in. Out come concrete situations that sit ON the
boundary of those zones -- the cases where a strict reading and a
lenient reading diverge. A scenario that both readings answer the same
way proves nothing and is wasted approval effort.

CLIENT IS INJECTED
-------------------
The model client is a constructor argument, same posture as
sealed_channel and twin_client elsewhere in this codebase. Tests pass
a stub; production passes a real client. Nothing here reaches the
network on its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Protocol

from .scenarios import Scenario, ScenarioLibrary

MAX_SCENARIOS_PER_BATCH = 100


class ModelClient(Protocol):
    """Minimal interface a scenario-generating model must satisfy."""

    def complete(self, prompt: str) -> str:  # pragma: no cover - protocol
        ...


@dataclass
class InterpretationContext:
    """Everything the model needs to write useful probes.

    ambiguity_zones is the important field. It is the business's own
    admission of where the regulation is not clear. Naming those zones
    up front is what makes drift localizable later: results are grouped
    by zone, so a report can say WHICH part of the reading slipped.
    """

    regulation_id: str
    regulation_text: str
    chosen_interpretation: str
    ambiguity_zones: List[str]
    interpretation_version: str = "v1"
    prior_scenario_questions: Optional[List[str]] = None

    def prompt(self, count: int) -> str:
        prior = self.prior_scenario_questions or []
        prior_block = (
            "\nSCENARIOS ALREADY IN THE LIBRARY (do not repeat these):\n"
            + "\n".join(f"- {q}" for q in prior[:60])
            if prior else ""
        )
        zones = "\n".join(f"- {z}" for z in self.ambiguity_zones)
        return f"""You are generating test scenarios that probe how a governance system
applies one organization's chosen reading of a regulation. You are NOT
deciding what the correct answer is. A human reviewer decides that.

REGULATION ID: {self.regulation_id}
INTERPRETATION VERSION: {self.interpretation_version}

REGULATION TEXT:
{self.regulation_text}

THE ORGANIZATION'S CURRENT CHOSEN READING:
{self.chosen_interpretation}

NAMED AMBIGUITY ZONES (where this regulation is genuinely open):
{zones}
{prior_block}

Write {count} scenarios. Each must:
1. Sit on the boundary of exactly one named zone, where a strict reading
   and a lenient reading would plausibly diverge. Skip any case both
   readings answer identically.
2. Be concrete: real facts, real numbers, no placeholders.
3. Offer 2 to 4 mutually exclusive answer options.
4. Include your own suggested answer AS COMMENTARY ONLY, with your
   reasoning, so the human reviewer can see how you read it.

Respond with JSON only. No preamble, no markdown fences. Shape:
{{"scenarios": [
  {{"zone": "<one of the named zones>",
    "question": "<one sentence: what is being asked>",
    "situation": {{"<fact_name>": "<fact_value>"}},
    "options": ["A", "B"],
    "model_suggested_answer": "A",
    "model_reasoning": "<why the model leans that way>"}}
]}}"""


class ScenarioGenerator:
    """Turns a regulation plus its chosen reading into PROPOSED scenarios."""

    def __init__(self, client: ModelClient, generator_label: str = "ai-scenario-generator"):
        self._client = client
        self._label = generator_label
        # Rejections from the most recent generate() call, as
        # (question, reason). Surfaced rather than swallowed: a batch
        # where half the scenarios were dropped is telling you the zone
        # list or the prompt needs work.
        self.rejected: List[tuple] = []

    def generate(
        self,
        context: InterpretationContext,
        count: int = 50,
        library: Optional[ScenarioLibrary] = None,
    ) -> List[Scenario]:
        """Produce `count` PROPOSED scenarios and add them to `library`.

        Zone validation is strict: a scenario naming a zone that is not
        in the declared list is dropped with a reason rather than
        silently coerced into the nearest match. A model inventing its
        own zone is a signal that the declared zone list is incomplete,
        and that is a conversation for the humans, not a rounding error.
        """
        if count < 1 or count > MAX_SCENARIOS_PER_BATCH:
            raise ValueError(f"count must be 1..{MAX_SCENARIOS_PER_BATCH}, got {count}")

        self.rejected = []
        library = library if library is not None else ScenarioLibrary()
        prior = [s.question for s in library.all() if s.regulation_id == context.regulation_id]
        context.prior_scenario_questions = prior

        raw = self._client.complete(context.prompt(count))
        parsed = self._parse(raw)

        declared = set(context.ambiguity_zones)
        created: List[Scenario] = []
        for row in parsed:
            zone = str(row.get("zone", "")).strip()
            options = [str(o) for o in row.get("options", [])]
            question = str(row.get("question", "")).strip()
            if zone not in declared:
                self.rejected.append((question or "<no question>", f"undeclared zone {zone!r}"))
                continue
            if len(options) < 2 or len(set(options)) != len(options):
                self.rejected.append((question, f"needs 2+ distinct options, got {options}"))
                continue
            if not question:
                self.rejected.append(("<no question>", "missing question text"))
                continue

            scenario = Scenario(
                regulation_id=context.regulation_id,
                zone=zone,
                question=question,
                situation=dict(row.get("situation") or {}),
                options=options,
                generated_by=self._label,
            )
            # Model commentary rides along for the human reviewer and is
            # deliberately NOT the expected answer.
            scenario.situation.setdefault("_model_suggested_answer", row.get("model_suggested_answer"))
            scenario.situation.setdefault("_model_reasoning", row.get("model_reasoning"))
            library.add(scenario)
            created.append(scenario)

        return created

    @staticmethod
    def _parse(raw: str) -> List[Dict[str, Any]]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            payload = json.loads(text.strip())
        except json.JSONDecodeError as exc:
            raise ValueError(f"model did not return parseable JSON: {exc}") from exc
        scenarios = payload.get("scenarios")
        if not isinstance(scenarios, list):
            raise ValueError("model response has no 'scenarios' list")
        return scenarios


class StubModelClient:
    """Deterministic client for tests and dry runs. Returns whatever
    canned JSON it was handed, so a test can exercise the whole
    generate/approve/run path with no network and no nondeterminism."""

    def __init__(self, response: str):
        self._response = response
        self.prompts: List[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._response
