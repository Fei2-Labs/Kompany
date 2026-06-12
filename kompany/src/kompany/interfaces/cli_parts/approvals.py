"""Approvals queue and inbox commands.

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


