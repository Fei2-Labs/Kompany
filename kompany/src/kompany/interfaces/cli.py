"""Kompany CLI — the primary interface."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="kompany",
    help="Autonomous business operating system for solo founders.",
    no_args_is_help=True,
)
console = Console()


def _get_engine(config: str | None = None):
    from kompany.core.engine import KompanyEngine
    return KompanyEngine(config_path=config)


@app.command()
def init(
    name: str = typer.Option(..., prompt="Company name"),
    product: str = typer.Option(..., prompt="One-line product description"),
    balance: float = typer.Option(0.0, prompt="Starting balance (EUR)"),
    stage: str = typer.Option("solo", prompt="Stage (solo/pre-seed/seed/series-a)"),
):
    """Initialize a new Kompany."""
    engine = _get_engine()
    engine.initialize_company(name=name, product=product, balance=balance, stage=stage)
    console.print(Panel(
        f"[green]Kompany '{name}' initialized.[/green]\n"
        f"Product: {product}\n"
        f"Stage: {stage}\n"
        f"Balance: €{balance:.2f}",
        title="Kompany",
    ))


@app.command()
def directive(
    text: str = typer.Argument(..., help="Your directive in natural language"),
    config: str = typer.Option(None, "--config", "-c"),
):
    """Send a directive to your Kompany."""
    engine = _get_engine(config)
    with console.status("[bold]CEO processing directive..."):
        result = engine.process_directive(text)
    console.print(Panel(result.message, title=f"Kompany [{result.status}]"))


@app.command()
def status(config: str = typer.Option(None, "--config", "-c")):
    """Show company status."""
    engine = _get_engine(config)
    cfo = engine.registry.get("cfo")
    summary = cfo.get_summary()
    active = engine.projects.list_active()

    table = Table(title="Kompany Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Company", engine.settings.company_name or "(not initialized)")
    table.add_row("Balance", f"€{summary['balance']:.2f}")
    table.add_row("Total Income", f"€{summary['total_income']:.2f}")
    table.add_row("Total Expenses", f"€{summary['total_expenses']:.2f}")
    table.add_row("AI Costs", f"${abs(summary['total_ai_costs']):.4f}")
    table.add_row("Active Projects", str(len(active)))
    console.print(table)


@app.command()
def projects(config: str = typer.Option(None, "--config", "-c")):
    """List active projects."""
    engine = _get_engine(config)
    active = engine.projects.list_active()

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
):
    """Run a full multi-agent debate on a strategic question."""
    engine = _get_engine(config)
    from kompany.core.debate import DebateEngine

    stage = engine.settings.company_stage or "solo"
    with console.status(f"[bold]Running {stage}-stage debate..."):
        de = DebateEngine(engine.registry, stage=stage)
        result = de.run(question=question, company_state=engine.get_company_state())

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
):
    """Show recent ledger entries."""
    engine = _get_engine(config)
    entries = engine.ledger.get_recent(limit=limit)

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
):
    """Show details for a specific project."""
    engine = _get_engine(config)
    p = engine.projects.get(project_id)

    if not p:
        console.print(f"[red]Project '{project_id}' not found.[/red]")
        raise typer.Exit(1)

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

    tasks = engine.projects.list_tasks(p.id)
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
def execute(
    project_id: str = typer.Argument(..., help="Project ID to execute"),
    config: str = typer.Option(None, "--config", "-c"),
):
    """Execute a revenue project's tasks autonomously."""
    engine = _get_engine(config)
    p = engine.projects.get(project_id)

    if not p:
        console.print(f"[red]Project '{project_id}' not found.[/red]")
        raise typer.Exit(1)

    with console.status(f"[bold]Executing project '{p.name}'..."):
        result = engine.execute_project(project_id)

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
