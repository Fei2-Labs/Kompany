# ADR-0010: Judgment provider seam — replaceable, opt-in, inert by default

**Status:** Accepted (2026-09-25)
**Deciders:** Founder (solo), via grill session
**Contract:** additive minor bump `1.2.0` → `1.3.0`
**Implementation:** `kompany/src/kompany/core/judgment.py`, `kompany/tests/test_judgment_seam.py`

## Context

Kompany decides things at three layers today:

1. **Pure-code hard gates** — `core/autonomy.py` (no auto-pay, ever) and
   `core/outward_policy.py` (`founder_identity` / spend / over-cap always gated).
   Hard floors can only restrict, never promote.
2. **Heuristic gates** — `core/deai_gate.py` (AI-tell scan), `core/fabrication_check.py`
   (hard claims must be grounded in evidence). Pure code, never crash.
3. **Economy-tier LLM judges** — the de-AI LLM layer, fabrication claim extraction,
   `core/debate.py`'s CEO decision. Free-text verdicts; a failure leaves the
   deterministic verdict standing.

A fourth kind of decision source exists in the market: services that return typed,
calibrated judgments with probability distributions instead of generated prose. They
are a real improvement over a free-text LLM judge for gate decisions — the confidence
number is the part a gate actually needs. All the ones worth using are **hosted** and
require a per-account API key.

Core is AGPL-3.0, public, and self-hosted by strangers on their own machines. Its
runtime dependency list contains no hosted judgment service. Adding one to a decision
path is a one-way door: a public repo cannot un-publish an architecture.

## Decision

Land the **seam**, not a provider.

### 1. Core defines a provider-agnostic `JudgmentProvider` ABC and names no vendor

`ChoiceQuestion` / `BooleanQuestion` / `ScoreQuestion` in, `Judgment` (value +
distribution + confidence + `abstained`) out. This vocabulary is the ordinary shape of
a calibrated judgment, not a transcription of any one service's API.

**Rejected: a required Core dependency on a specific judgment service.** Every
`git clone` would need somebody else's API key before the engine decided anything.
That violates the AGENTS.md tier test ("would withholding it make Core feel
crippled?") in the worst direction — it makes Core crippled *without* a purchase.

**Rejected: an optional pip extra pointing at a named vendor.** Still puts the vendor's
name in the public repo's decision path and still makes the good behaviour conditional
on a key. The seam gets the same benefit with no lock-in.

### 2. Core's shipped default abstains, and abstention means "use your own answer"

`AbstainingJudgmentProvider` answers every question with `abstained=True`. A caller
that sees an abstention MUST use the verdict it would have produced anyway.

This is not a placeholder. It is the correct behaviour for an install with no judgment
plugin — which is every install by default. **Judgment is an upgrade to a decision that
already has an answer, never the only thing between the engine and an action.**

### 3. Off-machine judgment is opt-in: `external_judgment_enabled`, default OFF

A provider declares `is_external`. An external provider found while the flag is off is
**skipped** — not an error; the founder installed it and has not switched it on.
`is_external` defaults to `True`, so a provider author who forgets to declare fails
closed.

A judgment call carries directives, outward copy and financial context. Shipping that
egress on by default in a self-hosted product is a trust problem, not a convenience.

**Rejected: on by default with a settings toggle to disable.** Consent that has to be
withdrawn is not consent.

### 4. Failure abstains; it never raises and never blocks

`judge()` catches everything: a provider that raises, returns a non-mapping, answers
the wrong kind, or omits a key produces abstentions for the affected questions.
Callers need no try/except. This mirrors how the existing LLM judge layers already
degrade to their deterministic verdicts.

**Rejected: fail closed (park the action for approval when judgment is unavailable).**
It would make an optional upgrade able to halt the company by going offline, and it
inverts the seam's premise that the deterministic answer is always sufficient.

### 5. Providers arrive as plugins, via entry-point group `kompany.judgment`

Core ships none. `discover()["judgment"]` is `[]` on a stock install.

### 6. Nothing in Core consumes the seam yet

No gate is wired. `test_no_core_decision_path_consumes_the_seam_yet` enforces this by
scanning for real imports of `kompany.core.judgment` outside the module itself and the
contract re-export.

Wiring a concrete provider into a decision path is a **separate decision requiring
founder sign-off**, and the ranked first candidate is approval routing (should this
action proceed unattended, or park for the founder?) — the place where a calibrated
confidence number is worth the most. That decision is deliberately not taken here.

## Consequences

- Core stays fully functional, dependency-clean and egress-free with no key. Unchanged
  behaviour for every existing install: the seam is inert.
- The public API surface is fixed *before* the OSS announcement, so a later provider
  is an additive plugin rather than a retrofit into published AGPL code.
- The contract gains one ABC (`1.3.0`, additive — every 1.2.0 plugin keeps working).
- A judgment provider is a legitimate paid-plugin shape: additive capability above
  Core, never a replacement for a Core artifact.
- Cost: one unused module until something is wired. Accepted — the alternative is
  retrofitting a decision seam into a public codebase after strangers depend on it.

## Cross-references

- Seam: `kompany/src/kompany/core/judgment.py`
- Contract re-export: `kompany/src/kompany/plugins/contract.py`, group in `plugins/loader.py`
- Consent flag: `external_judgment_enabled` in `kompany/src/kompany/config/settings.py`
- Tests: `kompany/tests/test_judgment_seam.py`
- Existing decision layers: `core/autonomy.py`, `core/outward_policy.py`,
  `core/deai_gate.py`, `core/fabrication_check.py`, `core/debate.py`
- Contract reference: [`docs/context/plugin-contract.md`](../context/plugin-contract.md)
