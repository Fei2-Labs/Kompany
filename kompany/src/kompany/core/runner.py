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

        # Decompose the project plan into tasks
        tasks = self._decompose(project)

        # Create tasks in DB
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
        if result.fully_funded:
            self._engine.projects.update_status(project.id, ProjectStatus.COMPLETED)
        return result

    def _decompose(self, project: Project) -> list[TaskSpec]:
        """Use CEO to decompose a project's revenue paths into tasks."""
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
