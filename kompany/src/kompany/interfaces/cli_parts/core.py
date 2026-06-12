"""Onboard, init, directive and CEO-channel commands.

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

@app.command()
def onboard(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Headless mode: skip all prompts, use defaults, error on missing API key.",
    ),
    provider: str = typer.Option(
        None,
        "--provider",
        help="LLM provider (anthropic | openai | gemini | glm | kimi | custom).",
    ),
    api_key: str = typer.Option(
        None,
        "--api-key",
        help="API key for the chosen provider (overrides env var).",
    ),
    template: str = typer.Option(
        None,
        "--template",
        help="Starter company template id (e.g. blank, saas-startup).",
    ),
    directive: str = typer.Option(
        None,
        "--directive",
        help="Optional first directive to run after setup completes.",
    ),
    data_dir: str = typer.Option(
        None,
        "--data-dir",
        help="Override the data directory (defaults to ~/.kompany).",
    ),
    budget: float = typer.Option(
        None,
        "--budget",
        help="Override the template's default initial_budget (USD).",
    ),
    revenue_target: float = typer.Option(
        None,
        "--revenue-target",
        help="Override the template's revenue target (USD).",
    ),
    customer_target: int = typer.Option(
        None,
        "--customer-target",
        help="Override the template's customer target (integer, optional).",
    ),
    deadline: str = typer.Option(
        None,
        "--deadline",
        help="ISO 8601 deadline (YYYY-MM-DD or full timestamp).",
    ),
):
    """One-line install: run the four-step onboarding wizard.

    With ``--yes`` plus an API key on the environment or via ``--api-key``,
    this command is fully headless and completes in under a minute. Without
    flags it walks you through provider selection, key entry (masked),
    template choice, and an optional first directive.
    """
    from pathlib import Path as _Path

    from kompany.installer import run_onboard

    result = run_onboard(
        yes=yes,
        provider=provider,
        api_key=api_key,
        template=template,
        directive=directive,
        data_dir=_Path(data_dir) if data_dir else None,
        initial_budget=budget,
        revenue_target=revenue_target,
        customer_target=customer_target,
        deadline=deadline,
        console=console,
    )
    if result.status == "cancelled":
        raise typer.Exit(1)


@app.command()
def init(
    name: str = typer.Option(..., prompt="Company name"),
    capital: float = typer.Option(0.0, prompt="Starting capital (EUR)"),
    goal: str = typer.Option("", prompt="Primary goal"),
    time_horizon: str = typer.Option("", "--time-horizon", prompt="Time horizon"),
    exclusions: str = typer.Option("", prompt="Exclusions (optional)"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Initialize a new Kompany."""
    engine = _get_engine()
    engine.initialize_company(
        name=name,
        capital=capital,
        goal=goal,
        time_horizon=time_horizon,
        exclusions=exclusions,
    )
    result = {
        "status": "initialized",
        "name": name,
        "capital": capital,
        "goal": goal,
        "time_horizon": time_horizon,
        "exclusions": exclusions,
        "stage": "solo",
    }
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"[green]Kompany '{name}' initialized.[/green]\n"
        f"Goal: {goal or '(none)'}\n"
        f"Time horizon: {time_horizon or '(none)'}\n"
        f"Exclusions: {exclusions or '(none)'}\n"
        f"Stage: solo\n"
        f"Capital: €{capital:.2f}",
        title="Kompany",
    ))


# Channel statuses that pause a session waiting for a founder turn. The
# interactive loop keeps prompting on these; one-shot mode prints + exits so
# a script can re-invoke with --session.
_CHANNEL_PAUSE_STATUSES = frozenset({"clarify", "gated"})


@app.command()
def directive(
    text: str = typer.Argument(..., help="Your directive in natural language"),
    config: str = typer.Option(None, "--config", "-c"),
    session: str = typer.Option(
        None,
        "--session",
        "-s",
        help="Continue an existing CEO-channel session (clarify reply / gated GO context).",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help="Stay in the session: answer the CEO's clarify questions and GO/abandon gates on stdin until it resolves.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Send a directive into the CEO channel.

    One-shot by default: prints the result and exits. If the CEO asks a
    clarify question or posts a spend gate, the result carries a session_id —
    re-invoke with ``--session <id>`` to continue (scripts), or use
    ``--interactive`` to answer inline on stdin until the session resolves.
    """
    engine = _get_engine(config)
    with console.status("[bold]CEO processing directive..."):
        result = engine.process_directive(text, session_id=session)

    if as_json:
        _emit_json(result.to_dict())
        return

    if interactive:
        _run_channel_interactive(engine, result)
        return

    _render_directive_result(result)
    # One-shot: tell a scripting founder how to continue a paused session.
    if result.status in _CHANNEL_PAUSE_STATUSES and result.session_id:
        verb = "GO/abandon" if result.status == "gated" else "reply"
        console.print(
            f"[dim]Session {result.session_id} awaits your {verb}. "
            f"Continue with:[/dim] kompany directive \"<reply>\" "
            f"--session {result.session_id}"
        )


def _render_directive_result(result) -> None:
    """Render one channel turn as a titled panel (CEO reply / question)."""
    title = f"Kompany [{result.status}]"
    if result.status == "clarify":
        title = "CEO asks"
    elif result.status == "gated":
        title = "CEO // spend gate"
    console.print(Panel(result.message, title=title))


def _run_channel_interactive(engine, result) -> None:
    """Drive a session to resolution: answer clarify questions and GO/abandon
    gates on stdin. The engine enforces the clarify cap, so this loop cannot
    run forever — at the cap the CEO returns a non-pausing status."""
    while True:
        _render_directive_result(result)
        sid = result.session_id
        if result.status not in _CHANNEL_PAUSE_STATUSES or not sid:
            return

        if result.status == "gated":
            while True:
                reply = typer.prompt("GO / abandon").strip().lower()
                if reply in {"go", "g", "yes", "y"}:
                    with console.status("[bold]Executing..."):
                        result = engine.channel_go(sid)
                    break
                if reply in {"abandon", "a", "no", "n"}:
                    result = engine.channel_abandon(sid)
                    break
                console.print("[dim]Type 'go' to execute or 'abandon' to cancel.[/dim]")
            continue

        # clarify: read the founder's reply, continue the same session.
        reply = typer.prompt("Your reply").strip()
        if not reply:
            console.print("[dim]Empty reply; abandoning session.[/dim]")
            result = engine.channel_abandon(sid)
            continue
        with console.status("[bold]CEO processing reply..."):
            result = engine.process_directive(reply, session_id=sid)


channel_app = typer.Typer(
    name="channel",
    help="CEO-channel conversation history (sessions / show).",
    no_args_is_help=True,
)
app.add_typer(channel_app)


@channel_app.command("sessions")
def channel_sessions(
    state: str = typer.Option(None, "--state", help="Filter by session state."),
    limit: int = typer.Option(50, "--limit", help="Max sessions to list."),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List CEO-channel sessions, newest first."""
    engine = _get_engine(config)
    capped = max(1, min(int(limit), 200))
    sessions = engine.channel.list_sessions(state=state, limit=capped)
    rows = [_cli_session_to_dict(s) for s in sessions]
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print("[dim]No channel sessions.[/dim]")
        return
    table = Table(title=f"CEO channel ({len(rows)} session(s))")
    table.add_column("Session", style="cyan")
    table.add_column("State")
    table.add_column("Route")
    table.add_column("Clarify", justify="right")
    table.add_column("Created", style="dim")
    for row in rows:
        table.add_row(
            row["session_id"],
            row["state"],
            row["route"] or "-",
            str(row["clarify_turns"]),
            row["created_at"] or "",
        )
    console.print(table)


@channel_app.command("show")
def channel_show(
    session_id: str = typer.Argument(..., help="Session id to show"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show one session plus its ordered turns (the full thread)."""
    engine = _get_engine(config)
    session = engine.channel.get_session(session_id)
    if session is None:
        console.print(f"[red]Unknown channel session {session_id!r}.[/red]")
        raise typer.Exit(1)
    turns = engine.channel.session_turns(session_id)
    payload = {
        "session": _cli_session_to_dict(session),
        "turns": [_cli_turn_to_dict(t) for t in turns],
    }
    if as_json:
        _emit_json(payload)
        return
    table = Table(title=f"Session {session_id} [{session.state.value}]")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Role", style="cyan")
    table.add_column("Kind")
    table.add_column("Content")
    table.add_column("Cost", justify="right", style="dim")
    for turn in turns:
        table.add_row(
            str(turn.turn_index),
            turn.role,
            turn.kind,
            turn.content[:120],
            f"{turn.cost:.4f}",
        )
    console.print(table)


def _cli_session_to_dict(session) -> dict:
    """Channel-session parity dict (matches REST/SDK/MCP serialization)."""
    return {
        "session_id": session.id,
        "state": session.state.value,
        "route": session.route,
        "clarify_turns": session.clarify_turns,
        "created_at": str(session.created_at) if session.created_at is not None else None,
        "closed_at": str(session.closed_at) if session.closed_at is not None else None,
        "run_id": session.run_id,
        "directive_id": session.directive_id,
        "project_id": session.project_id,
        "approval_id": session.approval_id,
    }


def _cli_turn_to_dict(turn) -> dict:
    """Channel-turn parity dict (matches REST/SDK/MCP serialization)."""
    return {
        "turn_index": turn.turn_index,
        "role": turn.role,
        "content": turn.content,
        "kind": turn.kind,
        "cost": turn.cost,
        "run_id": turn.run_id,
        "directive_id": turn.directive_id,
        "created_at": str(turn.created_at) if turn.created_at is not None else None,
    }


