"""In-loop memory tools (06-16-agentic-chat-engine P5).

Lets the agentic CEO read and write its own memory mid-task, the way
GenericAgent exposes ``update_working_checkpoint`` / long-term-update as
first-class tools rather than a side system:

- ``recall`` — READ: trigger-word search over learned skills AND prior
  agent memories. Classified ``SideEffect.READ`` so the registry runs it
  inline (no approval, no external side effect).
- ``remember`` — WRITE_LOCAL: persist a durable note to ``agent_memories``.
  Local memory only (a row in the engine DB), never an external action, so
  it is classified ``SideEffect.WRITE_LOCAL`` and runs inline too.
- ``save_skill`` — WRITE_LOCAL: let the agent crystallize a skill explicitly
  (name + trigger words + SOP) into the skill store, same inline class.

All three are local-state tools — they never reach the founder approval
seam, which is reserved for EXTERNAL_ACTION / SPEND.
"""

from __future__ import annotations

from typing import Any

from kompany.core.agent_tools.base import FunctionTool, SideEffect, ToolContext

__all__ = ["memory_tools", "recall_tool", "remember_tool", "save_skill_tool"]

_AGENT_ROLE = "ceo"


def _recall(args: dict[str, Any], ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "ERROR: recall requires a 'query'."
    engine = getattr(ctx, "engine", None)
    if engine is None:
        return "recall: no memory available in this context."
    parts: list[str] = []
    skills = getattr(engine, "skills", None)
    if skills is not None:
        skill_text = skills.retrieve_text(_AGENT_ROLE, query)
        if skill_text:
            parts.append(skill_text)
    memory = getattr(engine, "memory", None)
    if memory is not None:
        mem_text = memory.recall_text(_AGENT_ROLE, limit=5, query=query)
        if mem_text:
            parts.append(mem_text)
    return "\n\n".join(parts) if parts else f"recall: nothing found for {query!r}."


def _remember(args: dict[str, Any], ctx: ToolContext) -> str:
    note = str(args.get("note", "")).strip()
    if not note:
        return "ERROR: remember requires a 'note'."
    engine = getattr(ctx, "engine", None)
    memory = getattr(engine, "memory", None)
    if memory is None:
        return "remember: no memory store available; note not saved."
    category = str(args.get("category", "observation")).strip() or "observation"
    mem_id = memory.remember(_AGENT_ROLE, note, category=category)
    return f"remember: saved note id={mem_id} (category={category})."


def _save_skill(args: dict[str, Any], ctx: ToolContext) -> str:
    name = str(args.get("name", "")).strip()
    sop = str(args.get("sop", "")).strip()
    if not name or not sop:
        return "ERROR: save_skill requires 'name' and 'sop'."
    engine = getattr(ctx, "engine", None)
    skills = getattr(engine, "skills", None)
    if skills is None:
        return "save_skill: no skill store available; skill not saved."
    result = skills.save(
        agent_role=_AGENT_ROLE,
        name=name,
        trigger_words=args.get("trigger_words", []),
        when_to_use=str(args.get("when_to_use", "")).strip(),
        sop=sop,
        code=(str(args.get("code", "")).strip() or None),
    )
    return f"save_skill: {result['action']} skill {name!r} (id={result['id']})."


def recall_tool() -> FunctionTool:
    return FunctionTool(
        name="recall",
        description=(
            "Search your own memory (learned skills + prior learnings) by "
            "keywords before doing work — reuse what you solved before. "
            "Read-only."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you want to recall (keywords/task).",
                },
            },
            "required": ["query"],
        },
        side_effect=SideEffect.READ,
        func=_recall,
    )


def remember_tool() -> FunctionTool:
    return FunctionTool(
        name="remember",
        description=(
            "Save a durable note to your local memory (a fact, preference, or "
            "pitfall worth keeping). Local only — not an external action."
        ),
        parameters={
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "The note to keep."},
                "category": {
                    "type": "string",
                    "description": "Optional tag (default 'observation').",
                },
            },
            "required": ["note"],
        },
        side_effect=SideEffect.WRITE_LOCAL,
        func=_remember,
    )


def save_skill_tool() -> FunctionTool:
    return FunctionTool(
        name="save_skill",
        description=(
            "Crystallize a reusable skill: a named SOP (markdown steps) with "
            "trigger words so a later similar task reuses it. Local memory "
            "only."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short skill name."},
                "trigger_words": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords that route a task to this skill.",
                },
                "when_to_use": {"type": "string"},
                "sop": {"type": "string", "description": "The SOP body (markdown)."},
                "code": {"type": "string", "description": "Optional snippet."},
            },
            "required": ["name", "sop"],
        },
        side_effect=SideEffect.WRITE_LOCAL,
        func=_save_skill,
    )


def memory_tools() -> list[FunctionTool]:
    return [recall_tool(), remember_tool(), save_skill_tool()]
