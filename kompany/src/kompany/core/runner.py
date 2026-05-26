"""ProjectRunner — autonomous execution of revenue projects."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from kompany.state.models import (
    Project,
    ProjectStatus,
    Task,
    TaskStatus,
)


class TaskSpec(BaseModel):
    """A single task specification."""
    title: str
    assigned_agent: str
    prompt: str


class TaskDecomposition(BaseModel):
    """CEO's breakdown of a revenue path into executable tasks."""
    tasks: list[TaskSpec] = Field(default_factory=list)


class ProjectRunResult(BaseModel):
    """Result of running a project."""
    project_id: str
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_ai_cost: float = 0.0
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    fully_funded: bool = False


class ProjectRunner:
    """Executes revenue projects by decomposing paths into tasks and running them."""

    def __init__(self, engine):
        # Avoid circular import — engine is passed at runtime
        self._engine = engine

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

        # Decompose only if the project has no tasks yet. Re-running an
        # already-decomposed project (e.g. the founder re-picked it from
        # step 5, or a second kickoff fired) must NOT create a duplicate
        # task set — it should just run whatever pending tasks remain.
        existing_tasks = self._engine.projects.list_tasks(project_id)
        if not existing_tasks:
            tasks = self._decompose(project)
            for spec in tasks:
                task = Task(
                    project_id=project_id,
                    title=spec.title,
                    assigned_agent=spec.assigned_agent,
                )
                self._engine.projects.create_task(task)

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
        all_tasks = self._engine.projects.list_tasks(project.id)
        unfinished = [
            t for t in all_tasks
            if (t.status.value if isinstance(t.status, TaskStatus) else t.status)
            not in {TaskStatus.COMPLETED.value}
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

    def _decompose(self, project: Project) -> list[TaskSpec]:
        """Use CEO to decompose a project's revenue paths into tasks.

        Two project shapes flow through here:

        1. **Revenue projects** (template ``apply_template`` revenue
           drafts): plan blob has ``paths`` → CEO LLM-decomposes into
           3-5 TaskSpecs.
        2. **First-move directives** (team-proposed at step 5 of
           onboarding): plan blob has ``week_plan`` (a 5-entry list,
           one per weekday) + ``proposer_role`` + ``other_agents_involved``.
           The team already decomposed the work; we just turn each day
           into a TaskSpec and rotate ownership through the proposer +
           collaborators. No extra LLM call — week_plan IS the plan.
        """
        # Branch 2: first-move directive — week_plan already exists.
        week_plan = project.plan.get("week_plan") or []
        if week_plan:
            proposer = (project.plan.get("proposer_role") or "ceo").lower()
            collabs = [
                str(r).lower()
                for r in (project.plan.get("other_agents_involved") or [])
            ]
            owners = [proposer, *collabs] or ["ceo"]
            tasks: list[TaskSpec] = []
            for i, line in enumerate(week_plan):
                title = str(line).strip()
                if not title:
                    continue
                # Round-robin across owners so different agents take
                # different days — keeps the office visibly busy
                # instead of one CEO doing everything.
                owner = owners[i % len(owners)]
                tasks.append(
                    TaskSpec(
                        title=title[:140],
                        assigned_agent=owner,
                        prompt=(
                            f"Project: {project.name}\n"
                            f"Day {i + 1} of week-1 plan: {title}\n\n"
                            "Execute this concretely. Return what you "
                            "did, what you found, and the next step."
                        ),
                    )
                )
            return tasks

        # Branch 1: classic revenue path decomposition.
        paths = project.plan.get("paths", [])
        if not paths:
            return []

        # Pick the recommended path, or first available
        recommended = project.plan.get("recommended_path", "")
        target_path = None
        for p in paths:
            if p.get("name") == recommended:
                target_path = p
                break
        if not target_path:
            target_path = paths[0]

        state = self._engine.get_company_state()
        ceo = self._engine.registry.get("ceo", company_state=state)

        prompt = (
            f"Project: {project.name}\n"
            f"Revenue path: {target_path.get('name', 'unknown')}\n"
            f"Description: {target_path.get('description', '')}\n"
            f"Target revenue: €{target_path.get('estimated_revenue_eur', 0):.0f}\n"
            f"Timeframe: {target_path.get('timeframe', 'unknown')}\n\n"
            f"Break this into 3-5 concrete tasks. Available agents:\n"
            f"- researcher: market research, competitor analysis\n"
            f"- writer: content creation, proposals, landing pages\n"
            f"- analyst: financial modeling, ROI calculations\n"
            f"- builder: code, configurations, integrations\n"
            f"- procurement: sourcing, vendor evaluation\n"
        )

        resp = ceo.call_structured(
            prompt=prompt,
            output_schema=TaskDecomposition,
            action_type="agent_task_execute",
        )
        return resp.parsed.tasks

    def _execute_task(
        self, task: Task, project: Project, result: ProjectRunResult
    ) -> None:
        """Execute a single task using the assigned agent."""
        # Mark as active
        self._engine.projects.update_task_status(task.id, TaskStatus.ACTIVE)
        self._engine.agent_status.set(task.assigned_agent, "working", task.title)
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
            memory_ctx = self._engine.memory.recall_text(task.assigned_agent)

            prompt = (
                f"Project: {project.name}\n"
                f"Task: {task.title}\n\n"
                f"Execute this task and provide your output.\n"
            )
            if memory_ctx:
                prompt = f"{memory_ctx}\n\n{prompt}"

            resp = agent.call(prompt=prompt, action_type="agent_task_execute")

            # Store result
            task_result = {"output": resp.text, "cost": resp.cost_usd}
            self._engine.projects.update_task_status(
                task.id, TaskStatus.COMPLETED, result=task_result
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
