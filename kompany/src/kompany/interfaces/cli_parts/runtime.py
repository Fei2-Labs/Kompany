"""Runtime, heartbeat, tool policy, credential and backup commands.

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


@app.command("drain")
def drain(
    reason: str = typer.Option("deployment", "--reason", "-r"),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Begin a deployment drain (suspend + report initial drain status).

    Poll ``kompany runtime drain-status`` until ``ready_for_restart`` is
    true before restarting the process (Stage A deployment plan, step 6)."""
    engine = _get_engine(config)
    payload = engine.drain(reason=reason)
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"State: {payload['state']}\n"
        f"Active operations: {payload.get('active_operations')}\n"
        f"Ready for restart: {payload.get('ready_for_restart')}",
        title="Drain",
    ))


@app.command("drain-status")
def drain_status(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Poll drain progress without changing runtime state."""
    engine = _get_engine(config)
    payload = engine.drain_status()
    if as_json:
        _emit_json(payload)
        return
    console.print(Panel(
        f"State: {payload['state']}\n"
        f"Active operations: {payload.get('active_operations')}\n"
        f"Ready for restart: {payload.get('ready_for_restart')}",
        title="Drain status",
    ))


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


