"""CoS agent — Chief of Staff. Synthesis, debate moderation, and coordination."""

from __future__ import annotations

from typing import Any

from kompany.agents.base import BaseAgent
from kompany.agents.cos_distillation import (
    DISTILLATION_SYSTEM_PROMPT,
    DistillationOutput,
    build_distillation_user_prompt,
)
from kompany.llm.client import LLMResponse


class CoSAgent(BaseAgent):
    """CoS — Chief of Staff. Synthesizes debates, coordinates cross-squad work."""

    role = "cos"
    display_name = "CoS"
    model_tier = "primary"
    squad = "strategy"

    def system_prompt(self) -> str:
        prompt = (
            "You are the Chief of Staff (CoS). You synthesize multi-agent debates, "
            "identify consensus and dissent, coordinate cross-functional initiatives, "
            "and prepare decision briefs for the CEO. You are neutral and analytical. "
            "Surface tradeoffs clearly. Never take sides — illuminate the landscape."
        )
        return self.with_soul_context(prompt)

    def distill(
        self,
        episode_summaries: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Run the cross-episode distillation LLM call.

        The caller has already pre-selected and pre-summarized the episodes
        (see :mod:`kompany.agents.cos_distillation`); this method only owns
        the LLM round-trip itself so the engine can wrap the surrounding
        DB writes / audit events in a single ``run_scope``.

        The returned :class:`LLMResponse` carries the parsed
        :class:`DistillationOutput` on ``.parsed``. The watchdog +
        run-id-aware client wrapper handles silent-run / retry semantics.
        """
        user_prompt = build_distillation_user_prompt(episode_summaries)
        model = self.settings.get_model_for_tier(self.model_tier)
        resp = self.llm.call_structured(
            model=model,
            system=DISTILLATION_SYSTEM_PROMPT,
            prompt=user_prompt,
            output_schema=DistillationOutput,
            agent_name=self.display_name,
            max_tokens=max_tokens,
        )
        self.cost_accumulated += resp.cost_usd
        return resp
