"""``kompany daemon`` sub-app — run/install/uninstall/status (rendering only).

06-12-daemon-tick-loop PR2. All logic lives in
:mod:`kompany.core.daemon_ops`; this module renders results via rich
(interfaces spec: no business logic in an interface file). It is a
sibling of ``cli.py`` because that file is over the 500-line cap —
``cli.py`` just registers :data:`daemon_app`.

These operations are machine-local process management and deliberately
CLI-only (PRD D5 — the documented interface-parity exception); tick
observability joins the existing status/observability operations on
all four interfaces instead.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

daemon_app = typer.Typer(
    name="daemon",
    help=(
        "Run or manage the 24/7 Kompany daemon server "
        "(machine-local process management — CLI-only by design)."
    ),
    no_args_is_help=True,
)
console = Console()


def _emit_json(data) -> None:
    console.print_json(data=data)


def _data_dir(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


@daemon_app.command("run")
def daemon_run(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(0, "--port", help="TCP port (0 = OS picks)."),
    data_dir: str = typer.Option(
        None, "--data-dir", help="Override the Kompany data directory."
    ),
):
    """Run the daemon server in the foreground (refuses if one already runs)."""
    from kompany.core import daemon_ops

    result = daemon_ops.run_daemon(host=host, port=port, data_dir=_data_dir(data_dir))
    if not result["started"]:
        console.print(f"[yellow]{result['message']}[/yellow]")
        raise typer.Exit(1)


@daemon_app.command("install")
def daemon_install(
    data_dir: str = typer.Option(
        None, "--data-dir", help="Override the Kompany data directory."
    ),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Install the daemon supervisor (launchd on macOS, systemd on Linux)."""
    from kompany.core import daemon_ops

    result = daemon_ops.install_daemon(data_dir=_data_dir(data_dir))
    if as_json:
        _emit_json(result)
        return
    if not result["installed"]:
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(1)
    if "plist_path" in result:
        # launchd
        bootstrap = result["bootstrap"]
        bootstrap_line = (
            "loaded into launchd"
            if bootstrap["ok"]
            else f"[yellow]bootstrap failed (loads at next login): {bootstrap['error']}[/yellow]"
        )
        console.print(Panel(
            f"Plist: {result['plist_path']}\n"
            f"Command: {' '.join(result['program_arguments'])}\n"
            f"Launchd: {bootstrap_line}",
            title="kompany daemon install",
        ))
    else:
        # systemd
        reload_res = result["reload"]
        enable_res = result["enable"]
        reload_line = (
            "daemon-reload ok"
            if reload_res and reload_res["ok"]
            else f"[yellow]daemon-reload failed: {reload_res['error'] if reload_res else 'n/a'}[/yellow]"
        )
        enable_line = (
            "enabled + started"
            if enable_res and enable_res["ok"]
            else f"[yellow]enable --now failed: {enable_res['error'] if enable_res else 'n/a'}[/yellow]"
        )
        console.print(Panel(
            f"Unit: {result['unit_path']}\n"
            f"ExecStart: {result['exec_start']}\n"
            f"{reload_line}\n"
            f"{enable_line}",
            title="kompany daemon install",
        ))


@daemon_app.command("uninstall")
def daemon_uninstall(
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Remove the daemon supervisor (launchd plist or systemd unit). Idempotent."""
    from kompany.core import daemon_ops

    result = daemon_ops.uninstall_daemon()
    if as_json:
        _emit_json(result)
        return
    if result["error"]:
        console.print(f"[red]{result['error']}[/red]")
        raise typer.Exit(1)
    state = "removed" if result["removed"] else "was not installed"
    console.print(f"[green]Daemon supervisor {state}.[/green]")


@daemon_app.command("status")
def daemon_status(
    data_dir: str = typer.Option(
        None, "--data-dir", help="Override the Kompany data directory."
    ),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Show daemon server, supervisor, and ticker status."""
    from kompany.core import daemon_ops

    payload = daemon_ops.daemon_status(data_dir=_data_dir(data_dir))
    if as_json:
        _emit_json(payload)
        return
    server = payload["server"]
    supervisor = payload["supervisor"]
    ticker = payload["ticker"]
    server_line = (
        f"running (source={server['source']}, pid={server['pid']}, "
        f"port={server['port']})"
        if server["running"]
        else "not running"
    )
    sup_type = supervisor.get("type", "none")
    if sup_type == "launchd":
        sup_line = (
            f"installed ({'loaded' if supervisor['loaded'] else 'not loaded'}) — "
            f"{supervisor['plist_path']}"
            if supervisor["installed"]
            else "not installed"
        )
    elif sup_type == "systemd":
        sup_line = (
            f"installed ({'active' if supervisor['loaded'] else 'inactive'}, "
            f"{'enabled' if supervisor['enabled'] else 'disabled'}) — "
            f"{supervisor['unit_path']}"
            if supervisor["installed"]
            else "not installed"
        )
    else:
        sup_line = "not installed (no built-in supervisor on this platform)"
    if ticker is None:
        ticker_line = "— (no server)"
    elif ticker.get("running"):
        ticker_line = (
            f"running (every {ticker['interval_seconds']}s, "
            f"{ticker['tick_count']} ticks, last {ticker['last_tick_at'] or '—'})"
        )
    else:
        ticker_line = "stopped"
    console.print(Panel(
        f"Server: {server_line}\n"
        f"Supervisor: {sup_line}\n"
        f"Ticker: {ticker_line}",
        title="Kompany Daemon",
    ))


__all__ = ["daemon_app"]
