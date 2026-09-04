"""Workflow plugin operations — list, run, and boot-time plugin binding.

Closes the gap between the declarative :class:`WorkflowRunner` (YAML +
``python_callables``) and the running engine: ``run_workflow`` wires the
production step executor, the agent registry, a full ``ToolContext``
service bundle (contract 1.1.0) and a run id, then audits the outcome.
LLM cost is booked by ``BaseAgent.call`` inside each step — nothing here
touches the ledger directly.

Approval gates inside a workflow are filed by the workflow's own Python
steps against the existing ``ApprovalRequests`` inbox (never a parallel
table); the effect of the founder's decision is registered per
``action_type`` through :meth:`ApprovalsMixin.register_approval_effect`,
typically from :meth:`Workflow.bind`, which :meth:`_bind_workflow_plugins`
invokes once at engine init.
"""

from __future__ import annotations

from typing import Any, Mapping

from kompany.core.event_hub import get_event_hub
from kompany.core.run_context import run_scope
from kompany.core.step_executor import ExecutorContext, default_step_executor
from kompany.core.tool_actions import build_tool_context
from kompany.core import workflows_registry


class WorkflowsMixin:
    """Engine mixin: workflow catalog + execution + plugin binding."""

    def workflows_list(self) -> list[dict[str, Any]]:
        """Catalog of built-in + plugin workflows with cost preview.

        Same shape on CLI ``kompany workflows list``, REST
        ``GET /workflows``, MCP ``kompany_workflows_list`` and the SDK.
        """
        rows: list[dict[str, Any]] = []
        for workflow_id in workflows_registry.list_workflows():
            try:
                runner = workflows_registry.get(workflow_id)
            except Exception as exc:  # noqa: BLE001 — one bad YAML must not hide the rest
                rows.append({"workflow_id": workflow_id, "error": repr(exc)})
                continue
            estimate = runner.estimate_cost()
            rows.append(
                {
                    "workflow_id": runner.workflow_id,
                    "display_name": runner.display_name,
                    "description": str(runner._data.get("description") or "").strip(),
                    "source": (
                        "plugin"
                        if workflows_registry.plugin_for(workflow_id) is not None
                        else "builtin"
                    ),
                    "steps": [
                        {
                            "id": s["id"],
                            "agent_role": s["agent_role"],
                            "autonomy_tier": s.get("autonomy_tier", "auto"),
                            "cost_estimate_usd": s.get("cost_estimate_usd"),
                            "python_callable": s.get("python_callable"),
                        }
                        for s in runner.steps
                    ],
                    "estimated_cost_usd": round(estimate.total_usd, 4),
                    "estimate_confidence": estimate.confidence,
                }
            )
        return rows

    def run_workflow(
        self,
        workflow_id: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        project_id: str | None = None,
        directive_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a workflow end to end and return a JSON-able result.

        Raises ``WorkflowNotFound`` for an unknown id. Cost PREVIEW is
        published before the first step (``workflow.cost_preview``); the
        LEDGER rows land per step via the agents' own LLM calls; the
        outcome is audited as ``workflow.completed`` / ``workflow.failed``.
        """
        plugin = workflows_registry.plugin_for(workflow_id)
        runner = workflows_registry.get(
            workflow_id,
            python_callables=getattr(plugin, "python_callables", None) or None,
            step_executor=default_step_executor,
        )
        estimate = runner.estimate_cost()
        initial_inputs = dict(inputs or {})
        with run_scope() as run_id:
            tool_ctx = build_tool_context(self, project_id=project_id, run_id=run_id)
            self.audit.record(
                "workflow.started",
                f"Workflow {workflow_id} started",
                detail={
                    "workflow_id": workflow_id,
                    "estimated_cost_usd": estimate.total_usd,
                    "inputs": sorted(initial_inputs),
                },
                directive_id=directive_id,
                project_id=project_id,
            )
            get_event_hub().publish(
                "workflow.cost_preview",
                {
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "estimated_cost_usd": estimate.total_usd,
                    "confidence": estimate.confidence,
                    "project_id": project_id,
                },
            )
            ctx = ExecutorContext(
                registry=self.registry,
                runner=runner,
                company_state=self._workflow_company_state(),
                directive_id=directive_id,
                initial_inputs=initial_inputs,
                tool_context=tool_ctx,
            )
            result = runner.run(ctx)
            outcome = "workflow.completed" if result.ok else "workflow.failed"
            failed = [s for s in result.steps if s.error]
            self.audit.record(
                outcome,
                f"Workflow {workflow_id} {'completed' if result.ok else 'failed'}",
                detail={
                    "workflow_id": workflow_id,
                    "steps_run": len(result.steps),
                    "total_cost_usd": result.total_cost_usd,
                    "failed_step": failed[0].step_id if failed else None,
                    "error": failed[0].error if failed else None,
                },
                directive_id=directive_id,
                project_id=project_id,
            )
        return {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "project_id": project_id,
            "ok": result.ok,
            "estimated_cost_usd": estimate.total_usd,
            "total_cost_usd": result.total_cost_usd,
            "steps": [
                {
                    "step_id": s.step_id,
                    "output": s.output,
                    "cost_usd": s.cost_usd,
                    "error": s.error,
                }
                for s in result.steps
            ],
        }

    def _bind_workflow_plugins(self) -> list[tuple[str, str]]:
        """Call ``Workflow.bind(engine)`` on every discovered plugin workflow.

        Returns ``[(workflow_id, error_repr), ...]`` — an empty list means
        every plugin bound cleanly. Failures are audited and never raise:
        one broken third-party plugin must not block engine boot.
        """
        errors: list[tuple[str, str]] = []
        try:
            from kompany.plugins.loader import registered

            plugins = registered("workflow")
        except Exception as exc:  # noqa: BLE001
            return [("<discovery>", repr(exc))]
        for plugin in plugins:
            bind = getattr(plugin, "bind", None)
            if not callable(bind):
                continue
            wid = str(getattr(plugin, "workflow_id", "") or type(plugin).__name__)
            try:
                bind(self)
            except Exception as exc:  # noqa: BLE001 — surfaced, not fatal
                errors.append((wid, repr(exc)))
                try:
                    self.audit.record(
                        "plugin.bind_failed",
                        f"Workflow plugin {wid} failed to bind",
                        detail={"workflow_id": wid, "error": repr(exc)},
                    )
                except Exception:  # noqa: BLE001 — audit must never block boot
                    pass
        return errors

    def _workflow_company_state(self) -> dict[str, Any] | None:
        getter = getattr(self, "get_company_state", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:  # noqa: BLE001 — a missing company is not an error here
            return None


__all__ = ["WorkflowsMixin"]
