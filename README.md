# Sentinel OS

Problem

Governance is fractured. How can a governance system remain stable while the business systems, regulations, models, and industries it governs continuously change?

Thesis

Sentinel OS decouples governance from business systems so each can evolve independently while remaining verifiably connected.

## What Sentinel OS is

Sentinel OS exists to answer one question about an automated decision: why was it made and can that answer be proven? It sits beside a decision making system as a witness and judge. At the moment a decision happens, it records the inputs, the policy in force, the reasoning and the outcome, then binds them into tamper-evident evidence that an engineer, auditor or regulator can verify independently at any time.

The framework is domain-agnostic. Its founding scenario is an auditor asking a regulated company: "Can you tell me why the AI made this decision about xyz at that time?"{+£€%¥¥Sentinel OS is designed to be the system that answers that question, in any industry, without being rebuilt for each one.


## What Sentinel OS is NOT
Sentinel OS is not an
- AI governance checklist
- Static compliance document system
- Implementation of a single regulation
- Replacement for legal interpretation
- Commercial service platform.

## Research Mode Features

Some capabilities in this codebase are research-only and disabled by default pending IP/privacy attorney review before production deployment:

- **BISG demographic inference** (`bisg_estimator.py`, `obligation_sweep.py` geographic equity testing): Bayesian Improved Surname Geocoding is disabled unless `SENTINEL_BISG_RESEARCH_MODE=true` is explicitly set. When disabled, geographic cohort equity tests report skips rather than attempting demographic inference. The underlying regulatory checks remain available; only the BISG-specific inference is gated.

The default posture is witness ##NOT actor. Sentinel judges decisions and records evidence; it does not reach into the acting system. 

***The original reference implementation, a contact-center telephony deployment, does also operates in an acting mode where Sentinel DOES drive the decision. That is a distinct, optional operating mode, not the core of the architecture.

## Architectural Boundary

Sentinel OS is designed to provide a governance layer, not to replace the systems being governed.

Sentinel OS does not attempt to make AI models more capable, replace agent architectures, or become the application that performs business operations. It does not assume that one model, one framework, or one implementation pattern will define the future of automated systems.

Instead, Sentinel OS focuses on the layer that remains necessary regardless of how those systems change:

**How do we know what happened, why it happened, what governance applied, and whether the result can be independently verified?**

Sentinel OS is not:

- an AI model
- an agent framework
- a prompt management system
- a model guardrail library
- a policy checklist
- a compliance document repository
- a replacement for legal interpretation
- a business workflow engine

Those systems may change over time. New models will emerge. New agent architectures will replace current ones. New regulations will appear. New industries will develop their own requirements.

Sentinel OS is designed around the assumption that change is inevitable.

The purpose of the architecture is not to prevent change. The purpose is to create a stable governance foundation that remains connected to changing systems without becoming tightly coupled to them.

The separation is intentional:

- Business systems decide and operate.
- AI systems generate and assist.
- Governance systems evaluate and provide evidence.
- Human authorities interpret findings and make final determinations where required.

This boundary allows Sentinel OS to evolve differently from the systems it governs.

The systems underneath may change rapidly.

The governance foundation remains stable.

The connection between them remains verifiable.

## The Governance Problem

Governance is fractured. Requirements come from many regulators, differ by industry and jurisdiction, and change continuously. A lending rule is amended. A state adds a reporting obligation an adjacent state does not have. An industry that had no AI oversight last year gains it this year.

An organization cannot rebuild its governance architecture every time this happens. If governance logic is welded into the decision system, every regulatory change becomes an engineering project against production code, and every industry expansion becomes a rearchitecture.

The central architectural question is therefore: how can a governance system remain stable while continuously adapting to change?

The answer that defines Sentinel OS: the kernel remains stable, governance knowledge evolves, and the architecture adapts. The kernel does not remain stable because nothing changes. It remains stable because change is isolated into controlled, replaceable layers, and the boundary between what changes and what does not is enforced by the architecture rather than by discipline.

## Why Existing Approaches Struggle

Checklist and document systems freeze understanding at the time of writing. They describe intended controls but cannot prove what actually happened at decision time.

Conventional logging records actions but not proof. Logs can be edited, can be incomplete, and are typically the system's own report about itself. A system asserting its own good behavior is the very thing under examination.

Per-regulation builds do not compose. A system built to satisfy one regulation must be substantially rebuilt for the next one, and the parts worth keeping are entangled with the parts that must change.

Sentinel OS responds to all three failures with the same move: separate what must be permanent, proven, and deterministic from what must be replaceable, and make evidence a structural property rather than a reporting feature.

## Architectural Philosophy

Sentinel OS takes a deliberate design influence from COBOL-era mission-critical systems. This is an influence, not an identity claim. Those systems survived for decades because they prioritized durability, explicit rules, determinism, maintainability, and controlled evolution. Sentinel OS attempts to preserve those qualities while avoiding what eventually made such systems burdensome: accumulated complexity, opaque behavior, fragile dependencies, and reliance on institutional memory.

The design tension is: how can a system achieve decades-long reliability while remaining simple enough to understand?

The same influence shapes the governance checks themselves. The core checks are deliberately basic: membership in a declared list, comparison of a recomputed hash against a stored one, arithmetic over recorded facts. A check of this kind does not weaken as the models it governs grow more capable, because it never asks the model anything. It asks whether a past fact matches a declared rule, and no amount of model capability changes a past fact.

## Core Architecture

**Stable kernel.** The kernel defines the permanent primitives: the episode record of a decision, the rules for judging an episode, and the manifest by which a governance module declares what it can and cannot do. The kernel carries no industry vocabulary and no regulation text.

**Governance cassettes.** Cassettes are modular governance knowledge packages. All domain and regulatory knowledge lives in them, so governance requirements can evolve without redesigning the foundation. A cassette is identified by a content hash of its own code and configuration; a changed cassette hashes differently and must be a new version. Silent modification is refused rather than detected after the fact.

**Industry cassettes** carry domain-specific governance knowledge: what the decisions in that domain are, what outcomes mean, and over what horizon an outcome matures. Adding an industry means writing a cassette, not changing the foundation. The contact-center cassette is the reference implementation; it is a worked example, not a template.

**Regulatory cassettes** carry regulatory knowledge as a lens over decisions. They live in their own registry, separate from industry cassettes, so a lens can never load as operational policy. The default mode is observation: read-only review of the evidence record. Live evaluation is opt-in. Inserting or removing a lens is itself a first-class recorded event, and a live lens must disclose its findings to the evidence chain before any effect takes place. Lenses never alter the domain judgment.

**Evidence ledger.** Every governance-relevant event is written to an append-only, hash-chained ledger. Each entry binds the decision to the exact cassette version, model identity, and policy parameters in force when it was made.

**Twin architecture.** Evidence is mirrored to an independent replica held under separate custody, where the party that produced the evidence does not hold the keys to its sealed contents. The twin verifies by recomputation and comparison, so a rewritten chain, a deleted record, or a forged snapshot on the primary becomes a detectable divergence rather than a silent loss. The twin also derives outcome obligations independently from the decision feed, so an operator cannot suppress an obligation without suppressing the decision itself.

**Outcome verification.** Recording an action is not the same as verifying its result. A decision record closes permanently at decision time, but many decisions carry a durable obligation to learn how they turned out. Obligations are tracked separately, mature on a declared schedule, and resolve to explicit states. Cohort-level outcome review, comparing results across groups rather than judging single outcomes, is treated as governance; per-decision outcome tracking is business reporting and stays out of the governance chain, with one exception: an outcome showing the decision process itself failed is a governance event.

## Stable Kernel and Adaptive Layers

Kernel stability matters because everything downstream depends on the kernel's primitives meaning the same thing over time. Evidence written years apart must verify under the same rules. Every site that reconstructs a hash must agree exactly with every other. A verifier must be able to hold the whole verification model in their head. For these reasons, core primitives resist change, and predictable behavior is treated as a feature in itself rather than a limitation.

The cassette layer is where change is expected and welcomed. New regulation, new industry, new institutional policy: each arrives as a new or revised cassette, versioned by content hash, bound into the ledger at load time. The kernel's stability and the cassette layer's mobility are the same design decision viewed from two sides.

## Evidence Model

Evidence is not logging. Evidence is demonstrable proof of what was decided, which controls applied, what reasoning was recorded, how it was validated, and what the outcome was. The distinction is enforced structurally.

An actor's report about its own behavior is recorded but never trusted as proof. It is cross-checked against independently observed facts, and it never enters judgment as an input. Verification is recomputation, not trust: any verifier can rebuild each hash from the recorded fields and compare against the chain, and the twin performs this comparison from a separately held copy.

Every observation carries one of three stamps: verified, attested, or estimated. An estimate that will not name its method fails validation. The rule governing the whole model:

> Every claim is stamped verified, attested, or estimated, and they are not interchangeable. If it's unknown, Sentinel will timestamp why and what would close it.

The same discipline applies to what is not yet known. An unresolved outcome is never a flat "indeterminate" flag; it carries a typed reason, when it was opened, and the declared horizon by which it should resolve. Ambiguous outcomes are classified as ambiguous, not coerced into a verdict.

## Extension Philosophy

Extension happens at the edges, never in the core. A new industry is a new cassette. A new regulation is a new lens. The kernel does not change to accommodate either.

Capabilities are declared, not assumed. A cassette states which capabilities it implements, and the architecture refuses a cassette at the door rather than letting it run with placeholder values for a capability it does not honestly have. Declaring the absence of a capability is treated as correct behavior, not as failure.

Regulatory knowledge evolves on its own track. Only the regulating agency, or someone it explicitly designates, can author a lens with official standing. Lenses authored within this project, including the shipped consumer-lending reference lens, are permanently reference examples regardless of their accuracy. The intended future model, in which an agency publishes one official cassette that its auditors carry into any organization running the framework, is stated here as direction. It is not a current capability.

## Design Principles

Stability over reinvention: the foundation earns trust by not changing.

Evidence over assertion: a claim without a verifiable record is not a governance fact.

Determinism over ambiguity: the same inputs produce the same judgment, checks fail closed, and the system refuses to guess rather than producing a plausible answer.

Clarity over cleverness: a verifier who cannot understand the mechanism cannot trust its output.

Architecture over implementation tricks: guarantees come from structure, such as separate custody and content-hash binding, not from code paths that promise to behave.

Maintainability over short-term optimization: the system is built to be operated by people who did not build it.

Modular evolution over replacement: the response to change is a new module, not a new system.

## Truth Boundaries

Sentinel OS screens; it does not adjudicate. Its findings flag decisions for human review against specific, recorded criteria. A finding is never a compliance determination, and every report says so.

Sentinel OS does not interpret law. It structures evidence and applies declared checks. What a regulation requires remains a question for the regulator, counsel, and the courts.

Limitations are disclosed and remain disclosed. Pattern-based screens can be evaded by renaming a variable or rephrasing a narrative; mitigations exist for known evasions and are documented as mitigations that raise the bar, not as closures. A gap is acceptable only when it resolves to a flag or an explicit unknown, never to a silent pass.

Provenance stamps are not interchangeable, in the framework's own claims as much as in its data. Nothing in this document describes a company, a product line, a partnership, or a managed service as a current capability.

## Long-Term Architectural Intent

This document is written to remain valid for a decade. That is possible only because of what it deliberately excludes: no regulation text, no industry vocabulary, and no claim that depends on a particular module surviving. Those belong to the adaptive layers, where change is the design.

The long-term expectation is that oversight of automated decisions will broaden and harden as the systems being governed grow more capable, including in industries whose oversight is minimal today. The architecture anticipates this by keeping every industry-specific and regulation-specific assumption out of the kernel, and by grounding its guarantees in checks that do not degrade as models improve.

The intended end state is a shared governance foundation in which every industry has its own cassette and every regulator's requirements can be expressed as one, added without touching the foundation. That end state is direction. What exists today is the framework itself: a stable kernel, working cassettes, a tamper-evident evidence chain, an independent witness, and a discipline for saying exactly what is known, what is claimed, and what is not.