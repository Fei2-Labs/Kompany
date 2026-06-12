"""Project, funding, budget, debate and ledger commands.

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
def abandon(
    project_id: str = typer.Argument(..., help="Project (plan) to abandon"),
    reason: str = typer.Option("", "--reason", "-r", help="Why the plan is abandoned"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Abandon a plan (#10): cancel the project, stop its unfinished
    tasks, withdraw its open inbox cards, release the unspent envelope."""
    engine = _get_engine(config)
    try:
        payload = engine.abandon_project(project_id, reason=reason)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    if as_json:
        _emit_json(payload)
        return
    if not payload["cancelled"]:
        console.print(f"[dim]Project {project_id} is already terminal "
                      f"({payload['status']}) — nothing to abandon.[/dim]")
        return
    console.print(Panel(
        f"Plan abandoned ({payload['previous_status']} → cancelled)\n"
        f"Tasks stopped: {payload['tasks_stopped']} · "
        f"Inbox cards withdrawn: {payload['approvals_withdrawn']}\n"
        f"Envelope released to treasury: €{payload['envelope_released']:.2f}",
        title=f"Project {project_id}",
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


