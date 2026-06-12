"""Targets, episodes, health, memories and packet execution commands.

Split out of cli.py per ADR-0003 (06-12-adr3-splits). Command bodies are
verbatim moves; they register on the shared ``app`` from ``common``.
"""

from __future__ import annotations

import time  # noqa: F401

import typer
from rich.console import Console  # noqa: F401
from rich.panel import Panel  # noqa: F401
from rich.table import Table  # noqa: F401

from kompany.core.debate import DebateEngine  # noqa: F401

from kompany.interfaces.cli_parts.common import (  # noqa: F401
    app,
    console,
    _get_engine,
    _emit_json,
)



target_app = typer.Typer(
    name="target",
    help="Inspect or refresh the company's agreed targets.",
    no_args_is_help=True,
)
app.add_typer(target_app, name="target")


@target_app.command("show")
def target_show(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show the founder / team_proposal / agreed targets trio.

    Mission-targets task (05-19). ``agreed`` is the authoritative version
    every agent + the watchdog read.
    """
    engine = _get_engine(config)
    bundle = engine.get_targets_bundle()
    payload = {
        "founder": bundle.founder.model_dump(mode="json"),
        "proposal": (
            bundle.proposal.model_dump(mode="json")
            if bundle.proposal is not None
            else None
        ),
        "agreed": (
            bundle.agreed.model_dump(mode="json")
            if bundle.agreed is not None
            else None
        ),
        "review_thread_id": bundle.review_thread_id,
    }
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"founder:   {payload['founder']}\n"
        f"proposal:  {payload['proposal']}\n"
        f"agreed:    {payload['agreed']}\n"
        f"review:    {payload['review_thread_id'] or '(no open review)'}",
        title="Company targets",
    ))


@target_app.command("review")
def target_review(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Re-run the team feasibility review against the current founder targets.

    Creates a fresh ``approval_request(action_type='target_feasibility')``
    so the founder can revise / approve / reject the recommendation. The
    review thread id is mirrored to ``company_config['targets.review_thread_id']``
    and surfaced in ``kompany target show``.
    """
    engine = _get_engine(config)
    payload = engine.run_target_feasibility_review()
    if payload is None:
        console.print(
            "[yellow]No founder targets are set yet — "
            "run `kompany onboard` first.[/yellow]"
        )
        raise typer.Exit(1)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"Review id: {payload.get('id')}\n"
        f"Status: {payload.get('status')}\n"
        f"Summary: {payload.get('summary')}",
        title="Target feasibility review",
    ))


episodes_app = typer.Typer(
    name="episodes",
    help="Browse and rebuild project-episode records (self-learning).",
    no_args_is_help=True,
)
app.add_typer(episodes_app, name="episodes")


health_app = typer.Typer(
    name="health",
    help="Browse and resolve watchdog health events (resilience).",
    no_args_is_help=True,
)
app.add_typer(health_app, name="health")


@health_app.command("list")
def health_list(
    status: str = typer.Option(
        None,
        "--status",
        help="Filter by status (open | resolved | snoozed | dismissed).",
    ),
    kind: str = typer.Option(
        None,
        "--kind",
        help="Filter by kind (silent_run | recovered | retry_exhausted | stranded_in_progress | stranded_todo).",
    ),
    limit: int = typer.Option(50, "--limit"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List health events."""
    engine = _get_engine(config)
    rows = engine.list_health_events(status=status, kind=kind, limit=limit)
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print("[dim]No health events.[/dim]")
        return
    table = Table(title="Health Events")
    table.add_column("ID", style="cyan")
    table.add_column("Kind", style="magenta")
    table.add_column("Status", style="yellow")
    table.add_column("Task", style="dim")
    table.add_column("Created", style="dim")
    for row in rows:
        table.add_row(
            row["id"],
            row["kind"],
            row["status"],
            row.get("task_id") or "",
            row.get("created_at") or "",
        )
    console.print(table)


@health_app.command("show")
def health_show(
    event_id: str = typer.Argument(..., help="Health event id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show one health event by id."""
    engine = _get_engine(config)
    row = engine.get_health_event(event_id)
    if row is None:
        console.print(f"[red]Health event not found: {event_id}[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(row)
        return
    console.print(Panel(
        f"ID: {row['id']}\n"
        f"Kind: {row['kind']}\n"
        f"Status: {row['status']}\n"
        f"Task: {row.get('task_id') or '(none)'}\n"
        f"Project: {row.get('project_id') or '(none)'}\n"
        f"Run: {row.get('run_id') or '(none)'}\n"
        f"Created: {row['created_at']}\n"
        f"Snoozed until: {row.get('snoozed_until') or '(n/a)'}\n"
        f"Resolved by: {row.get('resolved_by') or '(n/a)'}\n"
        f"Resolved at: {row.get('resolved_at') or '(n/a)'}\n"
        f"Detail: {row.get('detail')}",
        title=f"Health Event {event_id}",
    ))


@health_app.command("resolve")
def health_resolve(
    event_id: str = typer.Argument(..., help="Health event id"),
    action: str = typer.Option(
        "continue",
        "--action",
        help="Player action: continue | snooze | dismiss",
    ),
    snooze_minutes: int = typer.Option(
        None,
        "--snooze-minutes",
        help="Snooze duration when action=snooze (defaults to company config).",
    ),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Resolve a health event."""
    engine = _get_engine(config)
    try:
        row = engine.resolve_health_event(
            event_id=event_id,
            action=action,
            snooze_minutes=snooze_minutes,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if row is None:
        console.print(f"[red]Health event not found: {event_id}[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(row)
        return
    console.print(Panel(
        f"Status: {row['status']}\n"
        f"Resolved by: {row.get('resolved_by')}\n"
        f"Snoozed until: {row.get('snoozed_until') or '(n/a)'}",
        title=f"Resolved {event_id}",
    ))


@episodes_app.command("list")
def episodes_list(
    retention: str = typer.Option(
        None,
        "--retention",
        help="Filter by retention tier (full | summary).",
    ),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List materialized project episodes."""
    engine = _get_engine(config)
    rows = engine.list_episodes(retention_tier=retention)
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print("[dim]No episodes recorded yet.[/dim]")
        return
    table = Table(title="Project Episodes")
    table.add_column("Project", style="cyan")
    table.add_column("Tier", style="yellow")
    table.add_column("Updated", style="dim")
    table.add_column("Summary")
    for row in rows:
        table.add_row(
            row["project_id"],
            row["retention_tier"],
            row.get("updated_at") or "",
            (row["summary"] or "")[:80],
        )
    console.print(table)


@episodes_app.command("get")
def episodes_get(
    project_id: str = typer.Argument(..., help="Project id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Fetch one episode (includes payload_json if retention is full)."""
    engine = _get_engine(config)
    row = engine.get_episode(project_id)
    if row is None:
        console.print(f"[red]Episode not found for project: {project_id}[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(row)
        return
    panel_text = (
        f"Project: {row['project_id']}\n"
        f"Retention: {row['retention_tier']}\n"
        f"Updated: {row.get('updated_at')}\n"
        f"Summary: {row['summary']}\n"
        f"Payload: "
        + (
            f"{len(row['payload_json'])} chars"
            if row.get("payload_json")
            else "(trimmed)"
        )
    )
    console.print(Panel(panel_text, title="Episode"))


@episodes_app.command("rebuild")
def episodes_rebuild(
    project_id: str = typer.Argument(..., help="Project id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Re-materialize a project's episode payload from source tables."""
    engine = _get_engine(config)
    try:
        row = engine.rebuild_episode(project_id)
    except LookupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(row)
        return
    console.print(Panel(
        f"Project: {row['project_id']}\n"
        f"Retention: {row['retention_tier']}\n"
        f"Updated: {row.get('updated_at')}\n"
        f"Summary: {row['summary']}",
        title="Episode Rebuilt",
    ))


@app.command("memories")
def memories(
    agent_role: str = typer.Argument(..., help="Agent role"),
    limit: int = typer.Option(20, "--limit", "-n"),
    include_stale: bool = typer.Option(False, "--include-stale"),
    knowledge_type: str = typer.Option(None, "--knowledge-type"),
    category: str = typer.Option(None, "--category"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List memories for an agent."""
    engine = _get_engine(config)
    payload = engine.list_memories(
        agent_role,
        limit=limit,
        include_stale=include_stale,
        knowledge_type=knowledge_type,
        category=category,
    )
    if as_json:
        _emit_json(payload)
        return
    if not payload:
        console.print(f"[dim]No memories for {agent_role}.[/dim]")
        return
    table = Table(title=f"Memories — {agent_role}")
    table.add_column("Category", style="yellow")
    table.add_column("Type", style="cyan")
    table.add_column("Content")
    for m in payload:
        table.add_row(m["category"], m.get("knowledge_type", ""), m["content"][:80])
    console.print(table)


@app.command("release-delivery")
def release_delivery(
    approval_id: str = typer.Argument(..., help="Approved delivery_approval id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Release a delivery package after delivery_approval is approved."""
    engine = _get_engine(config)
    try:
        result = engine.release_delivery(approval_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"Status: {result['status']}\n"
        f"Project: {result.get('project_id')}\n"
        f"Tasks completed: {result['tasks_completed']}\n"
        f"Tasks failed: {result['tasks_failed']}\n"
        f"Released at: {result.get('released_at') or '(not released)'}",
        title="Delivery",
    ))


@app.command("execute-packet")
def execute_packet(
    approval_id: str = typer.Argument(..., help="Approved decision-chain approval id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Execute an approved decision-chain packet under governance."""
    engine = _get_engine(config)
    try:
        with console.status("[bold]Executing approved decision packet..."):
            result = engine.execute_decision_packet(approval_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"Status: {result['status']}\n"
        f"Project ID: {result['project_id']}\n"
        f"Tasks completed: {result['tasks_completed']}\n"
        f"Tasks failed: {result['tasks_failed']}\n"
        f"Delivery approval ID: {result['delivery_approval_id']}",
        title="Governed Execution",
    ))


@app.command("resume-project")
def resume_project(
    project_id: str = typer.Argument(..., help="Project ID to resume"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Resume a project from persisted task/checkpoint state."""
    engine = _get_engine(config)
    try:
        with console.status(f"[bold]Resuming project '{project_id}'..."):
            result = engine.resume_project(project_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"Status: {result['status']}\n"
        f"Tasks completed: {result.get('tasks_completed', 0)}\n"
        f"Tasks failed: {result.get('tasks_failed', 0)}\n"
        f"Latest checkpoint: {(result.get('latest_checkpoint') or {}).get('id', '-')}",
        title=f"Project {project_id} Resume",
    ))


