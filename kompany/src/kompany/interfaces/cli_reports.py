"""``kompany report`` sub-app — founder reports (rendering only).

09-26-autopilot-reports. Logic lives in ``core/founder_report.py``; this
module renders results via rich. Sibling of ``cli.py`` (over the cap).
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

report_app = typer.Typer(
    name="report",
    help="Founder reports — daily / weekly autopilot reports, or one right now.",
    no_args_is_help=False,
    invoke_without_command=True,
)
console = Console()


def _get_engine(config: str | None):
    from kompany.core.engine import KompanyEngine

    return KompanyEngine(config_path=config)


@report_app.callback()
def report_now(
    ctx: typer.Context,
    config: str = typer.Option(None, "--config", "-c"),
    period: str = typer.Option("daily", "--period", "-p", help="daily | weekly | manual"),
    generate: bool = typer.Option(
        False, "--now", help="Generate a fresh report of the last 24h now"
    ),
    deliver: bool = typer.Option(False, "--deliver", help="With --now: also push it"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Show the latest report (default), or generate one with --now."""
    if ctx.invoked_subcommand is not None:
        return
    from kompany.core import founder_report as _fr

    engine = _get_engine(config)
    if generate:
        row = _fr.generate_report(engine, "manual", deliver=deliver)
    else:
        row = _fr.report_latest(engine, period)
    if as_json:
        console.print(json.dumps(row, indent=2, default=str))
        return
    if row is None:
        console.print(f"[dim]No {period} report yet. Try `kompany report --now`.[/dim]")
        return
    title = f"{row['period'].capitalize()} report · {row['generated_at']}"
    console.print(Panel(row["narrative"], title=title))
    if row.get("delivery"):
        statuses = ", ".join(str(d.get("status")) for d in row["delivery"])
        console.print(f"[dim]delivery: {statuses}[/dim]")


@report_app.command("list")
def report_list(
    config: str = typer.Option(None, "--config", "-c"),
    period: str = typer.Option(None, "--period", "-p"),
    limit: int = typer.Option(20, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Recent reports, newest first."""
    from kompany.core import founder_report as _fr

    rows = _fr.reports_list(_get_engine(config), period=period, limit=limit)
    if as_json:
        console.print(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        console.print("[dim]No reports yet.[/dim]")
        return
    table = Table(title="Founder reports")
    table.add_column("Generated", style="cyan")
    table.add_column("Period")
    table.add_column("Delivered", style="dim")
    table.add_column("Narrative")
    for r in rows:
        delivered = ", ".join(str(d.get("status")) for d in r.get("delivery") or []) or "—"
        table.add_row(
            str(r["generated_at"]), r["period"], delivered, r["narrative"][:120]
        )
    console.print(table)


__all__ = ["report_app"]
