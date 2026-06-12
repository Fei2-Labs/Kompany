"""``kompany self-update`` sub-app — propose/list/show (rendering only).

06-12-self-update-pipeline PR2. All logic lives in
``core/self_update/``; this module renders engine-op results via rich
(interfaces spec: no business logic in an interface file). Sibling of
``cli.py`` because that file is over the 500-line cap — ``cli.py`` just
registers :data:`self_update_app`.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

self_update_app = typer.Typer(
    name="self-update",
    help=(
        "Governed self-modification: propose a code change in the "
        "dedicated repo clone; the merge stays yours."
    ),
    no_args_is_help=True,
)
console = Console()


def _get_engine(config: str | None):
    from kompany.core.engine import KompanyEngine

    return KompanyEngine(config_path=config)


def _emit_json(data) -> None:
    console.print_json(data=data)


def _proposal_panel(row: dict, title: str) -> Panel:
    lines = [
        f"Proposal: {row.get('id')}",
        f"Status: {row.get('status')}",
        f"Tier: {row.get('tier') or '—'}",
        f"Branch: {row.get('branch')}",
        f"Vehicle: {row.get('vehicle') or '—'}",
        f"Cost: ${float(row.get('cost_usd') or 0.0):.2f}",
        f"Instruction: {row.get('instruction')}",
    ]
    diff_stat = (row.get("diff_stat") or "").strip()
    if diff_stat:
        lines.append(f"Diff:\n{diff_stat}")
    test_summary = (row.get("test_summary") or "").strip()
    if test_summary:
        first = test_summary.splitlines()[0]
        lines.append(f"Tests: {first}")
    if row.get("approval_id"):
        lines.append(
            f"Approval: {row['approval_id']} — decide it in the inbox "
            "(approve = push branch + PR; merge stays on GitHub)."
        )
    return Panel("\n".join(lines), title=title)


@self_update_app.command("propose")
def self_update_propose(
    instruction: str = typer.Argument(
        ..., help="What to change and why (plain language)."
    ),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Run the governed propose flow (clone → session → tier guard → tests → card)."""
    engine = _get_engine(config)
    try:
        row = engine.self_update_propose(instruction)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(row)
        return
    console.print(_proposal_panel(row, "kompany self-update propose"))


@self_update_app.command("list")
def self_update_list(
    limit: int = typer.Option(20, "--limit"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Recent self-update proposals, newest first."""
    rows = _get_engine(config).self_update_list(limit=limit)
    if as_json:
        _emit_json(rows)
        return
    table = Table(title="Self-update proposals")
    for col in ("id", "status", "tier", "branch", "instruction"):
        table.add_column(col)
    for row in rows:
        table.add_row(
            str(row.get("id")),
            str(row.get("status")),
            str(row.get("tier") or "—"),
            str(row.get("branch")),
            str(row.get("instruction"))[:60],
        )
    console.print(table)


@self_update_app.command("show")
def self_update_show(
    proposal_id: str = typer.Argument(...),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """One proposal in full."""
    row = _get_engine(config).self_update_show(proposal_id)
    if row is None:
        console.print("[red]proposal not found[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(row)
        return
    console.print(_proposal_panel(row, "kompany self-update show"))


__all__ = ["self_update_app"]
