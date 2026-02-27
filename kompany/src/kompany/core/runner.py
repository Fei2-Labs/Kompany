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

        # Execute each task
        db_tasks = self._engine.projects.list_tasks(project_id)
        for task in db_tasks:
            if task.status != TaskStatus.PENDING:
                continue
            self._execute_task(task, project, result)

        # Check if project is now fully funded
        result.fully_funded = self._engine.projects.is_fully_funded(project_id)
        if result.fully_funded:
            self._engine.projects.update_status(project_id, ProjectStatus.COMPLETED)

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
        )
        return resp.parsed.tasks

    def _execute_task(
        self, task: Task, project: Project, result: ProjectRunResult
    ) -> None:
        """Execute a single task using the assigned agent."""
        # Mark as active
        self._engine.projects.update_task_status(task.id, TaskStatus.ACTIVE)

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

            resp = agent.call(prompt=prompt)

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

        except Exception as e:
            self._engine.projects.update_task_status(
                task.id, TaskStatus.FAILED,
                result={"error": str(e)},
            )
            result.tasks_failed += 1
