"""``kompany workspace`` sub-app — multi-brand workspace registry (#15).

A workspace IS a data dir (one isolated brand: own DB, vault, ledger).
All logic lives in ``config/workspaces.py``; this module only renders
(interfaces spec: no business logic in an interface file). Sibling of
``cli.py`` because that file is over the 500-line cap.

No engine boot here on purpose: list/switch/create are pure registry
operations — they must work even when the active workspace is broken
or un-onboarded.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

workspace_app = typer.Typer(
    name="workspace",
    help="Multi-brand workspaces — one isolated data dir per brand.",
    no_args_is_help=True,
)
console = Console()


def _emit_json(data) -> None:
    console.print_json(data=data)


@workspace_app.command("list")
def workspace_list(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """List registered workspaces; the active brand is marked."""
    from kompany.config import workspaces

    payload = workspaces.workspaces_list()
    if as_json:
        _emit_json(payload)
        return
    table = Table(title="Workspaces")
    table.add_column("", width=2)
    table.add_column("Name", style="cyan")
    table.add_column("Label")
    table.add_column("Data dir", style="dim")
    for row in payload["workspaces"]:
        table.add_row(
            "▸" if row["active"] else "",
            row["name"],
            row["label"],
            row["data_dir"],
        )
    console.print(table)
    if payload["env_override"]:
        console.print(
            "[yellow]KOMPANY_DATA_DIR is set — it bypasses the registry; "
            "this process ignores the active workspace.[/yellow]"
        )


@workspace_app.command("switch")
def workspace_switch(
    name: str = typer.Argument(..., help="Workspace name to activate"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Switch the active workspace. Running servers/daemons rebind on
    their next engine init (the sidecar switch endpoint does this
    automatically; a KOMPANY_DATA_DIR-pinned daemon needs a restart)."""
    from kompany.config import workspaces

    try:
        entry = workspaces.set_active(name)
    except workspaces.WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if as_json:
        _emit_json(entry)
        return
    console.print(
        f"Active workspace: [cyan]{entry['name']}[/cyan] → {entry['data_dir']}"
    )


@workspace_app.command("create")
def workspace_create(
    name: str = typer.Argument(..., help="New workspace name (lowercase)"),
    label: str = typer.Option("", "--label", help="Display label"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Create a fresh workspace dir under ~/.kompany-workspaces/<name>
    and register it (not yet active — switch, then onboard)."""
    from kompany.config import workspaces

    try:
        entry = workspaces.create(name, label=label)
    except workspaces.WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if as_json:
        _emit_json(entry)
        return
    console.print(
        f"Created workspace [cyan]{entry['name']}[/cyan] at {entry['data_dir']}\n"
        f"Next: kompany workspace switch {entry['name']}  # then onboard"
    )


@workspace_app.command("remove")
def workspace_remove(
    name: str = typer.Argument(..., help="Workspace name to unregister"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Remove a registry entry. The data dir is NEVER deleted — the
    brand's vault/ledger/DB stay on disk for re-registration."""
    from kompany.config import workspaces

    try:
        entry = workspaces.remove(name)
    except workspaces.WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if as_json:
        _emit_json(entry)
        return
    console.print(
        f"Removed registry entry [cyan]{entry['name']}[/cyan]; "
        f"data kept at {entry['data_dir']}"
    )


__all__ = ["workspace_app"]
