"""Debate engine — orchestrates multi-agent debates for strategic decisions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kompany.core.debate_models import (
    AgentPosition,
    CEODecision,
    DebateResult,
    DebateRound,
    DebateSynthesis,
)

if TYPE_CHECKING:
    from kompany.agents.registry import AgentRegistry


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

    def __init__(self, registry: AgentRegistry, stage: str = "solo"):
        self._registry = registry
        self._stage = stage
        roles, self._num_rounds = STAGE_PROFILES.get(
            stage, STAGE_PROFILES["solo"]
        )
        self._debaters = [r for r in roles if r not in _NON_DEBATERS]
        self._all_roles = roles

    def run(
        self,
        question: str,
        company_state: dict | None = None,
        directive_id: str | None = None,
    ) -> DebateResult:
        """Run a full debate and return the result."""
        all_rounds: list[list[AgentPosition]] = []

        # Round 1 — Independent positions
        r1 = self._run_round(
            DebateRound.POSITION, question, [], directive_id
        )
        all_rounds.append(r1)

        # Round 2 — Rebuttal
        r2 = self._run_round(
            DebateRound.REBUTTAL, question, all_rounds, directive_id
        )
        all_rounds.append(r2)

        # Round 3 — Convergence (only for 3-round stages)
        if self._num_rounds >= 3:
            r3 = self._run_round(
                DebateRound.CONVERGENCE, question, all_rounds, directive_id
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

    def _run_round(
        self,
        round_type: DebateRound,
        question: str,
        prior_rounds: list[list[AgentPosition]],
        directive_id: str | None,
    ) -> list[AgentPosition]:
        """Run one debate round across all debating agents."""
        positions: list[AgentPosition] = []
        context = self._format_prior_rounds(prior_rounds)

        for role in self._debaters:
            agent = self._registry.get(role)
            prompt = self._build_round_prompt(
                round_type, question, context, role
            )
            resp = agent.call_structured(
                prompt=prompt,
                output_schema=AgentPosition,
                directive_id=directive_id,
                max_tokens=2048,
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
            f"Be neutral — surface tradeoffs, don't take sides."
        )
        resp = cos.call_structured(
            prompt=prompt,
            output_schema=DebateSynthesis,
            directive_id=directive_id,
            max_tokens=2048,
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
        prompt = (
            f"The executive team debated:\n\n\"{question}\"\n\n"
            f"Debate positions:\n{context}\n\n"
            f"CoS Synthesis:\n"
            f"- Consensus: {synthesis.consensus_position}\n"
            f"- Tensions: {', '.join(synthesis.key_tensions)}\n"
            f"- Recommended: {synthesis.recommended_option}\n"
            f"- Risks: {', '.join(synthesis.risk_flags)}\n\n"
            f"As CEO, make your final decision. Be decisive."
        )
        resp = ceo.call_structured(
            prompt=prompt,
            output_schema=CEODecision,
            directive_id=directive_id,
            max_tokens=2048,
        )
        return resp.parsed

    def _build_round_prompt(
        self,
        round_type: DebateRound,
        question: str,
        context: str,
        role: str,
    ) -> str:
        """Build the prompt for a specific debate round."""
        if round_type == DebateRound.POSITION:
            return (
                f'The Master asks: "{question}"\n\n'
                f"Provide your independent position as {role.upper()}. "
                f"Give domain-specific analysis (3-5 sentences), "
                f"a concrete recommendation, and your confidence level."
            )
        elif round_type == DebateRound.REBUTTAL:
            return (
                f'The Master asks: "{question}"\n\n'
                f"Prior positions:\n{context}\n\n"
                f"As {role.upper()}, review all positions. "
                f"Acknowledge valid points by name, challenge points you "
                f"disagree with, and update your position if warranted."
            )
        else:  # CONVERGENCE
            return (
                f'The Master asks: "{question}"\n\n'
                f"Prior rounds:\n{context}\n\n"
                f"As {role.upper()}, move toward consensus. "
                f"State any concessions and any non-negotiable hard lines."
            )

    @staticmethod
    def _format_prior_rounds(rounds: list[list[AgentPosition]]) -> str:
        """Format prior rounds into readable context for prompts."""
        if not rounds:
            return "(no prior positions)"
        parts: list[str] = []
        for i, rnd in enumerate(rounds, 1):
            parts.append(f"--- Round {i} ---")
            for pos in rnd:
                parts.append(
                    f"[{pos.agent_name} ({pos.squad})] "
                    f"Recommendation: {pos.recommendation}\n"
                    f"Analysis: {pos.analysis}\n"
                    f"Confidence: {pos.confidence}"
                )
        return "\n\n".join(parts)
