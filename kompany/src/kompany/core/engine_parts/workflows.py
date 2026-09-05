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
from kompany.state.models import ApprovalRequest

# Approval action_type for a YAML step declared ``autonomy_tier: approval``.
# The card carries the run checkpoint; approving resumes the workflow at
# that step, rejecting ends the run.
ACTION_WORKFLOW_STEP = "workflow_step"


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
        start_at: str | None = None,
        prior_outputs: Mapping[str, Any] | None = None,
        force_auto: frozenset[str] | set[str] | None = None,
        resumed_from: str | None = None,
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
            result = runner.run(
                ctx, start_at=start_at, prior_outputs=prior_outputs, force_auto=force_auto
            )
            failed = [s for s in result.steps if s.error]
            paused = failed[0] if failed and str(failed[0].error).startswith("needs_approval") else None
            if paused is not None:
                card = self._file_workflow_step_gate(
                    runner, paused.step_id, workflow_id=workflow_id, run_id=run_id,
                    inputs=initial_inputs, outputs=result.outputs, project_id=project_id,
                    directive_id=directive_id,
                )
                self.audit.record(
                    "workflow.paused",
                    f"Workflow {workflow_id} paused at {paused.step_id} for founder approval",
                    detail={"workflow_id": workflow_id, "step_id": paused.step_id,
                            "approval_id": card.id, "resumed_from": resumed_from},
                    directive_id=directive_id, project_id=project_id,
                )
                return {
                    "workflow_id": workflow_id, "run_id": run_id, "project_id": project_id,
                    "ok": False, "status": "paused", "paused_at": paused.step_id,
                    "approval_id": card.id, "estimated_cost_usd": estimate.total_usd,
                    "total_cost_usd": result.total_cost_usd,
                    "steps": [{"step_id": s.step_id, "output": s.output, "cost_usd": s.cost_usd,
                               "error": s.error} for s in result.steps if s.step_id != paused.step_id],
                }
            outcome = "workflow.completed" if result.ok else "workflow.failed"
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
            "status": "completed" if result.ok else "failed",
            "resumed_from": resumed_from,
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

    # ------------------------------------------------------------------
    # Approval-gated YAML steps (#42)
    # ------------------------------------------------------------------

    def _file_workflow_step_gate(
        self, runner: Any, step_id: str, *, workflow_id: str, run_id: str,
        inputs: dict[str, Any], outputs: dict[str, Any], project_id: str | None,
        directive_id: str | None,
    ) -> ApprovalRequest:
        step = next(s for s in runner.steps if s["id"] == step_id)
        from kompany.core.step_executor import _format, _scope

        class _Ctx:  # minimal scope carrier for the prompt preview
            initial_inputs = inputs

        preview = _format(step.get("prompt_template", ""), _scope(_Ctx(), outputs))[:1500]
        request = ApprovalRequest(
            action_type=ACTION_WORKFLOW_STEP,
            summary=f"{runner.display_name}: approve step '{step_id}' ({step['agent_role'].upper()})",
            payload={
                "workflow_id": workflow_id, "step_id": step_id, "run_id": run_id,
                "agent_role": step["agent_role"], "project_id": project_id,
                "directive_id": directive_id, "inputs": inputs, "prior_outputs": outputs,
                "estimated_step_cost_usd": float(step.get("cost_estimate_usd") or 0.0),
                "prompt_preview": preview,
                "remaining_steps": [s["id"] for s in runner.steps][
                    [s["id"] for s in runner.steps].index(step_id):],
            },
            directive_id=directive_id, project_id=project_id,
            requested_by=step["agent_role"], severity="high",
        )
        return self.approvals.create(request)

    def resume_workflow_step(self, request: ApprovalRequest) -> dict[str, Any]:
        """Approval effect: re-enter the run at the approved step (idempotent)."""
        payload = request.payload or {}
        if payload.get("effect_applied"):
            return {"status": "already_applied"}
        step_id = str(payload.get("step_id") or "")
        result = self.run_workflow(
            str(payload.get("workflow_id")), payload.get("inputs") or {},
            project_id=payload.get("project_id"), directive_id=payload.get("directive_id"),
            start_at=step_id, prior_outputs=payload.get("prior_outputs") or {},
            force_auto={step_id}, resumed_from=request.id,
        )
        self.approvals.update_payload(request.id, {
            "effect_applied": True, "resumed_run_id": result.get("run_id"),
            "resume_status": result.get("status"),
        })
        try:
            self.approvals.add_comment(
                request.id,
                body=f"Resumed at '{step_id}' → run {result.get('run_id')} {result.get('status')}"
                     + (f"; paused again at '{result.get('paused_at')}'" if result.get("status") == "paused" else ""),
                by_type="system", by_id=None,
            )
        except Exception:  # noqa: BLE001 — outcome is audited by run_workflow
            pass
        return {"status": "resumed", "run": result}

    def reject_workflow_step(self, request: ApprovalRequest) -> dict[str, Any]:
        payload = request.payload or {}
        if payload.get("effect_applied"):
            return {"status": "already_applied"}
        self.approvals.update_payload(request.id, {"effect_applied": True})
        self.audit.record(
            "workflow.cancelled",
            f"Workflow {payload.get('workflow_id')} stopped at {payload.get('step_id')}: founder rejected",
            detail={"workflow_id": payload.get("workflow_id"), "step_id": payload.get("step_id"),
                    "approval_id": request.id, "reason": request.resolution_reason},
            directive_id=payload.get("directive_id"), project_id=payload.get("project_id"),
        )
        return {"status": "cancelled", "stopped_at": payload.get("step_id")}

    def _bind_workflow_plugins(self) -> list[tuple[str, str]]:
        """Call ``Workflow.bind(engine)`` on every discovered plugin workflow.

        Returns ``[(workflow_id, error_repr), ...]`` — an empty list means
        every plugin bound cleanly. Failures are audited and never raise:
        one broken third-party plugin must not block engine boot.
        """
        errors: list[tuple[str, str]] = []
        # Built-in gate for approval-tier YAML steps — registered before any
        # plugin so a plugin cannot shadow it by accident.
        self.register_approval_effect(
            ACTION_WORKFLOW_STEP,
            on_approve=lambda eng, req: eng.resume_workflow_step(req),
            on_reject=lambda eng, req: eng.reject_workflow_step(req),
        )
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
