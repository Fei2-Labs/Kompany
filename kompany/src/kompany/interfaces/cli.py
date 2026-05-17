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


@app.command()
def directive(
    text: str = typer.Argument(..., help="Your directive in natural language"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Send a directive to your Kompany."""
    engine = _get_engine(config)
    with console.status("[bold]CEO processing directive..."):
        result = engine.process_directive(text)
    payload = {
        "status": result.status,
        "message": result.message,
        "project_id": result.project_id,
        "approval_id": result.approval_id,
        "total_ai_cost": result.total_ai_cost,
        "agents_used": result.agents_used,
    }
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(result.message, title=f"Kompany [{result.status}]"))


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
    engine = _get_engine(config)
    cfo = engine.registry.get("cfo")
    summary = cfo.get_summary()
    active = engine.projects.list_active()
    payload = {
        "company": engine.settings.company_name or "",
        "goal": engine.settings.company_goal or "",
        "time_horizon": engine.settings.company_time_horizon or "",
        "exclusions": engine.settings.company_exclusions or "",
        "stage": engine.settings.company_stage or "solo",
        "balance": summary["balance"],
        "total_income": summary["total_income"],
        "total_expenses": summary["total_expenses"],
        "total_ai_costs": abs(summary["total_ai_costs"]),
        "active_projects": len(active),
    }
    if as_json:
        _emit_json(payload)
        return

    table = Table(title="Kompany Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Company", engine.settings.company_name or "(not initialized)")
    table.add_row("Goal", engine.settings.company_goal or "(none)")
    table.add_row("Time Horizon", engine.settings.company_time_horizon or "(none)")
    table.add_row("Exclusions", engine.settings.company_exclusions or "(none)")
    table.add_row("Stage", engine.settings.company_stage or "solo")
    table.add_row("Balance", f"€{summary['balance']:.2f}")
    table.add_row("Total Income", f"€{summary['total_income']:.2f}")
    table.add_row("Total Expenses", f"€{summary['total_expenses']:.2f}")
    table.add_row("AI Costs", f"${abs(summary['total_ai_costs']):.4f}")
    table.add_row("Active Projects", str(len(active)))
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


@app.command("serve")
def serve(
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
