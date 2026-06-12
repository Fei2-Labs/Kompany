"""Shared Typer app + late-bound helpers for cli_parts modules.

``_get_engine`` / ``_emit_json`` delegate to ``kompany.interfaces.cli`` at
call time so tests that monkeypatch ``kompany.interfaces.cli._get_engine``
keep working after the ADR-0003 split.
"""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="kompany",
    help="Autonomous business operating system for solo founders.",
    no_args_is_help=True,
)
console = Console()


def _get_engine(config: str | None = None):
    from kompany.interfaces import cli

    return cli._get_engine(config)


def _emit_json(data):
    from kompany.interfaces import cli

    return cli._emit_json(data)
