"""Skill crystallization (06-16-agentic-chat-engine P5).

After a SUCCESSFUL agentic chat session, distill the tool-use path that
worked into a reusable skill (an SOP + trigger words) and store it via the
:class:`~kompany.state.skills.SkillStore`, so the next similar request reuses
the prior solution instead of re-deriving it (GenericAgent self-evolution).

Gating (cheap by default, never noisy):

- **Success only** — never crystallizes a failed/gated session.
- **Tool-use floor** — a session must have made at least
  ``settings.skill_crystallize_min_tools`` distinct tool calls; a trivial
  one-tool (or no-tool) chat is not worth a skill.
- **Toggleable** — ``settings.skill_crystallization_enabled`` (default ON).
- **Dedupe** — the distilled skill is keyed by name; re-crystallizing the
  same task type REFINES the existing skill row (bumps ``created_count``)
  instead of fanning out near-identical duplicates (handled in
  :meth:`SkillStore.save`).

One ``call_structured`` LLM call does the distillation, booked through the
normal cost path (``action_type="skill_crystallize"``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = ["SkillCrystallizationMixin", "CrystallizedSkill"]

_CRYSTALLIZE_SYSTEM = """\
You distill a SUCCESSFUL work session into a reusable SKILL for next time.
A skill is a minimal SOP (key steps + pitfalls), NOT a tutorial and NOT a
transcript. Output:
- name: a short, stable, reusable skill name (snake-ish, no dates/ids).
- trigger_words: 3-8 lowercase keywords a future similar request would
  contain, so trigger-word retrieval can find this skill. No stopwords.
- when_to_use: one sentence on the situation this applies to.
- sop: concise markdown steps (the tools used and the order that worked,
  plus any pitfall to avoid). Keep it short.
Only describe what ACTUALLY worked in this session. Do not invent steps.
"""


class CrystallizedSkill(BaseModel):
    """The distilled skill the LLM returns."""

    name: str = ""
    trigger_words: list[str] = Field(default_factory=list)
    when_to_use: str = ""
    sop: str = ""


class SkillCrystallizationMixin:
    """Crystallize successful agentic chats into reusable skills."""

    def _crystallization_enabled(self) -> bool:
        return bool(getattr(self.settings, "skill_crystallization_enabled", True))

    def _count_distinct_tools(self, tool_names: list[str]) -> int:
        return len({n for n in tool_names if n})

    def maybe_crystallize_skill(
        self,
        *,
        request: str,
        final_text: str,
        tool_names: list[str],
        succeeded: bool,
        session_id: str | None = None,
        agent_role: str = "ceo",
    ) -> dict[str, Any]:
        """Distill a successful session into a skill, if it clears the gates.

        Returns a small status dict ``{status, ...}`` — ``"disabled"``,
        ``"skipped_failed"``, ``"skipped_trivial"``, ``"saved"`` (with the
        skill name + insert/update action), or ``"error"`` (best-effort: a
        distill failure never breaks the chat). Cost is included when an LLM
        call was made.
        """
        if not self._crystallization_enabled():
            return {"status": "disabled"}
        if not succeeded:
            return {"status": "skipped_failed"}
        min_tools = int(getattr(self.settings, "skill_crystallize_min_tools", 2))
        distinct = self._count_distinct_tools(tool_names)
        if distinct < min_tools:
            return {"status": "skipped_trivial", "distinct_tools": distinct}

        try:
            distilled, cost = self._distill_skill(
                request, final_text, tool_names
            )
        except Exception as exc:  # noqa: BLE001 — distill must not break chat
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

        if not distilled.name or not distilled.sop.strip():
            return {"status": "skipped_empty"}

        triggers = distilled.trigger_words or []
        result = self.skills.save(
            agent_role=agent_role,
            name=distilled.name,
            trigger_words=triggers,
            when_to_use=distilled.when_to_use,
            sop=distilled.sop,
            session_id=session_id,
        )
        self.audit.record(
            "skill.crystallized",
            f"Crystallized skill {distilled.name!r} from a chat session",
            detail={
                "skill_name": distilled.name,
                "action": result["action"],
                "trigger_words": triggers,
                "distinct_tools": distinct,
                "cost_usd": cost,
            },
            agent_role=agent_role,
        )
        return {
            "status": "saved",
            "skill_name": distilled.name,
            "action": result["action"],
            "skill_id": result["id"],
            "cost_usd": cost,
        }

    def _distill_skill(
        self, request: str, final_text: str, tool_names: list[str],
    ) -> tuple[CrystallizedSkill, float]:
        """One cheap LLM call distilling the session into a skill."""
        tools_used = ", ".join(dict.fromkeys(n for n in tool_names if n)) or "(none)"
        prompt = (
            f"Original request:\n{request}\n\n"
            f"Tools that were used (in order): {tools_used}\n\n"
            f"Final answer that worked:\n{final_text[:1500]}\n\n"
            "Distill this into a reusable skill."
        )
        model = self.settings.get_model_for_tier("economy")
        resp = self.llm.call_structured(
            model=model,
            system=_CRYSTALLIZE_SYSTEM,
            prompt=prompt,
            output_schema=CrystallizedSkill,
            agent_name="skill_crystallize",
            action_type="skill_crystallize",
        )
        cost = float(getattr(resp, "cost_usd", 0.0) or 0.0)
        return resp.parsed, cost
