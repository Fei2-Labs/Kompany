"""``kompany channels`` sub-app — status/outbox (rendering only).

06-12-channels PRD D5. All logic lives in ``channels/ops.py``; this
module renders engine-op results via rich (interfaces spec: no business
logic in an interface file). Sibling of ``cli.py`` because that file is
over the 500-line cap — ``cli.py`` just registers :data:`channels_app`.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

channels_app = typer.Typer(
    name="channels",
    help="Bidirectional channels — Telegram chat, outbound drafts, email-in.",
    no_args_is_help=True,
)
console = Console()


def _get_engine(config: str | None):
    from kompany.core.engine import KompanyEngine

    return KompanyEngine(config_path=config)


@channels_app.command("status")
def channels_status(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Channel adapter health + outbox counts."""
    payload = _get_engine(config).channels_status()
    if as_json:
        console.print_json(data=payload)
        return
    tg = payload["telegram"]
    em = payload["email"]
    ob = payload["outbox"]
    lines = [
        f"Telegram: {'configured' if tg['configured'] else 'not configured'}"
        f" / {'running' if tg['running'] else 'stopped'}"
        f" (last update: {tg['last_update_at'] or '—'})",
        f"Email-in: {'configured' if em['configured'] else 'not configured'}"
        f" (every {em['poll_every_ticks']} ticks, "
        f"last poll: {em['last_poll_at'] or '—'})",
        f"Outbox hook: {'enabled' if ob['enabled'] else 'disabled'} — "
        + ", ".join(f"{k}: {v}" for k, v in ob["counts"].items()),
    ]
    console.print(Panel("\n".join(lines), title="Channels"))


@channels_app.command("outbox")
def channels_outbox(
    config: str = typer.Option(None, "--config", "-c"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Recent outbound drafts, newest first (drafts-only MVP)."""
    payload = _get_engine(config).outbox_list(limit=limit)
    if as_json:
        console.print_json(data=payload)
        return
    if not payload:
        console.print("[dim]Outbox is empty.[/dim]")
        return
    table = Table(title="Channel Outbox (approve = copy/post manually)")
    table.add_column("ID", style="cyan")
    table.add_column("Channel")
    table.add_column("Status")
    table.add_column("Text")
    table.add_column("Created", style="dim")
    for row in payload:
        table.add_row(
            row["id"],
            row["channel"],
            row["status"],
            (row["text"] or "")[:60],
            row["created_at"],
        )
    console.print(table)


__all__ = ["channels_app"]
