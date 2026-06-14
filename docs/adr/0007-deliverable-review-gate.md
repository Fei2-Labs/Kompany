# ADR-0007: Outward-facing deliverables pass a C-suite review gate before they ship

**Status:** Proposed (2026-06-14) — drafted from a live operating-harness near-miss; awaiting founder acceptance
**Deciders:** Founder (solo)

## Context

A worker (the ops loop) drafted an outward-facing deliverable — a product and its sales copy — and was about to ship it. Nothing in the pipeline required anyone else to look at it first. The human caught this and asked: shouldn't a deliverable like this be reviewed by the relevant executives before it goes out?

It should. When that review was then convened, it was not a formality — it returned eight substantive fixes and held the deliverable from shipping: a missing worked example, asserted-not-justified claims, a buried core artifact, a too-soft call to action, and an entirely unwritten nurture sequence that would have broken the conversion funnel's close. A solo ship would have put all of that in front of customers.

This is a governance hole distinct from how decisions get made (ADR-0006). ADR-0006 governs *deciding what to do*. This governs *what gets produced before it represents the company outward*. A capable agent will produce something plausible and ship it; plausible is not the same as reviewed, and the author is usually blind to its own gaps.

## Decision

Any **substantive outward-facing deliverable** passes a **C-suite review gate** before it ships or goes to the human for final approval. Not the producing agent alone.

**What counts as substantive (gated):** a product (free or paid), a landing/sales page, a publication or press submission, a marketing campaign or sequence, a public announcement — anything that represents the company to outsiders and is costly or slow to walk back.

**What's exempt (ungated):** a single conversational reply, a routine internal log entry, low-stakes incremental edits. The gate is for things that carry the company's name outward, not every keystroke.

**How the gate works:**
1. The producing agent finishes a draft and routes it to review (does not ship).
2. The engine convenes the roles that own the deliverable's dimensions (per ADR-0006 convene-by-type): content/brand → CMO, conversion/revenue → CRO, legal exposure → CISO/legal, etc.
3. Reviewers must surface concrete defects — what stops the outcome, what weakens it, what's missing — and may HOLD the deliverable. The review is adversarial by design, not a sign-off ritual.
4. The producing agent incorporates the fixes; material changes go back for a re-review of those points.
5. Only then does the deliverable ship (within existing autonomy) or go to the human for the final outward GO where an identity/irreversibility gate applies.

The deliverable's record shows the review ran, who reviewed, what they flagged, and how it was resolved.

## Consequences

- Closes the "author ships its own unreviewed work outward" hole. Plausible-but-flawed deliverables get caught inside the company instead of in front of customers.
- New engine surface: a deliverable-type classifier, a review-convening step reusing ADR-0006's role map, and a HOLD/re-review loop in the produce→ship pipeline.
- Cost: outward deliverables ship slower and cost extra agent turns. Right trade — the work that represents the company is exactly the work that shouldn't go out unchecked.
- Composes with the rest: ADR-0006 frames the decision and convenes roles; ADR-0007 reviews the resulting deliverable; ADR-0005 makes the runtime reliably execute both. Together: decide well, produce, review, ship.
- Open questions: the substantive/exempt threshold (avoid gating trivia); how many review roles before it's bureaucratic; whether HOLD can be overridden by the human and how that's recorded.

## Validation source

A live operating harness: a deliverable nearly shipped solo; the human flagged the missing gate; the convened C-suite review then held it with eight concrete fixes. Specific business details are kept in the private operating records, not here.
