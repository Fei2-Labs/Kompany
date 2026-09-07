"""``kompany workflows list|show|run`` — workflow catalog + execution (contract 1.1.0).

Thin delegates to ``engine.workflows_list`` / ``engine.run_workflow``; same
payload shape as REST ``GET /workflows`` + ``POST /workflows/{id}/run``,
MCP ``kompany_workflows_list`` / ``kompany_workflow_run`` and the SDK.
``show`` renders one ``workflows_list`` row; ``run --dry-run`` is the
PREVIEW leg (rendered prompts + cost, no spend).
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


def _example_run_command(row: dict) -> str:
    """Copy-pasteable ``kompany workflows run`` built from ``example`` values
    of the required inputs (auto-filled inputs are left to the engine)."""
    examples = {
        i["name"]: i["example"]
        for i in row.get("inputs", [])
        if i.get("required") and i.get("example") is not None
    }
    cmd = f"kompany workflows run {row['workflow_id']}"
    if examples:
        cmd += f" --json-inputs '{_json.dumps(examples, ensure_ascii=False)}'"
    return cmd


@workflows_app.command("show")
def workflows_show_cmd(
    workflow_id: str = typer.Argument(..., help="Workflow id, e.g. idea-validation"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show one workflow: inputs, steps, cost estimate and an example run command."""
    engine = _get_engine(config)
    row = next((r for r in engine.workflows_list() if r["workflow_id"] == workflow_id), None)
    if row is None:
        console.print(f"[red]✗ workflow not found: {workflow_id!r}[/red] — see `kompany workflows list`")
        raise typer.Exit(1)
    if as_json:
        _emit_json(row)
        return
    if row.get("error"):
        console.print(f"[red]✗ {row['error']}[/red]")
        raise typer.Exit(1)
    console.print(f"[bold cyan]{row['display_name']}[/bold cyan]  [dim]({row['workflow_id']}, {row['source']})[/dim]")
    if row.get("description"):
        console.print(row["description"])
    inputs = row.get("inputs", [])
    if inputs:
        table = Table(title="Inputs")
        table.add_column("Name", style="cyan")
        table.add_column("Required")
        table.add_column("Auto-fill source", style="dim")
        table.add_column("Example")
        table.add_column("Description")
        for i in inputs:
            table.add_row(
                i["name"],
                "yes" if i["required"] else "no",
                i.get("source") or "-",
                "-" if i.get("example") is None else str(i["example"]),
                i.get("description") or "",
            )
        console.print(table)
    else:
        console.print("[dim]No declared inputs.[/dim]")
    steps = Table(title="Steps")
    steps.add_column("#", justify="right")
    steps.add_column("Step", style="cyan")
    steps.add_column("Role")
    steps.add_column("Tier")
    steps.add_column("Est. cost (USD)", justify="right")
    for n, st in enumerate(row["steps"], 1):
        est = st.get("cost_estimate_usd")
        tier = st.get("autonomy_tier", "auto")
        tier_txt = f"[yellow]{tier}[/yellow]" if tier != "auto" else tier
        steps.add_row(str(n), st["id"], st["agent_role"].upper(), tier_txt,
                      "-" if est is None else f"{float(est):.2f}")
    console.print(steps)
    console.print(
        f"Total estimate: [bold]${row['estimated_cost_usd']:.2f}[/bold] "
        f"(confidence {row['estimate_confidence']:.2f}). Preview first with --dry-run:"
    )
    console.print(f"  {_example_run_command(row)} --dry-run")
    console.print(f"  {_example_run_command(row)}")


@workflows_app.command("run")
def workflows_run_cmd(
    workflow_id: str = typer.Argument(..., help="Workflow id, e.g. idea-validation"),
    json_inputs: str = typer.Option(
        "{}", "--json-inputs", help="Initial inputs as a JSON object"
    ),
    project_id: str = typer.Option(None, "--project-id"),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Preview: resolve inputs and render every prompt without calling an LLM or spending.",
    ),
    config: str = typer.Option(None, "--config", "-c"),
):
    """Run a workflow now. Gated steps file inbox cards; nothing auto-spends.

    Missing required inputs fail BEFORE any LLM call. Use --dry-run to see
    the resolved inputs and rendered prompts first.
    """
    from kompany.core.workflows_registry import WorkflowNotFound

    engine = _get_engine(config)
    try:
        inputs = _json.loads(json_inputs)
        if not isinstance(inputs, dict):
            raise ValueError("--json-inputs must be a JSON object")
        result = engine.run_workflow(
            workflow_id, inputs, project_id=project_id, dry_run=dry_run
        )
    except (ValueError, WorkflowNotFound) as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc
    _emit_json(result)
    if result["status"] == "dry_run":
        console.print(
            f"Workflow {workflow_id}: [cyan]dry run[/cyan] — {len(result['steps'])} step(s), "
            f"estimated ${result['estimated_cost_usd']:.2f}, $0.00 spent. "
            f"Drop --dry-run to execute."
        )
        return
    if result["status"] == "paused":
        console.print(
            f"Workflow {workflow_id}: [yellow]paused[/yellow] at '{result['paused_at']}' — "
            f"approve inbox card {result['approval_id']} to resume "
            f"(${result['total_cost_usd']:.2f} spent so far, run {result['run_id']})"
        )
        return
    status = "[green]ok[/green]" if result["ok"] else "[red]failed[/red]"
    console.print(
        f"Workflow {workflow_id}: {status} — {len(result['steps'])} step(s), "
        f"${result['total_cost_usd']:.2f} spent (run {result['run_id']})"
    )
