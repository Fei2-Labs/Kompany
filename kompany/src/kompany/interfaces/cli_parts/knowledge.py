"""Retrospective, distillation, template and glossary commands.

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

@app.command("retrospective")
def retrospective(
    project_id: str = typer.Argument(..., help="Project id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Run or replay a CoS retrospective for a project."""
    engine = _get_engine(config)
    result = engine.run_retrospective(project_id)
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"Status: {result['status']}\n"
        f"Project: {result['project_id']}\n"
        f"Reflections: {len(result['reflections'])}",
        title="Retrospective",
    ))


def _parse_since(value: str | None) -> "timedelta | None":
    """Parse a human-readable ``--since`` flag into a ``timedelta``.

    Accepts ``30d`` / ``12h`` / ``45m`` / ``3600s`` plus bare integers
    interpreted as seconds. Returns ``None`` when the flag is omitted so
    the engine applies its default.
    """
    from datetime import timedelta

    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    if text[-1] in units:
        try:
            qty = float(text[:-1])
        except ValueError as exc:
            raise typer.BadParameter(
                f"Invalid --since value '{value}'. Expected e.g. 30d, 12h, 45m."
            ) from exc
        return timedelta(seconds=qty * units[text[-1]])
    try:
        return timedelta(seconds=float(text))
    except ValueError as exc:
        raise typer.BadParameter(
            f"Invalid --since value '{value}'. Expected e.g. 30d, 12h, 45m."
        ) from exc


@app.command("distill")
def distill(
    since: str = typer.Option(
        None,
        "--since",
        help="Lookback window (e.g. 30d, 12h, 45m). Defaults to 30d.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print patterns the LLM produced without writing to agent_memories.",
    ),
    episodes: str = typer.Option(
        None,
        "--episodes",
        help="Comma-separated project ids; bypasses --since and the 50-episode cap.",
    ),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """CoS reviews recent episodes and writes cross-project patterns.

    The Chief of Staff loads recent ``project_episodes`` payloads,
    extracts durable patterns (player preferences, recurring failures,
    cost surprises), and UPSERTs them into ``agent_memories`` as
    ``experiential`` rows keyed by ``(agent_role, pattern_key)``. Future
    directive runs surface these memories to the relevant agent via the
    existing memory-recall path.
    """
    engine = _get_engine(config)
    window = _parse_since(since)
    episode_ids = (
        [pid.strip() for pid in episodes.split(",") if pid.strip()]
        if episodes
        else None
    )
    try:
        result = engine.distill(
            since=window,
            dry_run=dry_run,
            episode_ids=episode_ids,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if as_json:
        _emit_json(result)
        return

    status = result.get("status")
    if status == "no_episodes":
        console.print(Panel(
            "No episodes in window — nothing to distil.",
            title="Distillation",
        ))
        return
    if status == "no_parseable_episodes":
        console.print(Panel(
            f"[yellow]All {result.get('episodes_in', 0)} selected episode(s) "
            f"had malformed payloads.[/yellow]",
            title="Distillation",
        ))
        return

    patterns = result.get("patterns", []) or []
    lines: list[str] = []
    for pattern in patterns:
        role = pattern["target_agent_role"]
        summary = pattern["pattern_summary"].strip()
        confidence = pattern.get("confidence", 0.0)
        lines.append(
            f"[{role}] ({confidence:.2f})  {summary}"
        )
    body = "\n".join(lines) if lines else "(no patterns extracted)"
    title_suffix = " — dry-run" if dry_run else ""
    console.print(Panel(
        f"CoS reviewed {result['episodes_in']} episode(s); "
        f"extracted {result['patterns_out']} pattern(s); "
        f"AI cost ${result['ai_cost']:.4f}\n\n"
        f"{body}\n",
        title=f"Distillation{title_suffix}",
    ))


template_app = typer.Typer(
    name="template",
    help="Browse and apply ready-to-play company templates.",
    no_args_is_help=True,
)
app.add_typer(template_app, name="template")


@template_app.command("list")
def template_list(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List available company templates."""
    engine = _get_engine(config)
    rows = engine.list_templates()
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print("[dim]No templates available.[/dim]")
        return
    table = Table(title="Company Templates")
    table.add_column("ID", style="cyan")
    table.add_column("Mission", style="white")
    table.add_column("Team", style="magenta")
    table.add_column("Init budget", style="green", justify="right")
    for row in rows:
        team_count = len(row.get("enabled_agents") or [])
        budget = float(row.get("initial_budget") or 0.0)
        table.add_row(
            row["id"],
            row["mission_title"],
            f"{team_count} agents",
            f"${budget:,.0f}",
        )
    console.print(table)


@template_app.command("show")
def template_show(
    template_id: str = typer.Argument(..., help="Template id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show one template's manifest and mission body."""
    engine = _get_engine(config)
    try:
        payload = engine.show_template(template_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(payload)
        return
    team = ", ".join(payload.get("enabled_agents") or [])
    directives = payload.get("suggested_directives") or []
    suggested = (
        "\n".join(f"  - {d}" for d in directives) if directives else "  (none)"
    )
    console.print(Panel(
        f"ID: {payload['id']}\n"
        f"Name: {payload['name']}\n"
        f"Mission: {payload['mission_title']}\n"
        f"Initial budget: ${float(payload['initial_budget']):,.0f}\n"
        f"Team ({len(payload.get('enabled_agents') or [])} agents): {team}\n"
        f"RPG theme: {payload.get('rpg_theme') or '(none)'}\n"
        f"Suggested directives:\n{suggested}\n\n"
        f"--- mission.md ---\n{payload.get('mission', '')}",
        title=f"Template: {payload['id']}",
    ))


@template_app.command("apply")
def template_apply(
    template_id: str = typer.Argument(..., help="Template id"),
    force: bool = typer.Option(False, "--force", help="Re-apply over an existing template"),
    budget: float = typer.Option(
        None,
        "--budget",
        help="Override the template's default initial budget.",
    ),
    directive: str = typer.Option(
        None,
        "--directive",
        help="Replace the suggested directives with a single custom directive.",
    ),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Apply a template — writes config, ledgers the initial budget, and
    stages suggested directives as draft projects."""
    engine = _get_engine(config)
    try:
        result = engine.apply_template(
            template_id,
            force=force,
            override_budget=budget,
            override_directive=directive,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    team_count = len(result.get("enabled_agents") or [])
    project_count = len(result.get("project_ids") or [])
    console.print(Panel(
        f"[green]Template '{result['template_id']}' applied.[/green]\n"
        f"Mission written ({len(result.get('mission') or '')} chars)\n"
        f"Team configured: {team_count} agents\n"
        f"Initial budget ledgered: ${float(result['initial_budget']):,.0f}\n"
        f"Draft projects staged: {project_count}\n"
        + (f"[yellow]Force mode: overwrote previous template.[/yellow]\n"
           if result.get("force") else ""),
        title="Kompany template apply",
    ))


glossary_app = typer.Typer(
    name="glossary",
    help="Manage the company glossary (canonical terms + forbidden synonyms).",
    no_args_is_help=True,
)
app.add_typer(glossary_app, name="glossary")


def _parse_forbid(value: str | None) -> list[str] | None:
    """Split a comma-separated ``--forbid`` value into a clean list.

    Returns ``None`` when the option wasn't supplied (so the engine
    treats it as "leave forbidden_synonyms unchanged" on update) and
    ``[]`` when an empty string was supplied (an explicit clear).
    """
    if value is None:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


@glossary_app.command("list")
def glossary_list_cmd(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List every glossary term."""
    engine = _get_engine(config)
    rows = engine.list_glossary()
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print("[dim]No glossary terms configured.[/dim]")
        return
    table = Table(title="Company Glossary")
    table.add_column("Term", style="cyan")
    table.add_column("Definition", style="white")
    table.add_column("Forbidden synonyms", style="magenta")
    table.add_column("Source", style="green")
    for row in rows:
        forbids = ", ".join(row.get("forbidden_synonyms") or []) or "—"
        table.add_row(
            row["term"],
            row.get("definition", ""),
            forbids,
            row.get("added_by", "founder"),
        )
    console.print(table)


@glossary_app.command("show")
def glossary_show_cmd(
    term: str = typer.Argument(..., help="Canonical term to look up"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show one glossary entry."""
    engine = _get_engine(config)
    entry = engine.get_glossary_term(term)
    if entry is None:
        console.print(f"[red]Term not found: {term!r}[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(entry)
        return
    forbids = ", ".join(entry.get("forbidden_synonyms") or []) or "(none)"
    console.print(Panel(
        f"Term: {entry['term']}\n"
        f"Definition: {entry['definition']}\n"
        f"Forbidden synonyms: {forbids}\n"
        f"Added by: {entry.get('added_by', 'founder')}\n"
        f"Added at: {entry.get('added_at', '')}",
        title=f"Glossary: {entry['term']}",
    ))


@glossary_app.command("add")
def glossary_add_cmd(
    term: str = typer.Argument(..., help="Canonical term"),
    definition: str = typer.Option(..., "--def", "--definition", help="Short definition"),
    forbid: str = typer.Option(
        None,
        "--forbid",
        help="Comma-separated list of forbidden synonyms (e.g. 'user,lead,prospect').",
    ),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Add a new glossary term (founder-sourced)."""
    engine = _get_engine(config)
    try:
        result = engine.add_glossary_term(
            term=term,
            definition=definition,
            forbidden_synonyms=_parse_forbid(forbid),
            added_by="founder",
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"[green]Added glossary term {result['term']!r}.[/green]\n"
        f"Definition: {result['definition']}\n"
        f"Forbidden synonyms: "
        f"{', '.join(result.get('forbidden_synonyms') or []) or '(none)'}",
        title="kompany glossary add",
    ))


@glossary_app.command("update")
def glossary_update_cmd(
    term: str = typer.Argument(..., help="Term to update"),
    definition: str = typer.Option(
        None, "--def", "--definition", help="New definition (omit to leave unchanged)"
    ),
    forbid: str = typer.Option(
        None,
        "--forbid",
        help="Comma-separated forbidden synonyms (pass '' to clear, omit to keep)",
    ),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Update an existing glossary term."""
    engine = _get_engine(config)
    try:
        result = engine.update_glossary_term(
            term=term,
            definition=definition,
            forbidden_synonyms=_parse_forbid(forbid),
        )
    except LookupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"[green]Updated glossary term {result['term']!r}.[/green]\n"
        f"Definition: {result['definition']}\n"
        f"Forbidden synonyms: "
        f"{', '.join(result.get('forbidden_synonyms') or []) or '(none)'}",
        title="kompany glossary update",
    ))


@glossary_app.command("remove")
def glossary_remove_cmd(
    term: str = typer.Argument(..., help="Term to drop"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Remove a glossary term."""
    engine = _get_engine(config)
    removed = engine.remove_glossary_term(term)
    if as_json:
        _emit_json({"removed": removed, "term": term})
        return
    if not removed:
        console.print(f"[yellow]Term not found: {term!r}[/yellow]")
        raise typer.Exit(1)
    console.print(f"[green]Removed glossary term {term!r}.[/green]")


