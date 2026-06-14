# ADR-0006: Decisions must challenge the frame, convene the right roles, and pull their own knowledge

**Status:** Proposed (2026-06-14) — drafted from a live operating-harness decision failure; awaiting founder acceptance
**Deciders:** Founder (solo)

## Context

A decision-quality failure observed in live operation. The C-suite ran a full debate on a go-to-market problem and unanimously, confidently endorsed a set of tactics — all of which lived *inside* the framing the directive had handed it. The option that actually mattered was a different *kind* of move entirely, and no agent proposed it. It surfaced only when the human founder reframed the question, and then won easily.

This is not a memory bug and not a prompt-tuning bug. It is a hole in how the engine makes decisions. Three failures stacked:

1. **No frame challenge.** The directive carried an unstated assumption, the agents optimized inside it, and none asked whether the framing itself was wrong. The engine debates *how*; it never asks *whether the question is the right question*.
2. **Wrong room.** The decision was go-to-market, but the convened roster didn't include the roles most likely to raise the winning option. Who is in the room is currently ad-hoc, not derived from the decision type.
3. **Knowledge not retrieved.** The winning idea was already present in the company's own knowledge base and was never pulled into the debate. The engine debates from the agents' priors, not from what the company already knows.

Net effect: the engine executes well *inside* a frame, but the human is still the only reframe engine. That is a ceiling on autonomy. Kompany's premise is that the human sets direction and the system runs — but if the system can only optimize the human's current framing and never challenges it, every strategic pivot bottlenecks on the human noticing.

## Decision

Add a mandatory **pre-debate stage** to the decision pipeline (directive → debate → decision). No consequential decision proceeds to the debate proper until three gates run:

### Gate 1 — Frame challenge (institutionalized devil's advocate)
Before debating *how*, a dedicated step answers in writing, and seeds the answers into the debate as real options:
- What is the unstated assumption in this directive?
- Is the stated objective the right objective, or a proxy for a bigger one?
- What option is nobody proposing? (the "empty chair")

Implement as a standing **red-team / devil's-advocate seat** in every debate (CoS in that role, or a dedicated contrarian agent). Its only job is to attack the frame and name the missing option, so the human doesn't have to be the one to do it.

### Gate 2 — Convene by decision type
The engine **classifies the decision** (go-to-market, pricing, hiring, infra, legal, fundraising, product-scope…) and **auto-selects the required agents** from a decision-type → roles map. Each decision type names its mandatory roles; who is in the room is derived from the decision, never picked ad-hoc by whoever opened the directive.

### Gate 3 — Retrieve company knowledge
Before the debate, the engine **queries its own memory / episodes / knowledge base** for entries relevant to the decision and injects them as context. Prior research, past decisions, and recorded lessons are on the table by default. The debate starts from what the company knows, not only from the agents' priors.

A decision's record must show all three ran: the frame-challenge output, the roster (and why those roles), and the knowledge pulled in.

## Consequences

- Removes the biggest autonomy ceiling found so far: the human being the only source of reframes.
- New engine surface: a decision-type classifier + a decision-type→roles map, a red-team seat in the debate loop, and a retrieval step wired into debate setup.
- Cost: every consequential decision gets slightly slower and more expensive (extra agent turns for framing + retrieval). Worth it — the failure mode it prevents (confidently optimizing the wrong frame) is the expensive kind.
- Open questions: what counts as "consequential" enough to trigger the full pre-debate stage vs a lightweight path; whether the red-team seat is a distinct agent or a mode; how to keep retrieval precise enough not to flood the debate with weak matches.
- Relationship to ADR-0005: 0005 makes the runtime reliably *run* decisions; 0006 makes the decisions themselves *well-framed*.

## Validation source

A live operating harness surfaced this: a strategic reframe the C-suite missed and the human had to supply. The specific business details are kept in the private operating records, not here.
