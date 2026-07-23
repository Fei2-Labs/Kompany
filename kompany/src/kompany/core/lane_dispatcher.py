"""Lane dispatcher — N independent lane-workers over the daemon (ADR-0005).

The pre-lane ticker advanced AT MOST one task per tick of the oldest
eligible project. This dispatcher generalises that to: for every healthy
lane with free capacity, claim work (intake item first, else the same
oldest-eligible-project selection), take the lane's lease, advance ONE
task via the EXISTING ``ProjectRunner.run_one_pending`` slice, release
the lease, and record the outcome.

Behaviour parity: with the single default ``main`` lane (max_concurrent
1) this is exactly today's ``_action_advance`` — same project ordering,
same budget/approval gates (reused verbatim, never bypassed), one task
per dispatch.

ADR-0005 hard rule — silent success is a bug: if the runner raises
:class:`LLMUnavailable` (model exhausted, no fallback left) the dispatch
records a ``lane_timeout`` health event and marks the intake item
``failed``. It NEVER reports an idle "ok". Other runner exceptions are
left to the existing per-task failure path inside the runner.
"""

from __future__ import annotations

import logging
from typing import Any

from kompany.core.run_context import run_scope
from kompany.core.watchdog import LLMUnavailable
from kompany.state.models import TaskStatus

log = logging.getLogger(__name__)

# Lease TTL: longer than a normal task advance, short enough that a
# crashed lane frees up within a few ticks.
DEFAULT_LEASE_TTL_SECONDS = 900


def _status_value(status: Any) -> str:
    return status.value if isinstance(status, TaskStatus) else str(status)


class LaneDispatcher:
    """Schedules one task per healthy lane per dispatch pass."""

    def __init__(
        self,
        engine: Any,
        registry: Any,
        intake: Any,
        lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ):
        self._engine = engine
        self._registry = registry
        self._intake = intake
        self.lease_ttl_seconds = int(lease_ttl_seconds)

    def dispatch_once(self) -> list[str]:
        """Run one dispatch pass across all healthy lanes. Returns actions.

        Honours suspend (the founder brake): a suspended runtime dispatches
        nothing, exactly like the ticker's runtime gate.
        """
        runtime = self._engine.runtime.get() or {}
        if runtime.get("state") == "suspended":
            return ["idle_suspended"]

        actions: list[str] = []
        blocked = self._projects_awaiting_budget_approval()
        for lane in self._registry.list_healthy():
            actions.extend(self._dispatch_lane(lane, blocked))
        if not actions:
            actions.append("no_work")
        return actions

    # ------------------------------------------------------------------
    # Per-lane
    # ------------------------------------------------------------------

    def _dispatch_lane(self, lane: dict[str, Any], blocked: set[str]) -> list[str]:
        lane_id = lane["lane_id"]
        # Free capacity: the MVP runs one task per lane per pass, so a lane
        # holding a live lease is busy.
        if self._registry.has_active_lease(lane_id):
            return [f"lane_busy:{lane_id}"]

        item = self._intake.pop_for_lane(lane_id)
        project_id = self._select_project(item, blocked)
        if project_id is None:
            if item is not None:
                # Claimed an item but it had no runnable project — fail it
                # loudly rather than dropping it silently.
                self._intake.mark_failed(item["id"], "no_eligible_project")
                return [f"lane_no_project:{lane_id}"]
            return []

        if not self._registry.acquire_lease(
            lane_id, task_id=None, run_id=None, ttl_seconds=self.lease_ttl_seconds
        ):
            # Lost the race for the lease — leave the item assigned; the
            # next pass re-evaluates it.
            return [f"lane_busy:{lane_id}"]

        try:
            return self._run_in_lane(lane_id, project_id, item)
        finally:
            self._registry.release_lease(lane_id)

    def _run_in_lane(
        self, lane_id: str, project_id: str, item: dict[str, Any] | None
    ) -> list[str]:
        try:
            with run_scope(parent=self._delegation_parent_run(project_id)):
                task_id = self._run_one_pending(project_id)
        except LLMUnavailable as exc:
            # ADR-0005 hard rule: model unavailable is a FAILURE, never a
            # silent success. Record a health event and fail the item.
            self._record_lane_failure(lane_id, project_id, item, exc)
            return [f"lane_failed:{lane_id}"]

        if item is not None:
            if task_id:
                self._intake.mark_completed(
                    item["id"], {"task_id": task_id, "lane_id": lane_id}
                )
            else:
                self._intake.mark_failed(item["id"], "no_pending_task")
        if task_id:
            return [f"advanced_task:{task_id}"]
        return [f"lane_idle:{lane_id}"]

    def _delegation_parent_run(self, project_id: str) -> str | None:
        delegations = getattr(self._engine, "delegations", None)
        if delegations is None:
            return None
        pending = next(
            (
                task
                for task in self._engine.projects.list_tasks(project_id)
                if _status_value(task.status) == TaskStatus.PENDING.value
            ),
            None,
        )
        if pending is None or not pending.delegation_id:
            return None
        delegation = delegations.get(pending.delegation_id)
        return delegation.parent_run_id if delegation is not None else None

    def _record_lane_failure(
        self,
        lane_id: str,
        project_id: str,
        item: dict[str, Any] | None,
        exc: Exception,
    ) -> None:
        reason = f"{type(exc).__name__}: {exc}"
        if item is not None:
            self._intake.mark_failed(item["id"], reason)
        self._registry.mark_stalled(lane_id, reason)
        health = getattr(self._engine, "health_events", None)
        if health is not None:
            try:
                health.record(
                    "lane_timeout",
                    project_id=project_id,
                    detail={"lane_id": lane_id, "error": reason[:500]},
                )
            except Exception:  # noqa: BLE001 — health write must not mask the failure
                log.debug("lane health record failed", exc_info=True)

    # ------------------------------------------------------------------
    # Project selection (parity with ticker._action_advance)
    # ------------------------------------------------------------------

    def _select_project(
        self, item: dict[str, Any] | None, blocked: set[str]
    ) -> str | None:
        """Resolve which project this lane advances.

        Intake item with a runnable project wins; otherwise fall back to
        the oldest eligible active project — the exact selection today's
        ticker uses, so an empty queue behaves identically.
        """
        if item is not None:
            pid = item.get("project_id")
            if pid and self._project_runnable(pid, blocked):
                return pid
            return None
        return self._oldest_eligible_project(blocked)

    def _oldest_eligible_project(self, blocked: set[str]) -> str | None:
        candidates = sorted(
            self._engine.projects.list_active(), key=lambda p: p.created_at
        )
        for project in candidates:
            if not self._project_runnable(project.id, blocked):
                continue
            return project.id
        return None

    def _project_runnable(self, project_id: str, blocked: set[str]) -> bool:
        if project_id in blocked:
            return False
        tasks = self._engine.projects.list_tasks(project_id)
        # A project with NO tasks stays eligible (run_one_pending
        # decomposes-if-empty). Skip only when tasks exist but none pend.
        if tasks and not any(
            _status_value(t.status) == TaskStatus.PENDING.value for t in tasks
        ):
            return False
        return True

    def _projects_awaiting_budget_approval(self) -> set[str]:
        from kompany.core.harness_execution import (
            ACTION_BUDGET_INCREASE,
            ACTION_ENVELOPE_TOPUP,
        )

        blocking = {ACTION_ENVELOPE_TOPUP, ACTION_BUDGET_INCREASE}
        return {
            req.project_id
            for req in self._engine.approvals.list_pending()
            if req.action_type in blocking and req.project_id
        }

    def _run_one_pending(self, project_id: str) -> str | None:
        from kompany.core.runner import ProjectRunner

        return ProjectRunner(self._engine).run_one_pending(project_id)


__all__ = ["LaneDispatcher", "DEFAULT_LEASE_TTL_SECONDS"]
