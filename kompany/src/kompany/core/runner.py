"""ProjectRunner — autonomous execution of revenue projects."""

from __future__ import annotations

from kompany.core.harness_execution import (
    execute_harness_task,
    resolve_caps,
    select_runner,
)
# Extracted to core/task_outcome.py (06-12-daemon-tick-loop PR1) to keep
# this file under the 500-line cap. The alias preserves existing imports.
from kompany.core.task_outcome import classify_outcome as _classify_outcome
from kompany.core.task_outcome import resolve_outcome as _resolve_outcome
# Decomposition logic extracted to runner_parts/decompose.py (ADR-0003).
from kompany.core.runner_parts.decompose import decompose as _decompose_fn
from kompany.core.runner_parts.decompose import (
    decompose_unfiltered as _decompose_unfiltered_fn,
)
# Models re-exported here so all existing import sites remain valid.
from kompany.core.runner_parts._models import (  # noqa: F401 — public re-export
    ProjectRunResult,
    TaskDecomposition,
    TaskSpec,
)
from kompany.state.models import (
    Project,
    ProjectStatus,
    Task,
    TaskStatus,
)


class ProjectRunner:
    """Executes revenue projects by decomposing paths into tasks and running them."""

    def __init__(self, engine):
        # Avoid circular import — engine is passed at runtime
        self._engine = engine

    # Thin instance wrappers around the extracted free functions (ADR-0003).
    # Kept as methods so call sites and tests that reference / monkeypatch
    # ``runner._decompose`` keep working unchanged.
    def _decompose(self, project: Project) -> list[TaskSpec]:
        return _decompose_fn(self._engine, project)

    def _decompose_unfiltered(
        self, project: Project, rules: dict | None = None
    ) -> list[TaskSpec]:
        return _decompose_unfiltered_fn(self._engine, project, rules)

    def run(self, project_id: str) -> ProjectRunResult:
        """Execute a revenue project's tasks."""
        project = self._engine.projects.get(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found")

        result = ProjectRunResult(project_id=project_id)
        self._engine.audit.record(
            "project.execution_started",
            "Started project execution",
            project_id=project_id,
        )

        self._ensure_tasks(project)

        result = self._run_existing_tasks(project, result)

        self._engine.audit.record(
            "project.execution_completed",
            "Completed project execution",
            detail={
                "tasks_completed": result.tasks_completed,
                "tasks_failed": result.tasks_failed,
                "fully_funded": result.fully_funded,
            },
            project_id=project_id,
        )
        return result

    def resume(self, project_id: str) -> ProjectRunResult:
        """Resume an existing project without re-running completed tasks."""
        project = self._engine.projects.get(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found")

        result = ProjectRunResult(project_id=project_id)
        latest = self._engine.checkpoints.latest(project_id)
        self._engine.audit.record(
            "project.resume_started",
            "Started project resume",
            detail={"checkpoint_id": latest["id"] if latest else None},
            project_id=project_id,
        )

        result = self._run_existing_tasks(project, result, retry_failed=True)

        self._engine.audit.record(
            "project.resume_completed",
            "Completed project resume",
            detail={
                "tasks_completed": result.tasks_completed,
                "tasks_failed": result.tasks_failed,
                "fully_funded": result.fully_funded,
            },
            project_id=project_id,
        )
        return result

    def _run_existing_tasks(
        self,
        project: Project,
        result: ProjectRunResult,
        retry_failed: bool = False,
    ) -> ProjectRunResult:
        db_tasks = self._engine.projects.list_tasks(project.id)
        for task in db_tasks:
            status = task.status.value if isinstance(task.status, TaskStatus) else task.status
            if status == TaskStatus.COMPLETED.value:
                continue
            if retry_failed and status in {TaskStatus.FAILED.value, TaskStatus.ACTIVE.value}:
                self._engine.projects.update_task_status(task.id, TaskStatus.PENDING)
                task.status = TaskStatus.PENDING
                status = TaskStatus.PENDING.value
            if status != TaskStatus.PENDING.value:
                continue
            self._execute_task(task, project, result)

        return self._finalize_project(project, result)

    def run_one_pending(self, project_id: str) -> str | None:
        """Execute AT MOST one pending task of this project (ticker slice).

        Daemon tick loop (06-12-daemon-tick-loop D3): the ticker advances
        one task per tick, bounded by the same envelope guards / per-task
        caps that ``_execute_task`` already enforces. Returns the executed
        task's id, or ``None`` when the project is missing or has no
        pending task. Shares ``_execute_task`` and the completion check
        with the full run/resume paths.

        Deliberately PENDING-only: unlike ``resume`` (``retry_failed=
        True``), this slice never resets ``failed``/``active`` tasks back
        to pending — an unattended daemon must not grind retries against
        a task that keeps failing; retry stays a founder-initiated
        ``resume`` decision.
        """
        project = self._engine.projects.get(project_id)
        if not project:
            return None
        # A freshly created project has no tasks until its plan is
        # decomposed — without this the ticker would only ever continue
        # projects a founder session had already kicked off, never start
        # new ones.
        self._ensure_tasks(project)
        for task in self._engine.projects.list_tasks(project_id):
            status = task.status.value if isinstance(task.status, TaskStatus) else task.status
            if status != TaskStatus.PENDING.value:
                continue
            result = ProjectRunResult(project_id=project_id)
            self._execute_task(task, project, result)
            self._finalize_project(project, result)
            return task.id
        return None

    def _ensure_tasks(self, project: Project) -> None:
        """Decompose the plan into tasks only when none exist yet.

        Re-entering an already-decomposed project (founder re-pick, second
        kickoff, every ticker pass) must NOT create a duplicate task set —
        it just runs whatever pending tasks remain.
        """
        if self._engine.projects.list_tasks(project.id):
            return
        for spec in self._decompose(project):
            cap, turns = resolve_caps(spec.budget_cap_usd, spec.max_turns)
            self._engine.projects.create_task(
                Task(
                    project_id=project.id,
                    title=spec.title,
                    assigned_agent=spec.assigned_agent,
                    budget_cap_usd=cap,
                    max_turns=turns,
                )
            )

    def _finalize_project(
        self, project: Project, result: ProjectRunResult
    ) -> ProjectRunResult:
        """Funding/completion bookkeeping shared by run, resume, and the
        ticker's one-task slice."""
        result.fully_funded = self._engine.projects.is_fully_funded(project.id)
        # Mark the project completed when EITHER it's fully funded (revenue
        # projects) OR — for a first-move directive specifically — every
        # task is done. First-move directives have no funding target, so
        # without the all-tasks-done branch they ran all their tasks but
        # stayed 'active' forever (no episode, dashboard stuck on "team
        # working" + 0 episodes). The branch is scoped to first-move plans
        # so it does NOT bypass the delivery-approval gate that governs
        # decision-packet / revenue projects (a rejected delivery must
        # keep the project incomplete).
        plan = project.plan or {}
        is_first_move = (
            bool(plan.get("week_plan"))
            or str(plan.get("source", "")).startswith("team_proposal_first_week")
        )
        terminal_vals = {s.value for s in TaskStatus.terminal()}
        all_tasks = self._engine.projects.list_tasks(project.id)
        unfinished = [
            t for t in all_tasks
            if (t.status.value if isinstance(t.status, TaskStatus) else t.status)
            not in terminal_vals
        ]
        if result.fully_funded or (is_first_move and all_tasks and not unfinished):
            self._engine.projects.update_status(project.id, ProjectStatus.COMPLETED)
            # Materialize the episode now so the dashboard's EPISODES
            # panel shows the finished run. Best-effort — a
            # materialization failure must not break the run result.
            try:
                self._engine.episodes.record_or_update(project.id)
            except Exception as exc:  # noqa: BLE001
                self._engine.audit.record(
                    "learning.episode_record_failed",
                    "Episode materialization failed after completion",
                    detail={"project_id": project.id, "error": str(exc)},
                    project_id=project.id,
                )
        return result

    def _execute_task(
        self, task: Task, project: Project, result: ProjectRunResult
    ) -> None:
        """Execute a single task using the assigned agent."""
        # Harness execution leg (06-11-harness-execution-leg PR4): when a
        # ModelSource is configured (and the feature flag is on), every
        # task runs a full agentic harness session instead of one
        # structured LLM call. ``model_source=None`` or flag off → the
        # legacy single-call path below, untouched.
        runner = select_runner(
            getattr(self._engine, "settings", None),
            health_events=getattr(self._engine, "health_events", None),
            llm_client=getattr(self._engine, "llm", None),
        )
        if runner is not None:
            execute_harness_task(self._engine, runner, task, project, result)
            return
        # Mark as active
        self._engine.projects.update_task_status(task.id, TaskStatus.ACTIVE)
        self._engine.agent_status.set(
            task.assigned_agent,
            "working",
            task.title,
            project_id=project.id,
            project_type=project.type.value,
        )
        self._engine.audit.record(
            "task.started",
            "Started task execution",
            detail={"task_id": task.id, "title": task.title},
            agent_role=task.assigned_agent,
            directive_id=project.triggers_directive_id,
            project_id=project.id,
        )

        try:
            agent = self._engine.registry.get(task.assigned_agent)
            memory_ctx = self._engine.memory.recall_text(
                task.assigned_agent,
                query=f"{task.title} {project.name}",
            )

            prompt = (
                f"Project: {project.name}\n"
                f"Task: {task.title}\n\n"
                f"Execute this task and provide your output.\n"
            )
            if memory_ctx:
                prompt = f"{memory_ctx}\n\n{prompt}"

            resp = agent.call(prompt=prompt, action_type="agent_task_execute")

            # Honest-outcome classification (hybrid mode, step A) + the
            # #13 propose-instead-of-block hook: an outreach task whose
            # email integration IS connected files a tool_action draft
            # card (founder approves, the team sends) instead of blocking.
            #   completed → a real tool/integration actually acted
            #   blocked   → a capability is missing — founder_action names
            #               the connection to make, never founder labor
            #   delivered → an asset was produced / an action was proposed
            outcome, founder_action = _resolve_outcome(
                self._engine, task, project, resp.text
            )
            status = {
                "blocked": TaskStatus.BLOCKED,
                "delivered": TaskStatus.DELIVERED,
                "completed": TaskStatus.COMPLETED,
            }.get(outcome, TaskStatus.DELIVERED)

            task_result = {
                "output": resp.text,
                "cost": resp.cost_usd,
                "outcome": outcome,
                "founder_action": founder_action,
            }
            self._engine.projects.update_task_status(
                task.id, status, result=task_result
            )

            # Virtual clock model D: 1 completed task = 1 virtual day.
            # The dashboard's runway counter advances here, not on the
            # wall clock, so a founder who pauses Kompany doesn't lose
            # days and a team that burns through 5 tasks quickly still
            # consumes 5 days of budget.
            from kompany.state import virtual_clock

            virtual_clock.tick(
                self._engine.db,
                "task.completed",
                detail={
                    "task_id": task.id,
                    "project_id": project.id,
                    "agent": task.assigned_agent,
                },
                audit=self._engine.audit,
                project_id=project.id,
            )

            # Record a memory for the agent
            self._engine.memory.remember(
                agent_role=task.assigned_agent,
                content=f"Completed task '{task.title}' for project '{project.name}'",
                category="task_completion",
                directive_id=project.triggers_directive_id,
            )

            result.tasks_completed += 1
            result.total_ai_cost += resp.cost_usd
            result.outputs.append({
                "task_id": task.id,
                "title": task.title,
                "agent": task.assigned_agent,
                "output": resp.text[:500],
                "cost": resp.cost_usd,
            })
            self._engine.checkpoints.save(
                project_id=project.id,
                task_id=task.id,
                step_index=result.tasks_completed + result.tasks_failed,
                state={
                    "last_completed_task": task.id,
                    "tasks_completed": result.tasks_completed,
                    "tasks_failed": result.tasks_failed,
                },
            )
            self._engine.audit.record(
                "checkpoint.saved",
                "Saved checkpoint after task completion",
                detail={"task_id": task.id},
                agent_role=task.assigned_agent,
                directive_id=project.triggers_directive_id,
                project_id=project.id,
            )
            self._engine.audit.record(
                "task.completed",
                "Completed task execution",
                detail={"task_id": task.id, "cost": resp.cost_usd},
                agent_role=task.assigned_agent,
                directive_id=project.triggers_directive_id,
                project_id=project.id,
            )

        except Exception as e:
            self._engine.projects.update_task_status(
                task.id, TaskStatus.FAILED,
                result={"error": str(e)},
            )
            self._engine.checkpoints.save(
                project_id=project.id,
                task_id=task.id,
                step_index=result.tasks_completed + result.tasks_failed,
                state={
                    "failed_task": task.id,
                    "error": str(e),
                    "tasks_completed": result.tasks_completed,
                    "tasks_failed": result.tasks_failed + 1,
                },
            )
            self._engine.audit.record(
                "task.failed",
                "Task execution failed",
                detail={"task_id": task.id, "error": str(e)},
                agent_role=task.assigned_agent,
                directive_id=project.triggers_directive_id,
                project_id=project.id,
            )
            result.tasks_failed += 1
        finally:
            self._engine.agent_status.set(task.assigned_agent, "idle")
