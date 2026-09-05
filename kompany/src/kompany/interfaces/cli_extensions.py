"""``kompany extensions`` sub-app (07-24 four-layer). Rendering only."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

extensions_app = typer.Typer(
    name="extensions",
    help="Customer extensions: install into the customer layer, approve, run isolated.",
    no_args_is_help=True,
)
console = Console()


def _engine(config: str | None):
    from kompany.core.engine import KompanyEngine

    return KompanyEngine(config_path=config)


@extensions_app.command("list")
def extensions_list(config: str = typer.Option(None, "--config", "-c"),
                    as_json: bool = typer.Option(False, "--json")):
    rows = _engine(config).extensions_list()
    if as_json:
        console.print_json(data=rows); return
    table = Table(title="Extensions")
    for col in ("id", "version", "owner", "status", "block_reason"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"], r["version"], r["owner"], r["status"], r.get("block_reason") or "")
    console.print(table)


@extensions_app.command("show")
def extension_show(extension_id: str = typer.Argument(...), config: str = typer.Option(None, "--config", "-c"),
                   as_json: bool = typer.Option(False, "--json")):
    row = _engine(config).extension_show(extension_id)
    if row is None:
        console.print("[red]extension not found[/red]"); raise typer.Exit(1)
    if as_json:
        console.print_json(data=row); return
    caps = row["manifest"].get("capabilities", {})
    lines = [f"{row['name']} v{row['version']} ({row['owner']}) — {row['status']}",
             f"core_api: {row['manifest'].get('core_api') or 'any'}",
             f"tools: {', '.join(caps.get('tools', [])) or '—'}", f"paths: {', '.join(caps.get('paths', [])) or '—'}",
             f"network: {', '.join(caps.get('network', [])) or '—'}", f"budget: ${caps.get('budget_usd', 0):.2f}",
             f"package: {row['pkg_path']}"]
    if row.get("block_reason"):
        lines.append(f"[red]blocked: {row['block_reason']}[/red]")
    if row.get("approval_id") and row["status"] == "installed":
        lines.append(f"Approval {row['approval_id']} pending — approve it in the inbox to activate.")
    for run in row.get("runs", [])[:5]:
        lines.append(f"run {run['id']}: {run['status']} denied={len(run['denied'])} {run.get('error') or ''}")
    console.print(Panel("\n".join(lines), title="kompany extensions show"))


@extensions_app.command("install")
def extension_install(path: str = typer.Argument(..., help="Directory holding extension.json"),
                      config: str = typer.Option(None, "--config", "-c"), as_json: bool = typer.Option(False, "--json")):
    try:
        row = _engine(config).extension_install(path)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]"); raise typer.Exit(1)
    if as_json:
        console.print_json(data=row); return
    console.print(f"Installed {row['id']} v{row['version']} — status {row['status']}. "
                  + ("Approve its activation card in the inbox." if row["status"] == "installed" else row.get("block_reason") or ""))


@extensions_app.command("run")
def extension_run(extension_id: str = typer.Argument(...), job: str = typer.Option("{}", "--job", help="JSON object"),
                  timeout: int = typer.Option(120, "--timeout"), config: str = typer.Option(None, "--config", "-c")):
    try:
        out = _engine(config).extension_run(extension_id, json.loads(job), timeout_seconds=timeout)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]"); raise typer.Exit(1)
    console.print_json(data=out)
    if not out.get("ok"):
        raise typer.Exit(1)


@extensions_app.command("enable")
def extension_enable(extension_id: str = typer.Argument(...), config: str = typer.Option(None, "--config", "-c")):
    row = _engine(config).extension_set_enabled(extension_id, True)
    console.print(row["status"] if row else "[red]extension not found[/red]")


@extensions_app.command("disable")
def extension_disable(extension_id: str = typer.Argument(...), config: str = typer.Option(None, "--config", "-c")):
    row = _engine(config).extension_set_enabled(extension_id, False)
    console.print(row["status"] if row else "[red]extension not found[/red]")


__all__ = ["extensions_app"]
