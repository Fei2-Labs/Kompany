"""Trace, execute, serve, reset, tools and integrations commands.

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

@app.command()
def trace(
    run_id: str = typer.Argument(..., help="run_id to trace (r_<ulid>)"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show the time-ordered event stream for a run_id.

    Pulls every audit, decision, ledger, memory, task, and approval row
    tagged with the given ``run_id`` and prints them in chronological
    order. Useful for debugging "what actually happened on this directive"
    and for distillation/SOP work downstream.
    """
    engine = _get_engine(config)
    payload = engine.trace_run(run_id)
    if as_json:
        _emit_json(payload)
        return
    if payload["event_count"] == 0:
        console.print(f"[dim]No events found for run_id {run_id}.[/dim]")
        return
    table = Table(title=f"Trace {run_id} ({payload['event_count']} events)")
    table.add_column("Time", style="dim")
    table.add_column("Kind", style="cyan")
    table.add_column("Agent", style="yellow")
    table.add_column("Summary")
    for event in payload["events"]:
        kind = event["kind"]
        if kind == "audit":
            agent = event.get("agent_role") or ""
            summary = f"{event['event_type']}: {event.get('action') or ''}"
        elif kind == "decision":
            agent = ""
            summary = (
                f"{event.get('directive_type') or ''} "
                f"cost=${event.get('total_ai_cost') or 0:.4f}"
            )
        elif kind == "ledger":
            agent = ""
            summary = (
                f"€{event.get('amount') or 0:+.4f} "
                f"({event.get('category') or ''}) "
                f"{event.get('description') or ''}"
            )
        elif kind == "memory":
            agent = event.get("agent_role") or ""
            summary = f"[{event.get('category') or ''}] {event.get('content') or ''}"
        elif kind == "task":
            agent = event.get("assigned_agent") or ""
            summary = (
                f"task {event.get('status') or ''}: "
                f"{event.get('title') or ''}"
            )
        elif kind == "approval":
            agent = event.get("requested_by") or ""
            summary = (
                f"{event.get('action_type') or ''}[{event.get('status') or ''}]: "
                f"{event.get('summary') or ''}"
            )
        else:
            agent = ""
            summary = ""
        table.add_row(
            (event.get("timestamp") or "")[:19],
            kind,
            agent,
            summary[:80],
        )
    console.print(table)


@app.command()
def execute(
    project_id: str = typer.Argument(..., help="Project ID to execute"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Execute a revenue project's tasks autonomously."""
    engine = _get_engine(config)
    p = engine.projects.get(project_id)

    if not p:
        console.print(f"[red]Project '{project_id}' not found.[/red]")
        raise typer.Exit(1)

    with console.status(f"[bold]Executing project '{p.name}'..."):
        result = engine.execute_project(project_id)
    if as_json:
        _emit_json(result)
        return

    console.print(Panel(
        f"Tasks completed: {result['tasks_completed']}\n"
        f"Tasks failed: {result['tasks_failed']}\n"
        f"AI cost: ${result['total_ai_cost']:.4f}\n"
        f"Fully funded: {'Yes' if result['fully_funded'] else 'No'}",
        title=f"Project {project_id} Execution",
    ))

    if result["outputs"]:
        table = Table(title="Task Outputs")
        table.add_column("Task", style="cyan")
        table.add_column("Agent", style="yellow")
        table.add_column("Cost", style="green")
        table.add_column("Output")
        for o in result["outputs"]:
            table.add_row(
                o["title"][:40],
                o["agent"],
                f"${o['cost']:.4f}",
                o["output"][:60] + "..." if len(o["output"]) > 60 else o["output"],
            )
        console.print(table)


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8000, "--port", help="TCP port."),
    open_browser: bool = typer.Option(
        False,
        "--open",
        help="Open the web UI in the default browser after the server starts.",
    ),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="Dev mode: auto-reload on source change (uvicorn --reload).",
    ),
):
    """Start the Kompany backend + cyberpunk web UI.

    The web UI is served at ``/ui`` and the SSE feed at ``/events``. With
    ``--open``, the default browser is launched at the UI URL after a
    short delay so the server is ready to handle the first request.
    """
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        console.print(
            "[red]uvicorn is not installed.[/red] "
            "Install the api extras: pip install 'kompany[api]'"
        )
        raise typer.Exit(1) from exc

    from kompany.interfaces.api_guard import assert_bind_allowed

    try:
        assert_bind_allowed(host, _get_engine(None).settings)
    except SystemExit as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc

    url = f"http://{host}:{port}/ui/"
    console.print(f"[green]Kompany backend on[/green] http://{host}:{port}")
    console.print(f"[green]UI on[/green] {url}")
    console.print(f"[green]SSE stream on[/green] http://{host}:{port}/events")

    if open_browser:
        # Schedule the browser launch slightly after uvicorn starts. We
        # use a small threading.Timer so we don't block before uvicorn.run.
        import threading
        import webbrowser

        def _open():
            try:
                webbrowser.open(url)
            except Exception:  # pragma: no cover — best effort
                pass

        threading.Timer(1.0, _open).start()

    uvicorn.run(
        "kompany.interfaces.api:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


# ---------------------------------------------------------------------------
# kompany reset — wipe DB + state with safety-tiered confirmation.
# ---------------------------------------------------------------------------


def _resolve_data_dir_for_cli(override: str | None):
    """Match the resolution order used by the REST sidecar so reset
    targets the same on-disk store the desktop app boots against
    (unless the user explicitly overrides via --data-dir)."""
    import os
    from pathlib import Path as _Path

    if override:
        return _Path(override).expanduser()
    env = os.environ.get("KOMPANY_DATA_DIR", "").strip()
    if env:
        return _Path(env).expanduser()
    return _Path("~/.kompany").expanduser()


@app.command()
def reset(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the y/N prompt for onboarded (non-live) state. Live state still requires --force.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Skip the typed-RESET confirmation even for live state. Use with care.",
    ),
    no_backup: bool = typer.Option(
        False,
        "--no-backup",
        help="Skip the auto-backup. Irreversible — use only for disposable state.",
    ),
    keep_credentials: bool = typer.Option(
        False,
        "--keep-credentials",
        help="Wipe everything except credential_vault rows so the API key survives.",
    ),
    data_dir: str = typer.Option(
        None,
        "--data-dir",
        help="Override the data directory (defaults to KOMPANY_DATA_DIR env or ~/.kompany).",
    ),
) -> None:
    """Wipe a Kompany install so onboarding starts from scratch.

    Three confirmation tiers based on detected state:

    \b
      * fresh (no template applied)        — no prompt.
      * onboarded, no projects + no spend  — y/N prompt; --yes skips.
      * live (>=1 project OR ledger spend) — must type RESET; --force skips.

    An auto-backup is written to ``<data_dir>.backup-<ISO>`` unless
    ``--no-backup`` is passed.
    """
    from kompany.installer.reset import (
        ResetError,
        inspect_state,
        reset as do_reset,
    )

    target = _resolve_data_dir_for_cli(data_dir)
    console.print(f"[bold]Resetting:[/bold] {target}")

    state = inspect_state(target)
    if not state.exists or state.is_fresh:
        console.print("  [dim]nothing applied — wiping any leftover files[/dim]")
    else:
        console.print(
            f"  template: [cyan]{state.template_id}[/cyan]   "
            f"projects: [cyan]{state.project_count}[/cyan]   "
            f"episodes: [cyan]{state.episode_count}[/cyan]   "
            f"total_spend: [cyan]${state.total_spend_usd:.2f}[/cyan]"
        )
        if state.revenue_target_usd is not None:
            console.print(
                f"  agreed_revenue_target: [cyan]${state.revenue_target_usd:,.2f}[/cyan]   "
                f"deadline: [cyan]{state.deadline or '--'}[/cyan]"
            )

    if not no_backup and (state.exists and not state.is_fresh):
        console.print("  [dim]backup will be written to a sibling .backup-<ISO> dir[/dim]")

    def _confirm(summary, expected: str) -> bool:
        if expected:
            console.print(
                "[yellow]Live state detected[/yellow] — type "
                "[bold]RESET[/bold] to confirm (anything else aborts):"
            )
            try:
                typed = typer.prompt("  >", default="", show_default=False)
            except typer.Abort:
                return False
            return typed.strip() == expected
        try:
            return typer.confirm("Proceed?", default=False)
        except typer.Abort:
            return False

    try:
        result = do_reset(
            target,
            yes=yes,
            force=force,
            no_backup=no_backup,
            keep_credentials=keep_credentials,
            confirm_callback=_confirm,
        )
    except ResetError as exc:
        console.print(f"[red]✗ reset aborted: {exc}[/red]")
        raise typer.Exit(1) from exc

    if result.backup_path:
        console.print(f"  [green]✓[/green] backup: {result.backup_path}")
    if result.credentials_kept:
        console.print("  [green]✓[/green] credential_vault preserved (--keep-credentials)")
    if result.files_removed:
        console.print(f"  [green]✓[/green] files removed: {', '.join(result.files_removed)}")
    if result.dirs_removed:
        console.print(f"  [green]✓[/green] dirs removed: {', '.join(result.dirs_removed)}")
    for note in result.notes:
        console.print(f"  [dim]{note}[/dim]")
    console.print("[green]reset complete[/green]")


# ---------------------------------------------------------------------------
# Tools & actions (#4/#5) — list registered tools, propose an action
# ---------------------------------------------------------------------------

tools_app = typer.Typer(
    name="tools",
    help="Native tools — list the registry, propose an action for approval.",
    no_args_is_help=True,
)
app.add_typer(tools_app)


@tools_app.command("list")
def tools_list_cmd(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List registered tools with side_effect / tier / connection state."""
    engine = _get_engine(config)
    rows = engine.tools_list()
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print("[dim]No tools registered.[/dim]")
        return
    table = Table(title=f"Tools ({len(rows)})")
    table.add_column("Tool", style="cyan")
    table.add_column("Side effect")
    table.add_column("Tier")
    table.add_column("Connected")
    table.add_column("Providers", style="dim")
    for row in rows:
        table.add_row(
            row["name"],
            row["side_effect"] + (" [red](paid)[/red]" if row["paid"] else ""),
            row["autonomy_tier"],
            "[green]yes[/green]" if row["connected"] else "[yellow]no[/yellow]",
            ", ".join(p["integration_id"] for p in row["providers"]),
        )
    console.print(table)


@tools_app.command("propose")
def tools_propose_cmd(
    tool_name: str = typer.Argument(..., help="Tool to propose, e.g. email.send"),
    json_inputs: str = typer.Option(..., "--json-inputs", help="Tool inputs as a JSON object"),
    summary: str = typer.Option(None, "--summary", help="Card summary shown in the inbox"),
    reason: str = typer.Option(None, "--reason", help="Why this action should run"),
    project_id: str = typer.Option(None, "--project-id"),
    config: str = typer.Option(None, "--config", "-c"),
):
    """Propose a tool action — lands in the inbox; approve to execute."""
    import json as _json

    engine = _get_engine(config)
    try:
        inputs = _json.loads(json_inputs)
        if not isinstance(inputs, dict):
            raise ValueError("--json-inputs must be a JSON object")
        result = engine.propose_action(
            tool_name,
            inputs,
            summary=summary or f"Run {tool_name}",
            reason=reason,
            project_id=project_id,
        )
    except ValueError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc
    _emit_json(result)
    console.print(
        f"[green]Proposed.[/green] Approve with: kompany approve {result['id']}"
    )


@app.command("integrations")
def integrations_cmd(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List integrations with required credentials + connection state."""
    engine = _get_engine(config)
    rows = engine.integrations_list()
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print("[dim]No integrations registered.[/dim]")
        return
    table = Table(title=f"Integrations ({len(rows)})")
    table.add_column("Integration", style="cyan")
    table.add_column("Connected")
    table.add_column("Required credentials", style="dim")
    table.add_column("Tools", style="dim")
    for row in rows:
        table.add_row(
            f"{row['display_name']} [dim]({row['integration_id']})[/dim]",
            "[green]yes[/green]" if row["connected"] else "[yellow]no[/yellow]",
            ", ".join(row["required_credentials"]),
            ", ".join(row["tools"]),
        )
    console.print(table)
