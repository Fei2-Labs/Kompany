"""Debate engine — orchestrates multi-agent debates for strategic decisions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kompany.core.debate_models import (
    AgentPosition,
    CEODecision,
    Claim,
    DebateResult,
    DebateRound,
    DebateSynthesis,
    Source,
    SourceType,
)


# Shared schema description appended to every debate-style prompt. Tells
# the LLM how to populate ``claims`` and ``evidence`` so distillation
# can later distinguish sourced facts from inferences.
CLAIMS_SCHEMA_HINT = (
    "Output schema:\n"
    "- ``claims``: a list. Each claim is one atomic factual statement.\n"
    "  Split compound statements into multiple claims so each can be cited.\n"
    "- Each claim has ``evidence: list[Source]`` citing concrete sources:\n"
    "  source_type ∈ {user_input, template_default, ledger_entry, "
    "agent_memory, audit_event, inferred}; ``source_ref`` is the entry id "
    "/ field name / memory id; ``claim_supported`` is a short label.\n"
    "- If you have no concrete source for a claim, attach one Source with "
    "source_type=inferred (or leave evidence empty). Claims marked "
    "inferred-only will be flagged in the UI and will NOT be promoted to "
    "long-term agent memory by distillation — prefer to cite real sources "
    "whenever you can.\n"
    "- The deprecated ``analysis`` string field MAY be left empty; it is "
    "kept only for backward compatibility."
)

if TYPE_CHECKING:
    from kompany.agents.registry import AgentRegistry
    from kompany.core.debate_prep import PreDebateContext
    from kompany.state.episodes import Episodes
    from kompany.state.memory import AgentMemory


# Stage → (active agent roles, number of rounds)
STAGE_PROFILES: dict[str, tuple[list[str], int]] = {
    "solo": (["ceo", "cto", "cpo", "cfo", "cos"], 2),
    "pre-seed": (["ceo", "cto", "cpo", "cos", "cv"], 2),
    "seed": (["ceo", "cto", "cpo", "cmo", "cro", "cos", "cv"], 3),
    "series-a": (
        ["ceo", "cto", "cpo", "cfo", "cmo", "cro", "coo", "csa", "ciso", "cos", "cv"],
        3,
    ),
}

# CEO and CoS don't participate in debate rounds — they synthesize/decide
_NON_DEBATERS = {"ceo", "cos"}


class DebateEngine:
    """Orchestrates multi-agent debates."""

    def __init__(
        self,
        registry: AgentRegistry,
        stage: str = "solo",
        memory: "AgentMemory | None" = None,
        episodes: "Episodes | None" = None,
    ):
        self._registry = registry
        self._stage = stage
        roles, self._num_rounds = STAGE_PROFILES.get(
            stage, STAGE_PROFILES["solo"]
        )
        self._debaters = [r for r in roles if r not in _NON_DEBATERS]
        self._all_roles = roles
        # ADR-0006 pre-debate prep is opt-in: only built when BOTH memory
        # and episodes are wired. No memory/episodes → identical behaviour
        # to the pre-ADR-0006 engine (no prep, no frame challenges).
        self._prepper = None
        if memory is not None and episodes is not None:
            from kompany.core.debate_prep import DebatePrepper

            self._prepper = DebatePrepper(
                llm=registry._llm,
                settings=registry._settings,
                memory=memory,
                episodes=episodes,
            )

    def run(
        self,
        question: str,
        company_state: dict | None = None,
        directive_id: str | None = None,
        decision_type: str | None = None,
    ) -> DebateResult:
        """Run a full debate and return the result.

        ADR-0006: when a prepper is wired (memory + episodes were passed to
        ``__init__``) AND ``decision_type`` is given, a pre-debate context is
        assembled BEFORE round 1. It unions the decision-type roster into the
        debaters for this run and injects frame challenges + per-role prior
        learnings into the round-1 (POSITION) prompt. With no prepper or no
        ``decision_type`` the behaviour is identical to the pre-ADR-0006 path.
        """
        prep = self._prepare(question, directive_id, decision_type)
        debaters = self._effective_debaters(prep)

        all_rounds: list[list[AgentPosition]] = []

        # Round 1 — Independent positions (prep injected here only)
        r1 = self._run_round(
            DebateRound.POSITION, question, [], directive_id, debaters, prep
        )
        all_rounds.append(r1)

        # Round 2 — Rebuttal
        r2 = self._run_round(
            DebateRound.REBUTTAL, question, all_rounds, directive_id, debaters
        )
        all_rounds.append(r2)

        # Round 3 — Convergence (only for 3-round stages)
        if self._num_rounds >= 3:
            r3 = self._run_round(
                DebateRound.CONVERGENCE, question, all_rounds, directive_id,
                debaters,
            )
            all_rounds.append(r3)

        # CoS synthesis
        synthesis = self._synthesize(question, all_rounds, directive_id)

        # CEO decision
        decision = self._ceo_decide(
            question, all_rounds, synthesis, directive_id
        )

        return DebateResult(
            question=question,
            rounds=all_rounds,
            synthesis=synthesis,
            decision=decision,
            agents_participated=[p.agent_role for p in r1],
        )

    def _prepare(
        self,
        question: str,
        directive_id: str | None,
        decision_type: str | None,
    ) -> "PreDebateContext | None":
        """Run pre-debate prep when wired + a decision_type is supplied."""
        if self._prepper is None or not decision_type:
            return None
        return self._prepper.prepare(
            question=question,
            directive_id=directive_id,
            decision_type=decision_type,
            stage=self._stage,
            debaters=self._debaters,
        )

    def _effective_debaters(
        self, prep: "PreDebateContext | None"
    ) -> list[str]:
        """Union the convene roster into the stage debaters for this run.

        Existing debaters are never dropped. Convened roles are added only
        when they exist for this stage (``self._all_roles``) and are not
        synthesis/decision-only roles (CEO / CoS).
        """
        debaters = list(self._debaters)
        if prep is None:
            return debaters
        existing = set(debaters)
        for role in prep.convene_roster:
            if (
                role in self._all_roles
                and role not in _NON_DEBATERS
                and role not in existing
            ):
                debaters.append(role)
                existing.add(role)
        return debaters

    def _run_round(
        self,
        round_type: DebateRound,
        question: str,
        prior_rounds: list[list[AgentPosition]],
        directive_id: str | None,
        debaters: list[str] | None = None,
        prep: "PreDebateContext | None" = None,
    ) -> list[AgentPosition]:
        """Run one debate round across all debating agents."""
        positions: list[AgentPosition] = []
        context = self._format_prior_rounds(prior_rounds)
        roles = debaters if debaters is not None else self._debaters

        # Map round_type → SSE action_type label so the cost meter in the
        # debate UI can attribute spend to the right column.
        action_label = {
            DebateRound.POSITION: "debate_round_1",
            DebateRound.REBUTTAL: "debate_round_2",
            DebateRound.CONVERGENCE: "debate_round_3",
        }.get(round_type, "debate_round")

        for role in roles:
            agent = self._registry.get(role)
            prompt = self._build_round_prompt(
                round_type, question, context, role, prep
            )
            resp = agent.call_structured(
                prompt=prompt,
                output_schema=AgentPosition,
                directive_id=directive_id,
                max_tokens=2048,
                action_type=action_label,
            )
            pos = resp.parsed
            pos.agent_role = role
            pos.agent_name = agent.display_name
            pos.squad = agent.squad
            pos.round = round_type
            positions.append(pos)

        return positions

    def _synthesize(
        self,
        question: str,
        all_rounds: list[list[AgentPosition]],
        directive_id: str | None,
    ) -> DebateSynthesis:
        """CoS synthesizes the debate into a CEO brief."""
        cos = self._registry.get("cos")
        context = self._format_prior_rounds(all_rounds)
        prompt = (
            f"The executive team debated this question:\n\n"
            f'"{question}"\n\n'
            f"Here are all positions from the debate:\n\n{context}\n\n"
            f"As Chief of Staff, synthesize this debate into a CEO brief. "
            f"Identify consensus, key tensions, and your recommended option. "
            f"Be neutral — surface tradeoffs, don't take sides.\n\n"
            f"Express the consensus as a list of atomic ``consensus_claims`` "
            f"(not a free-text paragraph). Cite the source of every factual "
            f"claim — prefer agent_memory / ledger_entry / user_input over "
            f"inferred. The deprecated ``consensus_position`` string MAY be "
            f"left empty.\n\n" + CLAIMS_SCHEMA_HINT
        )
        resp = cos.call_structured(
            prompt=prompt,
            output_schema=DebateSynthesis,
            directive_id=directive_id,
            max_tokens=2048,
            action_type="debate_synthesis",
        )
        return resp.parsed

    def _ceo_decide(
        self,
        question: str,
        all_rounds: list[list[AgentPosition]],
        synthesis: DebateSynthesis,
        directive_id: str | None,
    ) -> CEODecision:
        """CEO makes the final decision based on debate and synthesis."""
        ceo = self._registry.get("ceo")
        context = self._format_prior_rounds(all_rounds)
        consensus_text = self._format_consensus_claims(synthesis)
        prompt = (
            f"The executive team debated:\n\n\"{question}\"\n\n"
            f"Debate positions:\n{context}\n\n"
            f"CoS Synthesis:\n"
            f"- Consensus:\n{consensus_text}\n"
            f"- Tensions: {', '.join(synthesis.key_tensions)}\n"
            f"- Recommended: {synthesis.recommended_option}\n"
            f"- Risks: {', '.join(synthesis.risk_flags)}\n\n"
            f"As CEO, make your final decision. Be decisive. Express your "
            f"reasoning as ``rationale_claims`` (a list of atomic factual "
            f"statements, each with cited evidence). The ``decision`` field "
            f"is the headline verdict; the deprecated ``rationale`` string "
            f"MAY be left empty.\n\n" + CLAIMS_SCHEMA_HINT
        )
        resp = ceo.call_structured(
            prompt=prompt,
            output_schema=CEODecision,
            directive_id=directive_id,
            max_tokens=2048,
            action_type="debate_decision",
        )
        return resp.parsed

    def _build_round_prompt(
        self,
        round_type: DebateRound,
        question: str,
        context: str,
        role: str,
        prep: "PreDebateContext | None" = None,
    ) -> str:
        """Build the prompt for a specific debate round.

        The shared :data:`CLAIMS_SCHEMA_HINT` block is appended so the LLM
        always knows how to populate per-claim evidence. Each claim must
        be one atomic factual statement with a ``Source`` list; the
        deprecated free-text ``analysis`` field MAY be left empty.

        ADR-0006: in the POSITION round, when a :class:`PreDebateContext`
        is present, frame challenges and this role's recalled prior
        learnings are prepended so the agent's opening position is informed
        by both. Other rounds are unaffected.
        """
        if round_type == DebateRound.POSITION:
            prep_block = self._prep_block(prep, role)
            body = (
                f"{prep_block}"
                f'The Master asks: "{question}"\n\n'
                f"Provide your independent position as {role.upper()}. "
                f"Produce 3-5 atomic claims (one factual statement each) and "
                f"a concrete recommendation, plus your confidence level."
            )
        elif round_type == DebateRound.REBUTTAL:
            body = (
                f'The Master asks: "{question}"\n\n'
                f"Prior positions:\n{context}\n\n"
                f"As {role.upper()}, review all positions. "
                f"Acknowledge valid points by name, challenge points you "
                f"disagree with, and update your claim list if warranted. "
                f"Cite the source of every factual claim you add."
            )
        else:  # CONVERGENCE
            body = (
                f'The Master asks: "{question}"\n\n'
                f"Prior rounds:\n{context}\n\n"
                f"As {role.upper()}, move toward consensus. "
                f"State any concessions and any non-negotiable hard lines. "
                f"Cite the source of any new factual claim."
            )
        return body + "\n\n" + CLAIMS_SCHEMA_HINT

    @staticmethod
    def _prep_block(prep: "PreDebateContext | None", role: str) -> str:
        """Render the ADR-0006 prep block for a role's POSITION prompt."""
        if prep is None:
            return ""
        parts: list[str] = []
        frame = prep.frame_block()
        if frame:
            parts.append(frame)
        learnings = prep.learnings_for(role)
        if learnings:
            parts.append(learnings)
        if not parts:
            return ""
        return "\n\n".join(parts) + "\n\n"

    @staticmethod
    def _format_prior_rounds(rounds: list[list[AgentPosition]]) -> str:
        """Format prior rounds into readable context for prompts.

        Renders each position's ``effective_claims`` (new ``claims`` field
        when present, legacy ``analysis`` otherwise) line-by-line so the
        next round's LLM sees the per-claim evidence structure rather than
        a flattened paragraph.
        """
        if not rounds:
            return "(no prior positions)"
        parts: list[str] = []
        for i, rnd in enumerate(rounds, 1):
            parts.append(f"--- Round {i} ---")
            for pos in rnd:
                claim_lines = DebateEngine._format_claim_block(pos.effective_claims())
                parts.append(
                    f"[{pos.agent_name} ({pos.squad})] "
                    f"Recommendation: {pos.recommendation}\n"
                    f"Claims:\n{claim_lines}\n"
                    f"Confidence: {pos.confidence}"
                )
        return "\n\n".join(parts)

    @staticmethod
    def _format_claim_block(claims: list[Claim]) -> str:
        """Format ``Claim`` list into ``▸ text [src1, src2]`` lines."""
        if not claims:
            return "  (no claims)"
        lines: list[str] = []
        for claim in claims:
            sources = [
                s.source_ref or s.source_type.value
                for s in claim.evidence
                if s.source_type != SourceType.INFERRED
            ]
            marker = "  ▸" if sources else "  ⚠"
            src_part = f" [{', '.join(sources)}]" if sources else ""
            lines.append(f"{marker} {claim.text}{src_part}")
        return "\n".join(lines)

    @staticmethod
    def _format_consensus_claims(synthesis: DebateSynthesis) -> str:
        """Render synthesis consensus_claims (or legacy text) for prompts."""
        return DebateEngine._format_claim_block(
            synthesis.effective_consensus_claims()
        )
