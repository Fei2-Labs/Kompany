"""Tool registry for the agentic loop (06-16 P1).

The NativeRunner consumes a ``ToolRegistry`` instead of the hardcoded
``native_tools.execute_tool`` dict. The registry:

- holds ``Tool`` instances,
- renders them to native tool schemas (``ToolSpec``) for ``call_with_tools``,
- dispatches a model-requested call: READ tools run inline; WRITE_LOCAL
  (workspace file edits) also run inline; EXTERNAL_ACTION/SPEND tools route
  through the real approval seam (06-16 P2) when an engine handle is present
  — ``engine.propose_action()`` creates a ``tool_action`` inbox approval and
  the call is PAUSED (observation = "pending founder approval id=…"); the
  approval id is recorded on the ctx so the chat session can surface as
  ``gated`` and resume on approve. With no engine handle (standalone /
  background use) the gated tool is refused with a clear observation.

Every dispatch returns an observation string and NEVER raises, matching
the existing ``native_tools.execute_tool`` contract so the loop can show
the model its mistake and continue.
"""

from __future__ import annotations

from kompany.core.agent_tools.base import SideEffect, Tool, ToolContext
from kompany.llm.client_parts._types import ToolSpec

__all__ = ["ToolRegistry", "GATED_PREFIX"]

GATED_PREFIX = "GATED: "

# Side effects that can run inline in the loop without founder approval.
# READ is pure read-only; WRITE_LOCAL is workspace-scoped file mutation
# (the existing write_file/file_patch tools) — local, reversible, not a
# spend or an external action, so it stays inline like before.
_INLINE_SIDE_EFFECTS = frozenset({SideEffect.READ, SideEffect.WRITE_LOCAL})


class ToolRegistry:
    """An ordered, name-indexed collection of in-loop tools."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        """Native tool schemas for ``call_with_tools``."""
        return [t.to_spec() for t in self._tools.values()]

    def is_inline(self, name: str) -> bool:
        """Whether ``name`` runs inline (READ/WRITE_LOCAL) vs gated."""
        tool = self._tools.get(name)
        return tool is not None and tool.side_effect in _INLINE_SIDE_EFFECTS

    def dispatch(
        self, name: str, args: dict, ctx: ToolContext
    ) -> str:
        """Run one tool by name; ALWAYS returns an observation string.

        READ / WRITE_LOCAL tools run inline. EXTERNAL_ACTION / SPEND tools
        route through ``engine.propose_action`` (06-16 P2): an inbox
        ``tool_action`` approval is created, the call is paused, and the
        approval id is appended to ``ctx.extra["pending_approval_ids"]`` so
        the caller can surface the session as ``gated``. With no engine
        handle the gated tool is refused (standalone/background use).
        """
        tool = self._tools.get(name)
        if tool is None:
            available = ", ".join(self.names()) or "(none)"
            return f"ERROR: unknown tool {name!r}. Available: {available}."
        if tool.side_effect not in _INLINE_SIDE_EFFECTS:
            return self._dispatch_gated(name, dict(args or {}), tool, ctx)
        try:
            return tool.run(dict(args or {}), ctx)
        except Exception as exc:  # noqa: BLE001 — observation, never a crash
            return f"ERROR: tool {name} failed: {type(exc).__name__}: {exc}"

    def _dispatch_gated(
        self, name: str, args: dict, tool: Tool, ctx: ToolContext
    ) -> str:
        """Route a side-effecting tool through the founder approval seam.

        The real action does NOT run here. ``engine.propose_action`` queues
        a ``tool_action`` inbox card carrying tool + inputs + cost preview;
        the action executes for real only on founder approve
        (``execute_approved_tool_action``). The created approval id is
        recorded on the ctx so the chat loop can pause the session as
        ``gated`` and resume when the founder clicks approve.
        """
        engine = getattr(ctx, "engine", None)
        if engine is None or not hasattr(engine, "propose_action"):
            return (
                f"{GATED_PREFIX}tool {name!r} has side_effect "
                f"{tool.side_effect.value!r} and requires founder approval, "
                "but no approval channel is available in this context. "
                "No action taken."
            )
        # ``dispatch_name`` is the engine-side lookup key — for plugin tools
        # whose model-facing ``name`` is sanitized (``email_send``) but whose
        # engine catalog name is the original dotted form (``email.send``).
        dispatch_name = getattr(tool, "dispatch_name", None) or name
        summary = f"Agentic chat requested: {dispatch_name}"
        project_id = ctx.extra.get("project_id")
        task_id = ctx.extra.get("task_id")
        requested_by = ctx.extra.get("agent_role") or "ceo"
        try:
            request = engine.propose_action(
                tool_name=dispatch_name,
                inputs=args,
                summary=summary,
                requested_by=requested_by,
                reason="Requested by the CEO mid-chat (agentic loop).",
                project_id=project_id,
                task_id=task_id,
                cycle_controls=ctx.extra.get("cycle_controls"),
            )
        except Exception as exc:  # noqa: BLE001 — observation, never a crash
            return (
                f"{GATED_PREFIX}tool {dispatch_name!r} could not be proposed for "
                f"approval: {type(exc).__name__}: {exc}. No action taken."
            )
        approval_id = (request or {}).get("id") if isinstance(request, dict) else None
        if approval_id:
            ctx.extra.setdefault("pending_approval_ids", []).append(approval_id)
        return (
            f"{GATED_PREFIX}{name} requires founder approval — created inbox "
            f"approval id={approval_id}. The action is PENDING founder "
            "approval and has NOT run. Do not retry it; tell the founder it "
            "is awaiting their approval and stop."
        )
