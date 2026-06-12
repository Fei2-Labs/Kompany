"""Model-source inspection and switching commands.

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

model_source_app = typer.Typer(
    name="model-source",
    help="Configure where the company's AI work runs (API key or subscription).",
    no_args_is_help=True,
)
app.add_typer(model_source_app, name="model-source")


def _source_panel_lines(source: dict) -> str:
    """Founder-facing summary of one serialized model source."""
    fee = source.get("monthly_fee_usd")
    return "\n".join([
        f"Kind: {source['kind']}",
        f"Billing: {source['billing_mode']}",
        f"Monthly fee: {f'${fee:.2f}' if fee is not None else '—'}",
        f"How work runs: {source.get('execution_summary', '')}",
    ])


@model_source_app.command("show")
def model_source_show(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show the active model source."""
    engine = _get_engine(config)
    source = engine.get_model_source()
    if as_json:
        _emit_json(source)
        return
    if source is None:
        console.print(
            "[dim]No model source configured — calls book real per-token "
            "cost via your API key. Configure one with "
            "'kompany model-source set --kind ...'.[/dim]"
        )
        return
    console.print(Panel(_source_panel_lines(source), title="Model source"))


@model_source_app.command("set")
def model_source_set(
    kind: str = typer.Option(
        None, "--kind",
        help="custom_api | claude_subscription | openai_subscription",
    ),
    monthly_fee: float = typer.Option(
        None, "--monthly-fee",
        help="Monthly subscription fee in USD (required for subscription billing).",
    ),
    billing_mode: str = typer.Option(
        None, "--billing-mode", help="api | subscription (defaults from kind)."
    ),
    clear: bool = typer.Option(
        False, "--clear",
        help="Remove the model source (back to legacy per-token billing).",
    ),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Set (or --clear) the active model source."""
    engine = _get_engine(config)
    payload: dict | None = None
    if not clear:
        if not kind:
            console.print("[red]Pass --kind (or --clear to remove the source).[/red]")
            raise typer.Exit(1)
        payload = {"kind": kind}
        if billing_mode is not None:
            payload["billing_mode"] = billing_mode
        if monthly_fee is not None:
            payload["monthly_fee_usd"] = monthly_fee
    try:
        result = engine.set_model_source(payload)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    source = result["source"]
    if source is None:
        console.print("[green]Model source cleared — legacy per-token billing.[/green]")
        return
    console.print(Panel(_source_panel_lines(source), title="kompany model-source set"))


@model_source_app.command("detect")
def model_source_detect(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Detect installed agent CLIs that unlock zero-key model sources."""
    engine = _get_engine(config)
    clis = engine.detect_agent_clis()
    if as_json:
        _emit_json(clis)
        return
    table = Table(title="Detected agent CLIs")
    table.add_column("CLI", style="cyan")
    table.add_column("Found", style="green")
    table.add_column("Version", style="white")
    table.add_column("Unlocks", style="magenta")
    for name, info in clis.items():
        table.add_row(
            name,
            "✓" if info.get("found") else "✗",
            info.get("version") or "—",
            info.get("source_kind", ""),
        )
    console.print(table)

