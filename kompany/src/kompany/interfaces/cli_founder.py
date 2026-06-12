"""``kompany founder`` sub-app — profile + rules (#6/#7).

All logic lives in ``core/founder_config.py`` via the engine's thin
wrappers; this module renders engine-op results via rich (interfaces
spec: no business logic in an interface file). Sibling of ``cli.py``
because that file is over the 500-line cap — ``cli.py`` just registers
:data:`founder_app`.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

founder_app = typer.Typer(
    name="founder",
    help="Founder profile (how the team addresses you) + rules (what the team may do).",
    no_args_is_help=True,
)
profile_app = typer.Typer(name="profile", help="Founder profile.", no_args_is_help=True)
rules_app = typer.Typer(name="rules", help="Founder rules (hard + soft).", no_args_is_help=True)
founder_app.add_typer(profile_app, name="profile")
founder_app.add_typer(rules_app, name="rules")

console = Console()


def _get_engine(config: str | None):
    from kompany.core.engine import KompanyEngine

    return KompanyEngine(config_path=config)


def _emit_json(data) -> None:
    console.print_json(data=data)


def _parse_json_arg(raw: str, what: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        console.print(f"[red]invalid JSON for {what}: {exc}[/red]")
        raise typer.Exit(1) from exc
    if not isinstance(data, dict):
        console.print(f"[red]{what} must be a JSON object[/red]")
        raise typer.Exit(1)
    return data


@profile_app.command("show")
def profile_show(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Show the founder profile."""
    profile = _get_engine(config).get_founder_profile()
    if as_json:
        _emit_json(profile)
        return
    if not profile:
        console.print(
            "[dim]No founder profile set. Set one with "
            "'kompany founder profile set --json '{\"address\": ...}''.[/dim]"
        )
        return
    lines = [f"{key}: {value}" for key, value in profile.items()]
    console.print(Panel("\n".join(lines), title="Founder profile"))


@profile_app.command("set")
def profile_set(
    payload_json: str = typer.Option(
        None, "--json",
        help='Profile fields as JSON, e.g. \'{"address": "Clare", "language": "zh"}\'. Partial payloads merge.',
    ),
    clear: bool = typer.Option(False, "--clear", help="Remove the founder profile."),
    config: str = typer.Option(None, "--config", "-c"),
):
    """Merge-set (or --clear) the founder profile."""
    if not clear and not payload_json:
        console.print("[red]Pass --json '{...}' (or --clear to remove the profile).[/red]")
        raise typer.Exit(1)
    payload = None if clear else _parse_json_arg(payload_json, "profile")
    try:
        result = _get_engine(config).set_founder_profile(payload)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    _emit_json(result)


@rules_app.command("show")
def rules_show(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Show the founder rules (hard + soft)."""
    rules = _get_engine(config).get_founder_rules()
    if as_json:
        _emit_json(rules)
        return
    if not rules:
        console.print(
            "[dim]No founder rules set. Set them with "
            "'kompany founder rules set --json '{\"hard\": [...], \"soft\": ...}''.[/dim]"
        )
        return
    table = Table(title="Hard rules (enforced)")
    table.add_column("Kind", style="cyan")
    table.add_column("Match", style="white")
    table.add_column("Action", style="magenta")
    for rule in rules.get("hard", []) or []:
        table.add_row(rule.get("kind", ""), rule.get("match", ""), rule.get("action", ""))
    console.print(table)
    soft = rules.get("soft") or ""
    if soft:
        console.print(Panel(soft, title="Soft preferences (best-effort)"))


@rules_app.command("set")
def rules_set(
    payload_json: str = typer.Option(
        None, "--json",
        help=(
            'Rules as JSON, e.g. \'{"hard": [{"kind": "exclude_capability", '
            '"match": "phone_call", "action": "skip"}], "soft": "prefer async"}\'. '
            "Top-level merge."
        ),
    ),
    clear: bool = typer.Option(False, "--clear", help="Remove all founder rules."),
    config: str = typer.Option(None, "--config", "-c"),
):
    """Merge-set (or --clear) the founder rules."""
    if not clear and not payload_json:
        console.print("[red]Pass --json '{...}' (or --clear to remove the rules).[/red]")
        raise typer.Exit(1)
    payload = None if clear else _parse_json_arg(payload_json, "rules")
    try:
        result = _get_engine(config).set_founder_rules(payload)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    _emit_json(result)
