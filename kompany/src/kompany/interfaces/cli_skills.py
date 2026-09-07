"""``kompany skills`` sub-app (08-29 R3 skill scopes). Rendering only."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

skills_app = typer.Typer(
    name="skills",
    help="Learned skills (crystallized SOPs) and who may reuse them.",
    no_args_is_help=True,
)
console = Console()


def _engine(config: str | None):
    from kompany.core.engine import KompanyEngine

    return KompanyEngine(config_path=config)


@skills_app.command("list")
def skills_list(
    agent_role: str = typer.Option(None, "--role", help="Skills visible to this role (default: all)"),
    scope: list[str] = typer.Option(None, "--scope", help="builtin | company | agent (repeatable)"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
):
    try:
        rows = _engine(config).skills_list(agent_role, scope or None)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]"); raise typer.Exit(1)
    if as_json:
        console.print_json(data=rows); return
    table = Table(title="Skills")
    for col in ("role", "name", "scope", "used", "triggers"):
        table.add_column(col)
    for r in rows:
        table.add_row(str(r.get("agent_role")), str(r.get("name")), str(r.get("scope")),
                      str(r.get("used_count") or 0), ", ".join(r.get("trigger_words") or [])[:60])
    console.print(table)


@skills_app.command("scope")
def skill_scope(
    agent_role: str = typer.Argument(..., help="Role that learned the skill"),
    name: str = typer.Argument(...),
    scope: str = typer.Argument(..., help="agent (private) | company (shared) | builtin"),
    config: str = typer.Option(None, "--config", "-c"),
):
    """Change who may reuse a skill."""
    try:
        row = _engine(config).skill_set_scope(agent_role, name, scope)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]"); raise typer.Exit(1)
    if row is None:
        console.print("[red]skill not found[/red]"); raise typer.Exit(1)
    console.print(f"{agent_role}/{name} → scope {row['scope']}")


__all__ = ["skills_app"]
