"""``kompany evolve`` sub-app (08-29 R2 artifact lane). Rendering only."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

evolve_app = typer.Typer(
    name="evolve",
    help="Self-evolution of souls and workflows: propose, audit, revert. Full-auto with doctor-gated revert.",
    no_args_is_help=True,
)
console = Console()


def _engine(config: str | None):
    from kompany.core.engine import KompanyEngine

    return KompanyEngine(config_path=config)


def _panel(row: dict, title: str) -> Panel:
    lines = [f"Proposal: {row['id']}  [{row['status']}]", f"{row['kind']}: {row['target']}",
             f"Instruction: {row['instruction']}"]
    if row.get("summary"):
        lines.append(f"Summary: {row['summary']}")
    if row.get("commit_sha"):
        lines.append(f"Commit: {row['commit_sha'][:12]}" + (f"  reverted by {row['revert_sha'][:12]}" if row.get("revert_sha") else ""))
    if row.get("doctor_status"):
        lines.append(f"Doctor: {row['doctor_status']}")
    for f in row.get("flags") or []:
        lines.append(f"[yellow]⚠ {f.get('kind')} ({f.get('severity')}): {', '.join(f.get('added', [])) or f.get('step') or ''}[/yellow]")
    if row.get("error"):
        lines.append(f"[red]{row['error']}[/red]")
    lines.append(f"Cost: ${float(row.get('cost_usd') or 0):.3f}")
    if row.get("diff_stat"):
        lines.append(f"Diff:\n{row['diff_stat']}")
    return Panel("\n".join(lines), title=title)


@evolve_app.command("propose")
def evolve_propose(kind: str = typer.Argument(..., help="soul | workflow | plugin"),
                   target: str = typer.Argument(..., help="role or workflow_id (file stem)"),
                   instruction: str = typer.Argument(...),
                   config: str = typer.Option(None, "--config", "-c"), as_json: bool = typer.Option(False, "--json")):
    try:
        row = _engine(config).evolution_propose(kind, target, instruction)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]"); raise typer.Exit(1)
    if as_json:
        console.print_json(data=row); return
    console.print(_panel(row, "kompany evolve propose"))
    raise typer.Exit(0 if row.get("status") == "applied" else 1)


@evolve_app.command("list")
def evolve_list(limit: int = typer.Option(20, "--limit"), status: str = typer.Option(None, "--status"),
                config: str = typer.Option(None, "--config", "-c"), as_json: bool = typer.Option(False, "--json")):
    rows = _engine(config).evolution_list(limit=limit, status=status)
    if as_json:
        console.print_json(data=rows); return
    t = Table(title="Artifact evolution")
    for col in ("id", "status", "kind", "target", "flags", "cost", "summary"):
        t.add_column(col)
    for r in rows:
        t.add_row(r["id"], r["status"], r["kind"], r["target"], str(len(r.get("flags") or [])),
                  f"${float(r.get('cost_usd') or 0):.2f}", (r.get("summary") or r.get("error") or "")[:50])
    console.print(t)


@evolve_app.command("show")
def evolve_show(proposal_id: str = typer.Argument(...), config: str = typer.Option(None, "--config", "-c"),
                as_json: bool = typer.Option(False, "--json")):
    row = _engine(config).evolution_show(proposal_id)
    if row is None:
        console.print("[red]proposal not found[/red]"); raise typer.Exit(1)
    if as_json:
        console.print_json(data=row); return
    console.print(_panel(row, "kompany evolve show"))


@evolve_app.command("revert")
def evolve_revert(proposal_id: str = typer.Argument(...), reason: str = typer.Option("founder revert", "--reason"),
                  config: str = typer.Option(None, "--config", "-c")):
    row = _engine(config).evolution_revert(proposal_id, reason)
    if row is None:
        console.print("[red]proposal not found[/red]"); raise typer.Exit(1)
    console.print(f"{proposal_id} → {row['status']}" + (f" ({row['revert_sha'][:12]})" if row.get("revert_sha") else ""))


@evolve_app.command("status")
def evolve_status(config: str = typer.Option(None, "--config", "-c"), as_json: bool = typer.Option(False, "--json")):
    st = _engine(config).evolution_status()
    if as_json:
        console.print_json(data=st); return
    ws = st["workspace"]
    lines = [f"Enabled: {st['enabled']}  tier: {st['model_tier']}",
             f"Budget today: ${st['spent_today_usd']:.2f} / ${st['daily_cap_usd']:.2f}",
             f"Workspace: {ws['root']} ({'initialised' if ws['initialized'] else 'not initialised'}"
             + (", dirty" if ws.get("dirty") else "") + ")",
             f"Souls: {', '.join(ws['souls']) or '—'}", f"Workflows: {', '.join(ws['workflows']) or '—'}"]
    for c in st["recent_commits"][:5]:
        lines.append(f"  {c['sha'][:7]} {c['message'][:70]}")
    console.print(Panel("\n".join(lines), title="kompany evolve status"))


__all__ = ["evolve_app"]
