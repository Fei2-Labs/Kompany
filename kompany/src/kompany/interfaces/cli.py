"""Kompany CLI — the primary interface."""

from __future__ import annotations

import time

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from kompany.core.debate import DebateEngine

app = typer.Typer(
    name="kompany",
    help="Autonomous business operating system for solo founders.",
    no_args_is_help=True,
)
console = Console()


def _get_engine(config: str | None = None):
    from kompany.core.engine import KompanyEngine
    return KompanyEngine(config_path=config)


def _emit_json(data):
    console.print_json(data=data)


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


@app.command("decision-packet")
def decision_packet(
    text: str = typer.Argument(..., help="Directive to prepare"),
    target_amount: float = typer.Option(None, "--target-amount"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Prepare a full decision-chain packet without executing it."""
    engine = _get_engine(config)
    payload = engine.prepare_decision_packet(text, target_amount=target_amount)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"Status: {payload['status']}\n"
        f"Approval ID: {payload['approval_id']}\n\n"
        f"Recommendation: {payload['synthesis']['recommendation']}",
        title="Decision Chain Packet",
    ))


@app.command()
def override(
    text: str = typer.Argument(..., help="Override request in natural language"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Request an override with a risk briefing before execution."""
    engine = _get_engine(config)
    payload = engine.process_override(text)
    if as_json:
        _emit_json(payload)
        return
    briefing = payload["briefing"]
    risks = "\n".join(f"- {risk}" for risk in briefing["risks"])
    console.print(Panel(
        f"{briefing['summary']}\n\nRisks:\n{risks}\n\n"
        f"Approval ID: {payload['approval_id']}",
        title="Override Risk Briefing",
    ))


@app.command("observability")
def observability(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show operational observability and RPG office status."""
    engine = _get_engine(config)
    payload = engine.observability_snapshot()
    if as_json:
        _emit_json(payload)
        return
    company = payload["company"]
    finance = payload["finance"]
    approvals = payload["approvals"]
    projects = payload["projects"]
    agents = payload["agents"]
    console.print(Panel(
        f"Company: {company.get('name') or '(not initialized)'}\n"
        f"Goal: {company.get('goal') or '(none)'}\n"
        f"Runtime: {payload['runtime']['state']}\n"
        f"Balance: €{finance['balance']:.2f}\n"
        f"Pending approvals: {approvals['pending']}\n"
        f"Active projects: {projects['active']}\n"
        f"Active agents: {agents['active']} / {agents['total']}",
        title="Kompany Observability",
    ))
    room_table = Table(title="RPG Office")
    room_table.add_column("Room", style="cyan")
    room_table.add_column("Characters")
    room_table.add_column("Purpose")
    for room in payload["office"]["rooms"]:
        characters = ", ".join(
            f"{c['role']}({c['status']})" for c in room["characters"]
        )
        room_table.add_row(room["name"], characters, room["purpose"])
    console.print(room_table)


@app.command("remote-command")
def remote_command(
    text: str = typer.Argument(..., help="Remote command text"),
    source: str = typer.Option("mobile", "--source"),
    chat_id: str = typer.Option("", "--chat-id"),
    bearer_token: str = typer.Option("", "--bearer-token"),
    nonce: str = typer.Option("", "--nonce"),
    request_id: str = typer.Option("", "--request-id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Handle an authenticated inbound remote command."""
    engine = _get_engine(config)
    replay_payload = {}
    if nonce:
        replay_payload["nonce"] = nonce
    if request_id:
        replay_payload["request_id"] = request_id
    payload = engine.handle_remote_command({
        "source": source,
        "text": text,
        "chat_id": chat_id,
        "bearer_token": bearer_token,
        "payload": replay_payload,
    })
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"Status: {payload['status']}\n"
        f"Command: {payload.get('command') or '-'}\n"
        f"Message: {payload.get('message') or '-'}",
        title="Remote Command",
    ))


@app.command("remote-replay-cleanup")
def remote_replay_cleanup(
    ttl_seconds: int | None = typer.Option(None, "--ttl-seconds"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Delete expired remote replay records."""
    engine = _get_engine(config)
    payload = engine.cleanup_remote_replays(ttl_seconds=ttl_seconds)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"Deleted: {payload['deleted']}\n"
        f"Remaining: {payload['remaining']}\n"
        f"TTL seconds: {payload['ttl_seconds']}",
        title="Remote Replay Cleanup",
    ))


@app.command("dashboard")
def dashboard(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Alias for the observability view."""
    observability(config=config, as_json=as_json)


@app.command("web")
def web(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show how to launch the browser dashboard."""
    payload = {
        "status": "ready",
        "command": f"uvicorn kompany.interfaces.api:app --host {host} --port {port}",
        "url": f"http://{host}:{port}/dashboard",
        "auth": "set WEB_DASHBOARD_TOKEN, open /dashboard, and enter it in the login form; API clients may use a Bearer token",
    }
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"Run: {payload['command']}\n"
        f"Open: {payload['url']}\n"
        "Auth: set WEB_DASHBOARD_TOKEN, then enter it in the dashboard login form. "
        "API clients may use a Bearer token.",
        title="Kompany Web Dashboard",
    ))


@app.command()
def status(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show company status."""
    from kompany.core.status_ops import build_status

    engine = _get_engine(config)
    payload = build_status(engine)
    if as_json:
        _emit_json(payload)
        return

    ticker = payload["ticker"]
    ticker_line = (
        f"running (every {ticker['interval_seconds']}s, "
        f"{ticker['tick_count']} ticks, last {ticker['last_tick_at'] or '—'})"
        if ticker["running"]
        else "stopped"
    )
    table = Table(title="Kompany Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Company", payload["company"] or "(not initialized)")
    table.add_row("Goal", payload["goal"] or "(none)")
    table.add_row("Time Horizon", payload["time_horizon"] or "(none)")
    table.add_row("Exclusions", payload["exclusions"] or "(none)")
    table.add_row("Stage", payload["stage"] or "solo")
    table.add_row("Balance", f"€{payload['balance']:.2f}")
    table.add_row("Total Income", f"€{payload['total_income']:.2f}")
    table.add_row("Total Expenses", f"€{payload['total_expenses']:.2f}")
    table.add_row("AI Costs", f"${payload['total_ai_costs']:.4f}")
    table.add_row("Active Projects", str(payload["active_projects"]))
    table.add_row("Ticker", ticker_line)
    console.print(table)


@app.command()
def agents(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Per-agent work summary from task history (delivered/completed/failed)."""
    engine = _get_engine(config)
    payload = engine.agent_work_summary()
    if as_json:
        _emit_json(payload)
        return

    if not payload:
        console.print("[dim]No recorded agent work yet.[/dim]")
        return

    table = Table(title="Agent Work Summary")
    table.add_column("Agent", style="cyan")
    table.add_column("Delivered", style="green")
    table.add_column("Completed", style="green")
    table.add_column("Failed", style="red")
    table.add_column("Total")
    table.add_column("Last Active", style="dim")
    for role in sorted(payload):
        row = payload[role]
        table.add_row(
            role.upper(),
            str(row["delivered"]), str(row["completed"]),
            str(row["failed"]), str(row["total"]),
            row["last_active"] or "—",
        )
    console.print(table)


@app.command()
def projects(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List active projects."""
    engine = _get_engine(config)
    active = engine.projects.list_active()
    payload = [
        {
            "id": p.id,
            "name": p.name,
            "type": p.type.value,
            "status": p.status.value,
            "target_amount": p.target_amount,
            "funded_amount": p.funded_amount,
        }
        for p in active
    ]
    if as_json:
        _emit_json(payload)
        return

    if not active:
        console.print("[dim]No active projects.[/dim]")
        return

    table = Table(title="Active Projects")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Type", style="yellow")
    table.add_column("Progress", style="green")

    for p in active:
        target = p.target_amount or 0
        progress = f"€{p.funded_amount:.0f}/€{target:.0f}" if target else "—"
        table.add_row(p.id, p.name, p.type.value, progress)

    console.print(table)


@app.command()
def fund(
    project_id: str = typer.Argument(..., help="Project to fund"),
    amount: float = typer.Argument(..., help="Amount (EUR) to earmark from treasury"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Earmark treasury into a project's budget envelope."""
    engine = _get_engine(config)
    try:
        budget = engine.fund_project(project_id, amount)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(budget)
        return
    console.print(Panel(
        f"Funded €{amount:.2f} → {budget['name']}\n"
        f"Envelope: €{budget['spent']:.2f} spent / €{budget['funded']:.2f} funded "
        f"(€{budget['remaining']:.2f} remaining)",
        title="Project funding",
    ))


@app.command()
def spend(
    project_id: str = typer.Argument(..., help="Project charged for this expense"),
    amount: float = typer.Argument(..., help="Expense amount (EUR)"),
    description: str = typer.Argument(..., help="What the money bought"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Record a real expense against a project's envelope (gated)."""
    engine = _get_engine(config)
    try:
        budget = engine.record_project_expense(project_id, amount, description)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(budget)
        return
    console.print(Panel(
        f"Spent €{amount:.2f} — {description}\n"
        f"Envelope: €{budget['spent']:.2f} spent / €{budget['funded']:.2f} funded "
        f"(€{budget['remaining']:.2f} remaining)",
        title="Project expense",
    ))


@app.command()
def budgets(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Per-project envelopes plus consolidated company treasury."""
    engine = _get_engine(config)
    active = engine.projects.list_active()
    rows = [engine.project_budget(p.id) for p in active]
    balance = engine.ledger.get_balance()
    free = engine.unallocated_treasury()
    if as_json:
        _emit_json({
            "balance": balance,
            "unallocated": free,
            "projects": rows,
        })
        return
    table = Table(title="Project Budgets")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Funded", justify="right")
    table.add_column("Spent", justify="right")
    table.add_column("Remaining", justify="right", style="green")
    for b in rows:
        table.add_row(
            b["project_id"], b["name"],
            f"€{b['funded']:.2f}", f"€{b['spent']:.2f}", f"€{b['remaining']:.2f}",
        )
    console.print(table)
    console.print(
        f"Company balance: €{balance:.2f}  |  "
        f"unallocated: €{free:.2f}  |  "
        f"earmarked: €{balance - free:.2f}"
    )


@app.command()
def debate(
    question: str = typer.Argument(..., help="Strategic question to debate"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Run a full multi-agent debate on a strategic question."""
    engine = _get_engine(config)

    stage = engine.settings.company_stage or "solo"
    with console.status(f"[bold]Running {stage}-stage debate..."):
        debate_engine = DebateEngine(engine.registry, stage=stage)
        result = debate_engine.run(
            question=question,
            company_state=engine.get_company_state(),
        )

    payload = {
        "question": result.question,
        "rounds": [[pos.model_dump() for pos in rnd] for rnd in result.rounds],
        "synthesis": result.synthesis.model_dump() if result.synthesis else None,
        "decision": result.decision.model_dump() if result.decision else None,
    }
    if as_json:
        _emit_json(payload)
        return

    for i, rnd in enumerate(result.rounds, 1):
        console.print(f"\n[bold cyan]--- Round {i} ---[/bold cyan]")
        for pos in rnd:
            console.print(Panel(
                f"[bold]{pos.recommendation}[/bold]\n\n"
                f"{pos.analysis}\n\n"
                f"Confidence: {pos.confidence}",
                title=f"{pos.agent_name} ({pos.squad})",
            ))

    if result.synthesis:
        s = result.synthesis
        console.print(Panel(
            f"Consensus: {s.consensus_position}\n\n"
            f"Recommended: {s.recommended_option}\n\n"
            f"Tensions: {', '.join(s.key_tensions)}\n"
            f"Risks: {', '.join(s.risk_flags)}\n\n"
            f"Decision required: {s.decision_required}",
            title="[bold]CoS Synthesis[/bold]",
        ))

    if result.decision:
        d = result.decision
        steps = "\n".join(f"  - {s}" for s in d.next_steps)
        console.print(Panel(
            f"[bold green]{d.decision}[/bold green]\n\n"
            f"Rationale: {d.rationale}\n\n"
            f"Confidence: {d.confidence_score:.0%} | "
            f"Reversibility: {d.reversibility}\n\n"
            f"Next steps:\n{steps}",
            title="[bold]CEO Decision[/bold]",
        ))


@app.command()
def ledger(
    limit: int = typer.Option(10, "--limit", "-n"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show recent ledger entries."""
    engine = _get_engine(config)
    entries = engine.ledger.get_recent(limit=limit)
    if as_json:
        _emit_json(entries)
        return

    if not entries:
        console.print("[dim]No ledger entries.[/dim]")
        return

    table = Table(title="Recent Ledger Entries")
    table.add_column("Time", style="dim")
    table.add_column("Amount", style="green")
    table.add_column("Balance", style="cyan")
    table.add_column("Category", style="yellow")
    table.add_column("Description")

    for e in entries:
        amt = e["amount"]
        color = "green" if amt >= 0 else "red"
        table.add_row(
            e["timestamp"][:16],
            f"[{color}]€{amt:+.4f}[/{color}]",
            f"€{e['balance_after']:.4f}",
            e["category"],
            e["description"][:50],
        )

    console.print(table)


@app.command()
def project(
    project_id: str = typer.Argument(..., help="Project ID to inspect"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show details for a specific project."""
    engine = _get_engine(config)
    p = engine.projects.get(project_id)

    if not p:
        console.print(f"[red]Project '{project_id}' not found.[/red]")
        raise typer.Exit(1)

    tasks = engine.projects.list_tasks(p.id)
    payload = {
        "id": p.id,
        "name": p.name,
        "type": p.type.value,
        "status": p.status.value,
        "target_amount": p.target_amount,
        "funded_amount": p.funded_amount,
        "plan": p.plan,
        "assigned_agents": p.assigned_agents,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "agent": t.assigned_agent,
                "status": t.status.value,
            }
            for t in tasks
        ],
    }
    if as_json:
        _emit_json(payload)
        return

    target = p.target_amount or 0
    pct = (p.funded_amount / target * 100) if target else 0

    console.print(Panel(
        f"Name: {p.name}\n"
        f"Type: {p.type.value}\n"
        f"Status: {p.status.value}\n"
        f"Progress: €{p.funded_amount:.2f} / €{target:.2f} ({pct:.0f}%)\n"
        f"Agents: {', '.join(p.assigned_agents)}",
        title=f"Project {p.id}",
    ))

    if tasks:
        table = Table(title="Tasks")
        table.add_column("ID", style="cyan")
        table.add_column("Title")
        table.add_column("Agent", style="yellow")
        table.add_column("Status", style="green")
        for t in tasks:
            table.add_row(t.id, t.title, t.assigned_agent, t.status.value)
        console.print(table)

    if p.plan:
        paths = p.plan.get("paths", [])
        if paths:
            ptable = Table(title="Revenue Paths")
            ptable.add_column("Path")
            ptable.add_column("Revenue", style="green")
            ptable.add_column("Timeframe")
            ptable.add_column("Risk", style="yellow")
            for path in paths:
                ptable.add_row(
                    path.get("name", ""),
                    f"€{path.get('estimated_revenue_eur', 0):.0f}",
                    path.get("timeframe", ""),
                    path.get("risk_level", ""),
                )
            console.print(ptable)


@app.command()
def approvals(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List pending approval requests."""
    engine = _get_engine(config)
    payload = engine.list_approvals()
    if as_json:
        _emit_json(payload)
        return

    if not payload:
        console.print("[dim]No pending approvals.[/dim]")
        return

    table = Table(title="Pending Approvals")
    table.add_column("ID", style="cyan")
    table.add_column("Action")
    table.add_column("Summary")
    table.add_column("Created", style="dim")
    for request in payload:
        table.add_row(
            request["id"],
            request["action_type"],
            request["summary"],
            request["created_at"],
        )
    console.print(table)


@app.command()
def approve(
    approval_id: str = typer.Argument(..., help="Approval request ID"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Approve a pending request."""
    engine = _get_engine(config)
    payload = engine.approve_request(approval_id)
    if not payload:
        console.print(f"[red]Approval '{approval_id}' not found.[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(f"Approved request {approval_id}", title="Approval"))


@app.command()
def reject(
    approval_id: str = typer.Argument(..., help="Approval request ID"),
    reason: str = typer.Option("", "--reason", "-r"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Reject a pending request."""
    engine = _get_engine(config)
    payload = engine.reject_request(approval_id, reason=reason)
    if not payload:
        console.print(f"[red]Approval '{approval_id}' not found.[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(f"Rejected request {approval_id}", title="Approval"))


# ---------------------------------------------------------------------------
# Approval thread + RPG inbox (05-18-approval-thread-and-rpg)
# ---------------------------------------------------------------------------


approval_app = typer.Typer(
    name="approval",
    help="Approval thread actions (show / approve / reject / revise / snooze / cancel / comment).",
    no_args_is_help=True,
)
app.add_typer(approval_app)


@app.command("inbox")
def inbox(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show the player's RPG inbox: pending + snoozed approvals."""
    engine = _get_engine(config)
    rows = engine.inbox()
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print("[dim]Inbox empty.[/dim]")
        return
    table = Table(title=f"Inbox ({len(rows)} item(s))")
    table.add_column("ID", style="cyan")
    table.add_column("Severity")
    table.add_column("Status")
    table.add_column("Action")
    table.add_column("Summary")
    table.add_column("Comments", style="dim")
    table.add_column("Created", style="dim")
    for row in rows:
        table.add_row(
            row["id"],
            row.get("severity", "medium"),
            row.get("status", "pending"),
            row["action_type"],
            row["summary"][:80],
            str(row.get("comment_count", 0)),
            row.get("created_at", ""),
        )
    console.print(table)


@approval_app.command("show")
def approval_show(
    approval_id: str = typer.Argument(..., help="Approval request ID"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Show one approval, its thread, and its comment timeline."""
    engine = _get_engine(config)
    data = engine.get_approval(approval_id)
    if data is None:
        console.print(f"[red]Approval '{approval_id}' not found.[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(data)
        return
    console.print(Panel(
        f"Status: {data['status']}  |  Severity: {data.get('severity', 'medium')}\n"
        f"Action: {data['action_type']}\n"
        f"Summary: {data['summary']}\n"
        f"Created: {data.get('created_at', '')}",
        title=f"Approval {approval_id}",
    ))
    if data["thread"] and len(data["thread"]) > 1:
        chain_tbl = Table(title="Revision chain")
        chain_tbl.add_column("ID")
        chain_tbl.add_column("Status")
        chain_tbl.add_column("Predecessor", style="dim")
        for entry in data["thread"]:
            chain_tbl.add_row(
                entry["id"],
                entry["status"],
                entry.get("predecessor_id") or "-",
            )
        console.print(chain_tbl)
    if data["comments"]:
        ct = Table(title="Comments")
        ct.add_column("At", style="dim")
        ct.add_column("By")
        ct.add_column("Body")
        for c in data["comments"]:
            by = c["by_type"] + (f":{c['by_id']}" if c.get("by_id") else "")
            ct.add_row(c.get("created_at", ""), by, c["body"])
        console.print(ct)


@approval_app.command("approve")
def approval_approve(
    approval_id: str = typer.Argument(..., help="Approval request ID"),
    comment: str = typer.Option("", "--comment"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Approve an approval, optionally with a comment."""
    engine = _get_engine(config)
    payload = engine.approve_request(approval_id, comment_body=comment or None)
    if not payload:
        console.print(f"[red]Approval '{approval_id}' not found.[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(f"Approved {approval_id}", title="Approval"))


@approval_app.command("reject")
def approval_reject(
    approval_id: str = typer.Argument(..., help="Approval request ID"),
    reason: str = typer.Option(..., "--reason", help="Rejection reason"),
    comment: str = typer.Option("", "--comment"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Reject an approval with a required reason."""
    engine = _get_engine(config)
    payload = engine.reject_request(
        approval_id, reason=reason, comment_body=comment or None
    )
    if not payload:
        console.print(f"[red]Approval '{approval_id}' not found.[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(f"Rejected {approval_id}: {reason}", title="Approval"))


@approval_app.command("revise")
def approval_revise(
    approval_id: str = typer.Argument(..., help="Approval request ID"),
    counter: str = typer.Option(..., "--counter", help="Counter-proposal text"),
    comment: str = typer.Option("", "--comment"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Counter-propose: original goes to ``revision_requested`` and a new
    pending approval is spawned with ``payload['revision_hint']``."""
    engine = _get_engine(config)
    payload = engine.request_approval_revision(
        approval_id,
        counter=counter,
        comment_body=comment or None,
    )
    if not payload:
        console.print(f"[red]Approval '{approval_id}' not found.[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(payload)
        return
    successor = payload["successor"]
    console.print(Panel(
        f"Original {approval_id} -> revision_requested\n"
        f"New approval {successor['id']} created with hint:\n"
        f"  {counter}",
        title="Revise",
    ))


@approval_app.command("snooze")
def approval_snooze(
    approval_id: str = typer.Argument(..., help="Approval request ID"),
    minutes: int = typer.Option(..., "--minutes", help="Snooze duration in minutes"),
    comment: str = typer.Option("", "--comment"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Snooze an approval; the watchdog will auto-unsnooze when due."""
    engine = _get_engine(config)
    payload = engine.snooze_approval(
        approval_id, minutes=minutes, comment_body=comment or None
    )
    if not payload:
        console.print(f"[red]Approval '{approval_id}' not found.[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"Snoozed {approval_id} for {minutes}m (until {payload.get('snoozed_until')})",
        title="Snooze",
    ))


@approval_app.command("cancel")
def approval_cancel(
    approval_id: str = typer.Argument(..., help="Approval request ID"),
    reason: str = typer.Option(..., "--reason"),
    comment: str = typer.Option("", "--comment"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Cancel an approval (terminal — player withdraws the question)."""
    engine = _get_engine(config)
    payload = engine.cancel_approval(
        approval_id, reason=reason, comment_body=comment or None
    )
    if not payload:
        console.print(f"[red]Approval '{approval_id}' not found.[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(f"Cancelled {approval_id}: {reason}", title="Cancel"))


@approval_app.command("comment")
def approval_comment(
    approval_id: str = typer.Argument(..., help="Approval request ID"),
    body: str = typer.Option(..., "--body"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Append a free-form comment to an approval thread."""
    engine = _get_engine(config)
    payload = engine.comment_on_approval(approval_id, body=body)
    if not payload:
        console.print(f"[red]Approval '{approval_id}' not found.[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(f"Comment added on {approval_id}", title="Comment"))


@app.command("runtime")
def runtime(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show engine runtime state."""
    engine = _get_engine(config)
    payload = engine.get_runtime_state()
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"State: {payload['state']}\n"
        f"Reason: {payload.get('reason') or '-'}\n"
        f"Since: {payload.get('since') or '-'}",
        title="Runtime",
    ))


@app.command("heartbeat")
def heartbeat(
    dispatch_notifications: bool = typer.Option(False, "--dispatch-notifications"),
    adapter: str = typer.Option("dry-run", "--adapter"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Run one heartbeat check."""
    engine = _get_engine(config)
    payload = engine.heartbeat_once(dispatch=dispatch_notifications, adapter=adapter)
    if as_json:
        _emit_json(payload)
        return
    notes = "\n".join(f"- {n['summary']}" for n in payload["notifications"])
    console.print(Panel(
        f"Runtime: {payload['runtime']['state']}\n"
        f"Pending approvals: {payload['pending_approvals']}\n"
        f"Active projects: {payload['active_projects']}\n"
        f"Notifications:\n{notes or '-'}",
        title="Heartbeat",
    ))


@app.command("heartbeat-loop")
def heartbeat_loop(
    interval: float = typer.Option(30.0, "--interval", min=0.0),
    once: bool = typer.Option(False, "--once"),
    max_ticks: int | None = typer.Option(None, "--max-ticks"),
    dispatch_notifications: bool = typer.Option(False, "--dispatch-notifications"),
    adapter: str = typer.Option("dry-run", "--adapter"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Run the heartbeat loop."""
    engine = _get_engine(config)
    ticks = 1 if once else max_ticks
    completed = 0
    while ticks is None or completed < ticks:
        payload = engine.heartbeat_once(
            dispatch=dispatch_notifications,
            adapter=adapter,
        )
        completed += 1
        if as_json:
            _emit_json(payload)
        else:
            console.print(Panel(
                f"Runtime: {payload['runtime']['state']}\n"
                f"Notifications: {len(payload['notifications'])}",
                title=f"Heartbeat #{completed}",
            ))
        if ticks is not None and completed >= ticks:
            break
        time.sleep(interval)


@app.command("tool-policies")
def tool_policies(
    agent_role: str | None = typer.Option(None, "--agent-role"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List tool authorization policies."""
    engine = _get_engine(config)
    payload = engine.list_tool_policies(agent_role=agent_role)
    if as_json:
        _emit_json(payload)
        return
    table = Table(title="Tool Authorization Policies")
    table.add_column("Agent", style="cyan")
    table.add_column("Tool")
    table.add_column("Allowed", style="green")
    table.add_column("Requires Approval", style="yellow")
    table.add_column("Reason")
    for policy in payload:
        table.add_row(
            policy["agent_role"],
            policy["tool_name"],
            str(policy["allowed"]),
            str(policy.get("requires_approval", False)),
            policy.get("reason") or "",
        )
    console.print(table)


@app.command("set-tool-policy")
def set_tool_policy(
    agent_role: str = typer.Argument(...),
    tool_name: str = typer.Argument(...),
    allowed: bool = typer.Option(..., "--allowed/--denied"),
    requires_approval: bool = typer.Option(False, "--requires-approval"),
    reason: str = typer.Option("", "--reason"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Create or update a tool authorization policy."""
    engine = _get_engine(config)
    payload = engine.set_tool_policy(
        agent_role,
        tool_name,
        allowed,
        reason=reason,
        requires_approval=requires_approval,
    )
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"{agent_role} -> {tool_name}: {payload['allowed']}\n"
        f"Requires approval: {payload.get('requires_approval', False)}",
        title="Tool Policy Updated",
    ))


@app.command("authorize-tool")
def authorize_tool(
    agent_role: str = typer.Argument(...),
    tool_name: str = typer.Argument(...),
    purpose: str = typer.Option("", "--purpose"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Check whether an agent may use a tool."""
    engine = _get_engine(config)
    payload = engine.authorize_tool(agent_role, tool_name, purpose=purpose)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"Status: {payload['status']}\nReason: {payload.get('reason') or '-'}",
        title="Tool Authorization",
    ))


@app.command("use-tool")
def use_tool(
    agent_role: str = typer.Argument(...),
    tool_name: str = typer.Argument(...),
    purpose: str = typer.Option("", "--purpose"),
    approval_id: str | None = typer.Option(None, "--approval-id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Authorize a tool use through the engine gate."""
    engine = _get_engine(config)
    payload = engine.use_tool(
        agent_role,
        tool_name,
        purpose=purpose,
        approval_id=approval_id,
    )
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"Status: {payload['status']}\n"
        f"Reason: {payload.get('reason') or '-'}\n"
        f"Approval ID: {payload.get('approval_id') or '-'}",
        title="Tool Use",
    ))


@app.command("suspend")
def suspend(
    reason: str = typer.Option("manual", "--reason", "-r"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Suspend the engine."""
    engine = _get_engine(config)
    payload = engine.suspend(reason=reason)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"State: {payload['state']}\nReason: {payload.get('reason') or '-'}",
        title="Suspend",
    ))


@app.command("resume")
def resume(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Resume the engine."""
    engine = _get_engine(config)
    payload = engine.resume()
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(f"State: {payload['state']}", title="Resume"))


@app.command("credentials")
def credentials(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List configured encrypted credential names."""
    engine = _get_engine(config)
    payload = engine.list_credentials()
    if as_json:
        _emit_json(payload)
        return
    if not payload:
        console.print("[dim]No credentials configured.[/dim]")
        return
    table = Table(title="Credential Vault")
    table.add_column("Name", style="cyan")
    table.add_column("Configured", style="green")
    table.add_column("Updated", style="dim")
    for item in payload:
        table.add_row(
            item["name"],
            str(item.get("configured", True)),
            (item.get("updated_at") or "")[:19],
        )
    console.print(table)


@app.command("credential-set")
def credential_set(
    name: str = typer.Argument(...),
    value: str = typer.Option(..., "--value", prompt=True, hide_input=True),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Set an encrypted credential value."""
    engine = _get_engine(config)
    payload = engine.set_credential(name, value)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(f"Credential configured: {payload['name']}", title="Credential Vault"))


@app.command("credential-delete")
def credential_delete(
    name: str = typer.Argument(...),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Delete an encrypted credential value."""
    engine = _get_engine(config)
    payload = engine.delete_credential(name)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"Credential deleted: {payload['name']} ({payload['deleted']})",
        title="Credential Vault",
    ))


@app.command("credential-rotate-key")
def credential_rotate_key(
    new_vault_key: str = typer.Option(..., "--new-vault-key", prompt=True, hide_input=True),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Re-encrypt credential vault entries with a new vault key."""
    engine = _get_engine(config)
    payload = engine.rotate_credential_key(new_vault_key)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"Rotated credentials: {payload['rotated']}",
        title="Credential Vault",
    ))


@app.command("backup")
def backup(
    label: str = typer.Option("manual", "--label", "-l"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Create a labeled SQLite snapshot."""
    engine = _get_engine(config)
    result = engine.create_backup(label=label)
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"ID: {result['id']}\nLabel: {result['label']}\n"
        f"Path: {result['path']}\nSize: {result['size_bytes']} bytes",
        title="Backup",
    ))


@app.command("backups")
def backups(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List SQLite snapshots, newest first."""
    engine = _get_engine(config)
    payload = engine.list_backups()
    if as_json:
        _emit_json(payload)
        return
    if not payload:
        console.print("[dim]No backups.[/dim]")
        return
    table = Table(title="Backups")
    table.add_column("ID", style="cyan")
    table.add_column("Kind", style="yellow")
    table.add_column("Label")
    table.add_column("Created", style="dim")
    table.add_column("Size", style="green")
    for r in payload:
        table.add_row(
            r["id"], r.get("kind", ""), r.get("label", ""),
            r.get("created_at", "")[:19], str(r.get("size_bytes", 0)),
        )
    console.print(table)


@app.command("restore")
def restore(
    backup_id: str = typer.Argument(..., help="Backup id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Restore a SQLite snapshot. Auto-creates a pre-restore backup."""
    engine = _get_engine(config)
    try:
        result = engine.restore_backup(backup_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"Restored: {result['id']}\n"
        f"Auto pre-restore: {result['auto_pre_restore_id']}\n"
        f"Restored at: {result['restored_at']}",
        title="Restore",
    ))


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


# Daemon sub-app (06-12-daemon-tick-loop PR2). Lives in cli_daemon.py —
# cli.py is over the file-size cap, new command groups go in siblings.
from kompany.interfaces.cli_anima import anima_app  # noqa: E402
from kompany.interfaces.cli_daemon import daemon_app  # noqa: E402
from kompany.interfaces.cli_self_update import self_update_app  # noqa: E402

app.add_typer(anima_app, name="anima")
app.add_typer(daemon_app, name="daemon")
app.add_typer(self_update_app, name="self-update")


target_app = typer.Typer(
    name="target",
    help="Inspect or refresh the company's agreed targets.",
    no_args_is_help=True,
)
app.add_typer(target_app, name="target")


@target_app.command("show")
def target_show(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show the founder / team_proposal / agreed targets trio.

    Mission-targets task (05-19). ``agreed`` is the authoritative version
    every agent + the watchdog read.
    """
    engine = _get_engine(config)
    bundle = engine.get_targets_bundle()
    payload = {
        "founder": bundle.founder.model_dump(mode="json"),
        "proposal": (
            bundle.proposal.model_dump(mode="json")
            if bundle.proposal is not None
            else None
        ),
        "agreed": (
            bundle.agreed.model_dump(mode="json")
            if bundle.agreed is not None
            else None
        ),
        "review_thread_id": bundle.review_thread_id,
    }
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"founder:   {payload['founder']}\n"
        f"proposal:  {payload['proposal']}\n"
        f"agreed:    {payload['agreed']}\n"
        f"review:    {payload['review_thread_id'] or '(no open review)'}",
        title="Company targets",
    ))


@target_app.command("review")
def target_review(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Re-run the team feasibility review against the current founder targets.

    Creates a fresh ``approval_request(action_type='target_feasibility')``
    so the founder can revise / approve / reject the recommendation. The
    review thread id is mirrored to ``company_config['targets.review_thread_id']``
    and surfaced in ``kompany target show``.
    """
    engine = _get_engine(config)
    payload = engine.run_target_feasibility_review()
    if payload is None:
        console.print(
            "[yellow]No founder targets are set yet — "
            "run `kompany onboard` first.[/yellow]"
        )
        raise typer.Exit(1)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"Review id: {payload.get('id')}\n"
        f"Status: {payload.get('status')}\n"
        f"Summary: {payload.get('summary')}",
        title="Target feasibility review",
    ))


episodes_app = typer.Typer(
    name="episodes",
    help="Browse and rebuild project-episode records (self-learning).",
    no_args_is_help=True,
)
app.add_typer(episodes_app, name="episodes")


health_app = typer.Typer(
    name="health",
    help="Browse and resolve watchdog health events (resilience).",
    no_args_is_help=True,
)
app.add_typer(health_app, name="health")


@health_app.command("list")
def health_list(
    status: str = typer.Option(
        None,
        "--status",
        help="Filter by status (open | resolved | snoozed | dismissed).",
    ),
    kind: str = typer.Option(
        None,
        "--kind",
        help="Filter by kind (silent_run | recovered | retry_exhausted | stranded_in_progress | stranded_todo).",
    ),
    limit: int = typer.Option(50, "--limit"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List health events."""
    engine = _get_engine(config)
    rows = engine.list_health_events(status=status, kind=kind, limit=limit)
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print("[dim]No health events.[/dim]")
        return
    table = Table(title="Health Events")
    table.add_column("ID", style="cyan")
    table.add_column("Kind", style="magenta")
    table.add_column("Status", style="yellow")
    table.add_column("Task", style="dim")
    table.add_column("Created", style="dim")
    for row in rows:
        table.add_row(
            row["id"],
            row["kind"],
            row["status"],
            row.get("task_id") or "",
            row.get("created_at") or "",
        )
    console.print(table)


@health_app.command("show")
def health_show(
    event_id: str = typer.Argument(..., help="Health event id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show one health event by id."""
    engine = _get_engine(config)
    row = engine.get_health_event(event_id)
    if row is None:
        console.print(f"[red]Health event not found: {event_id}[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(row)
        return
    console.print(Panel(
        f"ID: {row['id']}\n"
        f"Kind: {row['kind']}\n"
        f"Status: {row['status']}\n"
        f"Task: {row.get('task_id') or '(none)'}\n"
        f"Project: {row.get('project_id') or '(none)'}\n"
        f"Run: {row.get('run_id') or '(none)'}\n"
        f"Created: {row['created_at']}\n"
        f"Snoozed until: {row.get('snoozed_until') or '(n/a)'}\n"
        f"Resolved by: {row.get('resolved_by') or '(n/a)'}\n"
        f"Resolved at: {row.get('resolved_at') or '(n/a)'}\n"
        f"Detail: {row.get('detail')}",
        title=f"Health Event {event_id}",
    ))


@health_app.command("resolve")
def health_resolve(
    event_id: str = typer.Argument(..., help="Health event id"),
    action: str = typer.Option(
        "continue",
        "--action",
        help="Player action: continue | snooze | dismiss",
    ),
    snooze_minutes: int = typer.Option(
        None,
        "--snooze-minutes",
        help="Snooze duration when action=snooze (defaults to company config).",
    ),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Resolve a health event."""
    engine = _get_engine(config)
    try:
        row = engine.resolve_health_event(
            event_id=event_id,
            action=action,
            snooze_minutes=snooze_minutes,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if row is None:
        console.print(f"[red]Health event not found: {event_id}[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(row)
        return
    console.print(Panel(
        f"Status: {row['status']}\n"
        f"Resolved by: {row.get('resolved_by')}\n"
        f"Snoozed until: {row.get('snoozed_until') or '(n/a)'}",
        title=f"Resolved {event_id}",
    ))


@episodes_app.command("list")
def episodes_list(
    retention: str = typer.Option(
        None,
        "--retention",
        help="Filter by retention tier (full | summary).",
    ),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List materialized project episodes."""
    engine = _get_engine(config)
    rows = engine.list_episodes(retention_tier=retention)
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        console.print("[dim]No episodes recorded yet.[/dim]")
        return
    table = Table(title="Project Episodes")
    table.add_column("Project", style="cyan")
    table.add_column("Tier", style="yellow")
    table.add_column("Updated", style="dim")
    table.add_column("Summary")
    for row in rows:
        table.add_row(
            row["project_id"],
            row["retention_tier"],
            row.get("updated_at") or "",
            (row["summary"] or "")[:80],
        )
    console.print(table)


@episodes_app.command("get")
def episodes_get(
    project_id: str = typer.Argument(..., help="Project id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Fetch one episode (includes payload_json if retention is full)."""
    engine = _get_engine(config)
    row = engine.get_episode(project_id)
    if row is None:
        console.print(f"[red]Episode not found for project: {project_id}[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(row)
        return
    panel_text = (
        f"Project: {row['project_id']}\n"
        f"Retention: {row['retention_tier']}\n"
        f"Updated: {row.get('updated_at')}\n"
        f"Summary: {row['summary']}\n"
        f"Payload: "
        + (
            f"{len(row['payload_json'])} chars"
            if row.get("payload_json")
            else "(trimmed)"
        )
    )
    console.print(Panel(panel_text, title="Episode"))


@episodes_app.command("rebuild")
def episodes_rebuild(
    project_id: str = typer.Argument(..., help="Project id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Re-materialize a project's episode payload from source tables."""
    engine = _get_engine(config)
    try:
        row = engine.rebuild_episode(project_id)
    except LookupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(row)
        return
    console.print(Panel(
        f"Project: {row['project_id']}\n"
        f"Retention: {row['retention_tier']}\n"
        f"Updated: {row.get('updated_at')}\n"
        f"Summary: {row['summary']}",
        title="Episode Rebuilt",
    ))


@app.command("memories")
def memories(
    agent_role: str = typer.Argument(..., help="Agent role"),
    limit: int = typer.Option(20, "--limit", "-n"),
    include_stale: bool = typer.Option(False, "--include-stale"),
    knowledge_type: str = typer.Option(None, "--knowledge-type"),
    category: str = typer.Option(None, "--category"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List memories for an agent."""
    engine = _get_engine(config)
    payload = engine.list_memories(
        agent_role,
        limit=limit,
        include_stale=include_stale,
        knowledge_type=knowledge_type,
        category=category,
    )
    if as_json:
        _emit_json(payload)
        return
    if not payload:
        console.print(f"[dim]No memories for {agent_role}.[/dim]")
        return
    table = Table(title=f"Memories — {agent_role}")
    table.add_column("Category", style="yellow")
    table.add_column("Type", style="cyan")
    table.add_column("Content")
    for m in payload:
        table.add_row(m["category"], m.get("knowledge_type", ""), m["content"][:80])
    console.print(table)


@app.command("release-delivery")
def release_delivery(
    approval_id: str = typer.Argument(..., help="Approved delivery_approval id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Release a delivery package after delivery_approval is approved."""
    engine = _get_engine(config)
    try:
        result = engine.release_delivery(approval_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"Status: {result['status']}\n"
        f"Project: {result.get('project_id')}\n"
        f"Tasks completed: {result['tasks_completed']}\n"
        f"Tasks failed: {result['tasks_failed']}\n"
        f"Released at: {result.get('released_at') or '(not released)'}",
        title="Delivery",
    ))


@app.command("execute-packet")
def execute_packet(
    approval_id: str = typer.Argument(..., help="Approved decision-chain approval id"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Execute an approved decision-chain packet under governance."""
    engine = _get_engine(config)
    try:
        with console.status("[bold]Executing approved decision packet..."):
            result = engine.execute_decision_packet(approval_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"Status: {result['status']}\n"
        f"Project ID: {result['project_id']}\n"
        f"Tasks completed: {result['tasks_completed']}\n"
        f"Tasks failed: {result['tasks_failed']}\n"
        f"Delivery approval ID: {result['delivery_approval_id']}",
        title="Governed Execution",
    ))


@app.command("resume-project")
def resume_project(
    project_id: str = typer.Argument(..., help="Project ID to resume"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Resume a project from persisted task/checkpoint state."""
    engine = _get_engine(config)
    try:
        with console.status(f"[bold]Resuming project '{project_id}'..."):
            result = engine.resume_project(project_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"Status: {result['status']}\n"
        f"Tasks completed: {result.get('tasks_completed', 0)}\n"
        f"Tasks failed: {result.get('tasks_failed', 0)}\n"
        f"Latest checkpoint: {(result.get('latest_checkpoint') or {}).get('id', '-')}",
        title=f"Project {project_id} Resume",
    ))


@app.command()
def trace(
    run_id: str = typer.Argument(..., help="run_id to trace (r_<ulid>)"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show the time-ordered event stream for a run_id.

    Pulls every audit, decision, ledger, memory, task, and approval row
    tagged with the given ``run_id`` and prints them in chronological
    order. Useful for debugging "what actually happened on this directive"
    and for distillation/SOP work downstream.
    """
    engine = _get_engine(config)
    payload = engine.trace_run(run_id)
    if as_json:
        _emit_json(payload)
        return
    if payload["event_count"] == 0:
        console.print(f"[dim]No events found for run_id {run_id}.[/dim]")
        return
    table = Table(title=f"Trace {run_id} ({payload['event_count']} events)")
    table.add_column("Time", style="dim")
    table.add_column("Kind", style="cyan")
    table.add_column("Agent", style="yellow")
    table.add_column("Summary")
    for event in payload["events"]:
        kind = event["kind"]
        if kind == "audit":
            agent = event.get("agent_role") or ""
            summary = f"{event['event_type']}: {event.get('action') or ''}"
        elif kind == "decision":
            agent = ""
            summary = (
                f"{event.get('directive_type') or ''} "
                f"cost=${event.get('total_ai_cost') or 0:.4f}"
            )
        elif kind == "ledger":
            agent = ""
            summary = (
                f"€{event.get('amount') or 0:+.4f} "
                f"({event.get('category') or ''}) "
                f"{event.get('description') or ''}"
            )
        elif kind == "memory":
            agent = event.get("agent_role") or ""
            summary = f"[{event.get('category') or ''}] {event.get('content') or ''}"
        elif kind == "task":
            agent = event.get("assigned_agent") or ""
            summary = (
                f"task {event.get('status') or ''}: "
                f"{event.get('title') or ''}"
            )
        elif kind == "approval":
            agent = event.get("requested_by") or ""
            summary = (
                f"{event.get('action_type') or ''}[{event.get('status') or ''}]: "
                f"{event.get('summary') or ''}"
            )
        else:
            agent = ""
            summary = ""
        table.add_row(
            (event.get("timestamp") or "")[:19],
            kind,
            agent,
            summary[:80],
        )
    console.print(table)


@app.command()
def execute(
    project_id: str = typer.Argument(..., help="Project ID to execute"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Execute a revenue project's tasks autonomously."""
    engine = _get_engine(config)
    p = engine.projects.get(project_id)

    if not p:
        console.print(f"[red]Project '{project_id}' not found.[/red]")
        raise typer.Exit(1)

    with console.status(f"[bold]Executing project '{p.name}'..."):
        result = engine.execute_project(project_id)
    if as_json:
        _emit_json(result)
        return

    console.print(Panel(
        f"Tasks completed: {result['tasks_completed']}\n"
        f"Tasks failed: {result['tasks_failed']}\n"
        f"AI cost: ${result['total_ai_cost']:.4f}\n"
        f"Fully funded: {'Yes' if result['fully_funded'] else 'No'}",
        title=f"Project {project_id} Execution",
    ))

    if result["outputs"]:
        table = Table(title="Task Outputs")
        table.add_column("Task", style="cyan")
        table.add_column("Agent", style="yellow")
        table.add_column("Cost", style="green")
        table.add_column("Output")
        for o in result["outputs"]:
            table.add_row(
                o["title"][:40],
                o["agent"],
                f"${o['cost']:.4f}",
                o["output"][:60] + "..." if len(o["output"]) > 60 else o["output"],
            )
        console.print(table)


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8000, "--port", help="TCP port."),
    open_browser: bool = typer.Option(
        False,
        "--open",
        help="Open the web UI in the default browser after the server starts.",
    ),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="Dev mode: auto-reload on source change (uvicorn --reload).",
    ),
):
    """Start the Kompany backend + cyberpunk web UI.

    The web UI is served at ``/ui`` and the SSE feed at ``/events``. With
    ``--open``, the default browser is launched at the UI URL after a
    short delay so the server is ready to handle the first request.
    """
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        console.print(
            "[red]uvicorn is not installed.[/red] "
            "Install the api extras: pip install 'kompany[api]'"
        )
        raise typer.Exit(1) from exc

    url = f"http://{host}:{port}/ui/"
    console.print(f"[green]Kompany backend on[/green] http://{host}:{port}")
    console.print(f"[green]UI on[/green] {url}")
    console.print(f"[green]SSE stream on[/green] http://{host}:{port}/events")

    if open_browser:
        # Schedule the browser launch slightly after uvicorn starts. We
        # use a small threading.Timer so we don't block before uvicorn.run.
        import threading
        import webbrowser

        def _open():
            try:
                webbrowser.open(url)
            except Exception:  # pragma: no cover — best effort
                pass

        threading.Timer(1.0, _open).start()

    uvicorn.run(
        "kompany.interfaces.api:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


# ---------------------------------------------------------------------------
# kompany reset — wipe DB + state with safety-tiered confirmation.
# ---------------------------------------------------------------------------


def _resolve_data_dir_for_cli(override: str | None):
    """Match the resolution order used by the REST sidecar so reset
    targets the same on-disk store the desktop app boots against
    (unless the user explicitly overrides via --data-dir)."""
    import os
    from pathlib import Path as _Path

    if override:
        return _Path(override).expanduser()
    env = os.environ.get("KOMPANY_DATA_DIR", "").strip()
    if env:
        return _Path(env).expanduser()
    return _Path("~/.kompany").expanduser()


@app.command()
def reset(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the y/N prompt for onboarded (non-live) state. Live state still requires --force.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Skip the typed-RESET confirmation even for live state. Use with care.",
    ),
    no_backup: bool = typer.Option(
        False,
        "--no-backup",
        help="Skip the auto-backup. Irreversible — use only for disposable state.",
    ),
    keep_credentials: bool = typer.Option(
        False,
        "--keep-credentials",
        help="Wipe everything except credential_vault rows so the API key survives.",
    ),
    data_dir: str = typer.Option(
        None,
        "--data-dir",
        help="Override the data directory (defaults to KOMPANY_DATA_DIR env or ~/.kompany).",
    ),
) -> None:
    """Wipe a Kompany install so onboarding starts from scratch.

    Three confirmation tiers based on detected state:

    \b
      * fresh (no template applied)        — no prompt.
      * onboarded, no projects + no spend  — y/N prompt; --yes skips.
      * live (>=1 project OR ledger spend) — must type RESET; --force skips.

    An auto-backup is written to ``<data_dir>.backup-<ISO>`` unless
    ``--no-backup`` is passed.
    """
    from kompany.installer.reset import (
        ResetError,
        inspect_state,
        reset as do_reset,
    )

    target = _resolve_data_dir_for_cli(data_dir)
    console.print(f"[bold]Resetting:[/bold] {target}")

    state = inspect_state(target)
    if not state.exists or state.is_fresh:
        console.print("  [dim]nothing applied — wiping any leftover files[/dim]")
    else:
        console.print(
            f"  template: [cyan]{state.template_id}[/cyan]   "
            f"projects: [cyan]{state.project_count}[/cyan]   "
            f"episodes: [cyan]{state.episode_count}[/cyan]   "
            f"total_spend: [cyan]${state.total_spend_usd:.2f}[/cyan]"
        )
        if state.revenue_target_usd is not None:
            console.print(
                f"  agreed_revenue_target: [cyan]${state.revenue_target_usd:,.2f}[/cyan]   "
                f"deadline: [cyan]{state.deadline or '--'}[/cyan]"
            )

    if not no_backup and (state.exists and not state.is_fresh):
        console.print("  [dim]backup will be written to a sibling .backup-<ISO> dir[/dim]")

    def _confirm(summary, expected: str) -> bool:
        if expected:
            console.print(
                "[yellow]Live state detected[/yellow] — type "
                "[bold]RESET[/bold] to confirm (anything else aborts):"
            )
            try:
                typed = typer.prompt("  >", default="", show_default=False)
            except typer.Abort:
                return False
            return typed.strip() == expected
        try:
            return typer.confirm("Proceed?", default=False)
        except typer.Abort:
            return False

    try:
        result = do_reset(
            target,
            yes=yes,
            force=force,
            no_backup=no_backup,
            keep_credentials=keep_credentials,
            confirm_callback=_confirm,
        )
    except ResetError as exc:
        console.print(f"[red]✗ reset aborted: {exc}[/red]")
        raise typer.Exit(1) from exc

    if result.backup_path:
        console.print(f"  [green]✓[/green] backup: {result.backup_path}")
    if result.credentials_kept:
        console.print("  [green]✓[/green] credential_vault preserved (--keep-credentials)")
    if result.files_removed:
        console.print(f"  [green]✓[/green] files removed: {', '.join(result.files_removed)}")
    if result.dirs_removed:
        console.print(f"  [green]✓[/green] dirs removed: {', '.join(result.dirs_removed)}")
    for note in result.notes:
        console.print(f"  [dim]{note}[/dim]")
    console.print("[green]reset complete[/green]")
