"""``kompany anima`` sub-app — state/diary (rendering only).

06-12-anima-persona PRD D5. All logic lives in ``core/anima.py``; this
module renders engine-op results via rich (interfaces spec: no business
logic in an interface file). Sibling of ``cli.py`` because that file is
over the 500-line cap — ``cli.py`` just registers :data:`anima_app`.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

anima_app = typer.Typer(
    name="anima",
    help="Anima — the company's persona layer (emotion state + diary).",
    no_args_is_help=True,
)
console = Console()


def _get_engine(config: str | None):
    from kompany.core.engine import KompanyEngine

    return KompanyEngine(config_path=config)


def _emit_json(data) -> None:
    console.print_json(data=data)


@anima_app.command("state")
def anima_state(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Current persona state: valence, energy, derived tone."""
    payload = _get_engine(config).anima_state()
    if as_json:
        _emit_json(payload)
        return
    lines = [
        f"Valence: {payload['valence']:+.2f}",
        f"Energy: {payload['energy']:.2f}",
        f"Tone: {payload['tone']}",
        f"Last diary: {payload['last_diary_date'] or '—'}",
        f"Enabled: {'yes' if payload['enabled'] else 'no'}",
    ]
    console.print(Panel("\n".join(lines), title="Anima"))


@anima_app.command("diary")
def anima_diary(
    config: str = typer.Option(None, "--config", "-c"),
    limit: int = typer.Option(30, "--limit", "-n", help="Max entries"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Recent diary entries, newest first."""
    payload = _get_engine(config).anima_diary_list(limit=limit)
    if as_json:
        _emit_json(payload)
        return
    if not payload:
        console.print("[dim]No diary entries yet.[/dim]")
        return
    table = Table(title="Anima Diary")
    table.add_column("Date", style="cyan")
    table.add_column("Tone", style="dim")
    table.add_column("Entry")
    for row in payload:
        from kompany.core.anima import derive_tone

        table.add_row(
            row["date"],
            derive_tone(float(row["valence"]), float(row["energy"])),
            row["entry"],
        )
    console.print(table)


__all__ = ["anima_app"]
