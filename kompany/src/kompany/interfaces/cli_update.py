"""``kompany update`` — one-button update from signed GitHub releases (rendering only)."""

from __future__ import annotations

import time

import typer
from rich.console import Console
from rich.panel import Panel

update_app = typer.Typer(
    name="update",
    help="Update the engine from signed GitHub releases: check, apply, status, rollback, mode.",
    no_args_is_help=False,
    invoke_without_command=True,
)
console = Console()


def _engine(config: str | None):
    from kompany.core.engine import KompanyEngine

    return KompanyEngine(config_path=config)


def _panel(st: dict, title: str) -> Panel:
    latest = st.get("latest", {}).get("kompany", {})
    lines = [f"Installed: kompany {st.get('installed_version')}"
             + (f" · kompany-pro {st['installed_pro_version']}" if st.get("installed_pro_version") else ""),
             f"Latest:    kompany {latest.get('version') or '?'}"
             + (f" · kompany-pro {st['latest']['kompany-pro'].get('version')}" if st.get("latest", {}).get("kompany-pro") else "")
             + (f"  (checked {st['last_check_at'][:19]})" if st.get("last_check_at") else "  (never checked)"),
             f"Update available: {'YES' if st.get('update_available') else 'no'}   mode: {st.get('mode')}",
             f"Phase: {st.get('phase')}" + (f" → {st.get('target_version')}" if st.get("target_version") else "")]
    lay = st.get("layout", {})
    lines.append(f"Layout: {lay.get('releases_dir')} current={lay.get('current')} "
                 f"({'release layout' if lay.get('running_from_release') else 'NOT release layout'}"
                 f"{', desktop bundle' if lay.get('frozen') else ''}) supervised={st.get('supervised')}")
    if not st.get("can_apply"):
        lines.append(f"[yellow]{st.get('cannot_apply_reason')}[/yellow]")
    if st.get("attestation"):
        lines.append(f"Provenance: {st['attestation'].get('status')}")
    for step in (st.get("steps") or [])[-4:]:
        lines.append(f"  {step['at'][11:19]} {step['step']}: {step['detail']}")
    if st.get("error"):
        lines.append(f"[red]{st['error']}[/red]")
    return Panel("\n".join(lines), title=title)


@update_app.callback()
def update_root(ctx: typer.Context, config: str = typer.Option(None, "--config", "-c"),
                as_json: bool = typer.Option(False, "--json")):
    """With no subcommand: check GitHub for a newer release and report."""
    if ctx.invoked_subcommand is not None:
        return
    st = _engine(config).update_check()
    if as_json:
        console.print_json(data=st); return
    console.print(_panel(st, "kompany update — check"))
    if st.get("update_available") and st.get("can_apply"):
        console.print("Run `kompany update apply` to install it.")


@update_app.command("status")
def update_status(config: str = typer.Option(None, "--config", "-c"), as_json: bool = typer.Option(False, "--json")):
    st = _engine(config).update_status()
    console.print_json(data=st) if as_json else console.print(_panel(st, "kompany update — status"))


@update_app.command("apply")
def update_apply(version: str = typer.Option(None, "--version"), config: str = typer.Option(None, "--config", "-c"),
                 as_json: bool = typer.Option(False, "--json")):
    """Download, verify, install, back up, switch, restart. Blocks until the switch (or a failure)."""
    e = _engine(config)
    st = e.update_apply(version, background=False)
    if as_json:
        console.print_json(data=st); return
    console.print(_panel(st, "kompany update — apply"))
    if st.get("phase") == "restarting":
        console.print("[green]Switched.[/green] " + ("The supervisor restarts the daemon now; run `kompany doctor` in a minute."
                                                     if not st.get("restart_required") else "Restart the daemon to finish."))
    raise typer.Exit(0 if st.get("phase") in ("restarting", "done") else 1)


@update_app.command("rollback")
def update_rollback(config: str = typer.Option(None, "--config", "-c")):
    st = _engine(config).update_rollback()
    console.print(_panel(st, "kompany update — rollback"))
    raise typer.Exit(0 if st.get("phase") == "restarting" else 1)


@update_app.command("mode")
def update_mode(mode: str = typer.Argument(..., help="manual | automatic_when_idle"),
                config: str = typer.Option(None, "--config", "-c")):
    try:
        st = _engine(config).update_set_mode(mode)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]"); raise typer.Exit(1)
    console.print(f"update mode → {st['mode']}")


@update_app.command("watch")
def update_watch(config: str = typer.Option(None, "--config", "-c"), seconds: int = typer.Option(120, "--seconds")):
    """Poll the update phase until it settles (for scripts)."""
    e = _engine(config)
    for _ in range(max(1, seconds // 2)):
        st = e.update_status()
        console.print(f"{st.get('phase')}: {(st.get('steps') or [{}])[-1].get('detail', '')}")
        if st.get("phase") in ("done", "failed", "rolled_back", "idle", "restarting"):
            break
        time.sleep(2)


__all__ = ["update_app"]
