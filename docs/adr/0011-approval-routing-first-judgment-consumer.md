# ADR-0011: Approval routing is the judgment seam's first consumer

**Status:** Accepted (2026-09-25)
**Deciders:** Founder (solo)
**Contract:** no change — this wires an existing Core decision path to the existing
`JudgmentProvider` seam ([ADR-0010](0010-judgment-provider-seam.md)); it adds no
plugin surface.
**Implementation:** `kompany/src/kompany/core/approval_judgment.py`,
`kompany/src/kompany/core/engine_parts/surfaces.py::_auto_approve_eligible`

## Context

ADR-0010 landed the `JudgmentProvider` seam inert — no Core gate consumed it, by
design, until a founder-signed decision picked where. That decision had two open
questions, both escalated during the grill that produced ADR-0010 because the
advisory judgment call's own confidence was too low to settle them on its own:

1. **Which decision point first?** Candidate spread: approval routing 0.48 /
   outward pre-flight 0.29 / debate CEO 0.16, confidence 0.38.
2. **Augment or leave the existing LLM judges alone?** augment 0.55 / only-new-points
   0.44 / replace 0.01, confidence 0.32. The one firm signal in the whole spread:
   *don't replace* (0.01).

**Decision: approval routing, augmenting, not replacing.**

Approval routing — `core/engine_parts/surfaces.py::_auto_approve_eligible`, the
founder-tunable auto-approve check consulted from `propose_action` — scored highest
among the candidates and is the one ADR-0010 itself named as the ranked first
candidate. It decides one narrow thing: given a `tool_authorization` policy that
already allows a role to run a specific tool unattended, should this particular
call still surface for a human look. That is exactly the shape a calibrated
confidence number improves — today the decision is a single deterministic boolean
(policy present, `allowed=True`, `requires_approval=False`) with no room for "this
looks routine but something about it doesn't sit right."

## Decision

### 1. Judgment can only SUBTRACT eligibility, never grant it

`judgment_confirms_auto_approve` is consulted only after `_auto_approve_eligible`
has already found a policy that allows the call. It can turn that into a hold; it
can never make an unauthorized tool/role pair eligible. This mirrors every other
layer already touching this decision: `AutonomyGate.check_tool`'s no-auto-pay floor
and `outward_policy`'s hard floors both only restrict, never promote.

**Rejected: let judgment grant eligibility a policy doesn't cover.** That would
make an optional, best-effort upgrade able to unlock unattended execution the
founder never configured — the exact one-way door ADR-0010 flagged as needing
sign-off, and a strictly worse invariant than what exists today.

### 2. Augments the existing check; does not replace `tool_authorization`

The deterministic policy lookup is unchanged and still runs first. Judgment adds a
second opinion, consistent with the *augment* verdict (0.55) and the *don't
replace* signal (0.01, the grill's only firm answer, recorded in
`.agents/memory/project-judgment-seam-decision.md`).

### 3. Conservative double threshold: `probability ≥ 0.65` AND `confidence ≥ 0.6`

Interrupting the founder is a real cost, not a neutral default — a `BooleanQuestion`
sitting near 0.5 means "about as likely as not," not "somewhat concerning," and a
low-confidence answer on either side should not fire a hold. Both bars must clear
before a policy that already allowed the call gets overridden. Values are a
starting point recorded here for revisiting once a real provider's calibration is
observed in practice; they are not derived from data (none exists pre-provider).

**Rejected: a single confidence-only threshold.** A confident "no, don't hold" is
easy to conflate with a confident "yes, hold" if only one number gates. Splitting
`value` (which way) and `confidence` (how sure) is the same distinction ADR-0010's
`Judgment.usable` already draws.

### 4. Still a no-op on every install today

No provider ships with Core (`discover()["judgment"] == []`), so
`resolve_judgment_provider` returns `AbstainingJudgmentProvider`, `judge()` returns
an abstention, `usable` is `False`, and `judgment_confirms_auto_approve` returns
`True` unconditionally — the policy's own answer stands, byte-for-byte the same
behaviour as before this ADR. This is the same "Core is complete without a
provider" property ADR-0010 established, now demonstrated at a real call site
rather than only in the seam's own tests.

### 5. Isolated into its own module, not inlined into `surfaces.py`

`surfaces.py` is already at the ADR-0003 boundary (577 lines pre-change). The
consultation logic — question construction, state shape, thresholds — lives in
`core/approval_judgment.py`; `surfaces.py` gains one import and one call.

## Consequences

- The judgment seam has exactly one consumer, and it can only make the system more
  conservative, never less. The guard test from ADR-0010
  (`test_no_core_decision_path_consumes_the_seam_yet`, renamed
  `test_only_the_signed_off_consumer_imports_the_seam`) now allow-lists
  `approval_judgment.py` by name — a second consumer added without updating that
  test still fails CI, forcing the same founder sign-off this one got.
- Zero behavior change for any install without a judgment plugin — which remains
  every install today.
- The two escalated questions from ADR-0010 are now answered and recorded here;
  `.trellis/BACKLOG.md`'s "landed since this ranking" note should be updated to
  point at this ADR instead of carrying them as open.

## Cross-references

- Seam: `kompany/src/kompany/core/judgment.py` ([ADR-0010](0010-judgment-provider-seam.md))
- Consultation: `kompany/src/kompany/core/approval_judgment.py`
- Call site: `kompany/src/kompany/core/engine_parts/surfaces.py::_auto_approve_eligible`
- Tests: `kompany/tests/test_approval_judgment.py`, `kompany/tests/test_judgment_seam.py`
- Existing decision layers left untouched: `core/autonomy.py`, `core/outward_policy.py`
