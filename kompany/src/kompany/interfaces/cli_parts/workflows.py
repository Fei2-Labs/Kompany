"""``kompany workflows list|run`` — workflow catalog + execution (contract 1.1.0).

Thin delegates to ``engine.workflows_list`` / ``engine.run_workflow``; same
payload shape as REST ``GET /workflows`` + ``POST /workflows/{id}/run``,
MCP ``kompany_workflows_list`` / ``kompany_workflow_run`` and the SDK.
"""

from __future__ import annotations

import json as _json

import typer
from rich.table import Table

from kompany.interfaces.cli_parts.common import (
    _emit_json,
    _get_engine,
    app,
    console,
)

workflows_app = typer.Typer(
    help="Workflow catalog (built-in + plugin) and execution.",
    no_args_is_help=True,
)
app.add_typer(workflows_app, name="workflows")


@workflows_app.command("list")
def workflows_list_cmd(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List workflows with step count and LLM cost preview."""
    engine = _get_engine(config)
    rows = engine.workflows_list()
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print("[dim]No workflows registered.[/dim]")
        return
    table = Table(title=f"Workflows ({len(rows)})")
    table.add_column("Workflow", style="cyan")
    table.add_column("Source")
    table.add_column("Steps", justify="right")
    table.add_column("Est. cost (USD)", justify="right")
    table.add_column("Display name", style="dim")
    for row in rows:
        if row.get("error"):
            table.add_row(row["workflow_id"], "[red]error[/red]", "-", "-", row["error"])
            continue
        table.add_row(
            row["workflow_id"],
            row["source"],
            str(len(row["steps"])),
            f"{row['estimated_cost_usd']:.2f}",
            row["display_name"],
        )
    console.print(table)


@workflows_app.command("run")
def workflows_run_cmd(
    workflow_id: str = typer.Argument(..., help="Workflow id, e.g. brand-foundation"),
    json_inputs: str = typer.Option(
        "{}", "--json-inputs", help="Initial inputs as a JSON object"
    ),
    project_id: str = typer.Option(None, "--project-id"),
    config: str = typer.Option(None, "--config", "-c"),
):
    """Run a workflow now. Gated steps file inbox cards; nothing auto-spends."""
    from kompany.core.workflows_registry import WorkflowNotFound

    engine = _get_engine(config)
    try:
        inputs = _json.loads(json_inputs)
        if not isinstance(inputs, dict):
            raise ValueError("--json-inputs must be a JSON object")
        result = engine.run_workflow(workflow_id, inputs, project_id=project_id)
    except (ValueError, WorkflowNotFound) as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc
    _emit_json(result)
    status = "[green]ok[/green]" if result["ok"] else "[red]failed[/red]"
    console.print(
        f"Workflow {workflow_id}: {status} — {len(result['steps'])} step(s), "
        f"${result['total_cost_usd']:.2f} spent (run {result['run_id']})"
    )
