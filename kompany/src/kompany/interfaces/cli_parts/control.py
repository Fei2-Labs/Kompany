"""Decision packets, overrides, observability, dashboard, status.

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


@app.command("doctor")
def doctor(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Health tree: what is broken and how to fix it (offline, read-only)."""
    from rich.tree import Tree

    from kompany.core.doctor import render_tree

    engine = _get_engine(config)
    report = engine.doctor()
    if as_json:
        _emit_json(report)
        return
    colour = {"ok": "green", "info": "dim", "warn": "yellow", "fail": "red"}
    glyph = {"ok": "✓", "info": "·", "warn": "!", "fail": "✗"}

    def add(branch, n):
        label = f"[{colour[n['status']]}]{glyph[n['status']]} {n['label']}[/{colour[n['status']]}]"
        if n.get("detail"):
            label += f" [dim]— {n['detail']}[/dim]"
        sub = branch.add(label)
        if n.get("fix") and n["status"] in ("warn", "fail"):
            sub.add(f"[cyan]fix:[/cyan] {n['fix']}")
        for c in n.get("children", []):
            add(sub, c)

    s = report["summary"]
    tree = Tree(f"[bold]Kompany doctor[/bold] — {s['status'].upper()} ({s['ok']} ok, {s['warn']} warn, {s['fail']} fail)")
    for c in report["children"]:
        add(tree, c)
    console.print(tree)
    if s["status"] != "ok" and not console.is_terminal:
        console.print(render_tree(report))
    raise typer.Exit(1 if s["fail"] else 0)
