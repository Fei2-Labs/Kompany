"""Company portability commands: encrypted export / import bundles.

``kompany export`` produces a passphrase-encrypted bundle of the full
engine state (DB snapshot + config + vault/key files); ``--handoff``
tombstones this machine so the bundle's new home is the only live
company. ``kompany import`` reconstitutes the state on a fresh machine
WITHOUT booting an engine first (the engine would create a fresh DB).
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.panel import Panel

from kompany.interfaces.cli_parts.common import (
    app,
    console,
    _get_engine,
    _emit_json,
)


@app.command("export")
def export_company(
    out: str = typer.Option(None, "--out", "-o", help="Bundle output path (.kmp)"),
    handoff: bool = typer.Option(
        False,
        "--handoff",
        help="Tombstone this machine: the imported copy becomes the live company",
    ),
    passphrase: str = typer.Option(
        ...,
        "--passphrase",
        prompt=True,
        confirmation_prompt=True,
        hide_input=True,
        help="Encrypts the bundle (required again at import)",
    ),
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Export the full company state as a passphrase-encrypted bundle."""
    engine = _get_engine(config)
    try:
        result = engine.export_company(passphrase, out_path=out, handoff=handoff)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    lines = [
        f"Bundle: {result['path']}",
        f"Size: {result['size_bytes']} bytes",
        f"Files: {', '.join(result['files'])}",
    ]
    if handoff:
        lines.append(
            "[yellow]This machine is tombstoned — the engine here will no "
            "longer tick. Import the bundle on the new machine.[/yellow]"
        )
    console.print(Panel("\n".join(lines), title="Export"))


@app.command("import")
def import_company(
    bundle: str = typer.Argument(..., help="Path to a .kmp bundle"),
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing company database"
    ),
    passphrase: str = typer.Option(
        ...,
        "--passphrase",
        prompt=True,
        hide_input=True,
        help="The passphrase used at export",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Reconstitute a company from an encrypted bundle (fresh machine)."""
    from kompany.config.workspaces import resolve_data_dir
    from kompany.state.export_bundle import BundlePassphraseError, import_bundle

    data_dir = resolve_data_dir()
    try:
        result = import_bundle(Path(bundle), passphrase, data_dir, force=force)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        # BundlePassphraseError is a ValueError; message says which.
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"Data dir: {result['data_dir']}\n"
        f"Files: {', '.join(result['files'])}\n"
        f"Bundle created: {result['bundle_created_at']}\n"
        "Next: `kompany status` to verify, then `kompany daemon install`.",
        title="Import",
    ))


@app.command("merge")
def merge_company_cmd(
    source: str = typer.Argument(..., help="Path to the other side: a .kmp bundle or a kompany.db"),
    passphrase: str = typer.Option(None, "--passphrase", help="Passphrase when source is a .kmp bundle"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would change; write nothing"),
    force: bool = typer.Option(False, "--force", help="Allow merging when one side has no company_name"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
    config: str = typer.Option(None, "--config", "-c"),
):
    """Merge a diverged copy of the SAME company into this one (union, never replace).

    A verified backup is taken first. Different companies are refused.
    """
    from kompany.state.merge_company import MergeRefused, merge_company

    engine = _get_engine(config)
    try:
        report = merge_company(
            engine.db, Path(source), passphrase=passphrase, dry_run=dry_run, force=force,
            backups=engine.backups,
        )
    except (MergeRefused, FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if not dry_run:
        engine.audit.record(
            "company.merged", f"Merged fork from {source}",
            detail={k: v for k, v in report.as_dict().items() if k != "notes"},
        )
    payload = report.as_dict()
    if as_json:
        _emit_json(payload)
        return
    lines = [f"Company: {report.company}", f"Source: {report.source}",
             f"Mode: {'DRY RUN' if dry_run else 'applied'}" + (f" · backup {report.backup_id}" if report.backup_id else ""),
             f"Inserted: {payload['total_inserted']} rows — " + (", ".join(f"{t} +{n}" for t, n in report.inserted.items()) or "none"),
             f"Updated (newer wins): {payload['total_updated']} — " + (", ".join(f"{t} {n}" for t, n in report.updated.items()) or "none"),
             f"Collisions kept local: " + (", ".join(f"{t} {n}" for t, n in report.collisions.items()) or "none"),
             f"Skipped tables: {', '.join(report.skipped_tables) or 'none'}",
             f"Ledger balance chain recomputed over {report.ledger_recomputed_rows} rows"]
    lines += [f"Note: {n}" for n in report.notes]
    console.print(Panel("\n".join(lines), title="Merge"))


# ---------------------------------------------------------------------------
# Remote backup commands (07-14 cloud-deploy-backup-restore step 5)
# ---------------------------------------------------------------------------

@app.command("remote-backup")
def remote_backup(
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Upload an encrypted export bundle to S3-compatible remote storage."""
    from kompany.state.remote_backup import RemoteBackupConfig, RemoteBackupError, upload_bundle

    engine = _get_engine(None)
    cfg_dict = engine.settings.remote_backup
    if not cfg_dict:
        console.print("[red]No remote_backup config in config.yaml[/red]")
        raise typer.Exit(1)
    try:
        cfg = RemoteBackupConfig.from_dict(cfg_dict)
        result = upload_bundle(cfg, engine.settings.data_dir)
    except RemoteBackupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    lines = [
        f"Key: {result['key']}",
        f"Size: {result['size_bytes']} bytes",
        f"Created: {result['created_at']}",
    ]
    if result.get("pruned"):
        lines.append(f"Pruned: {result['pruned']} old bundle(s)")
    console.print(Panel("\n".join(lines), title="Remote Backup"))


@app.command("remote-backups")
def remote_backups(
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List remote backup bundles."""
    from kompany.state.remote_backup import RemoteBackupConfig, RemoteBackupError, list_remote_bundles

    engine = _get_engine(None)
    cfg_dict = engine.settings.remote_backup
    if not cfg_dict:
        console.print("[red]No remote_backup config in config.yaml[/red]")
        raise typer.Exit(1)
    try:
        cfg = RemoteBackupConfig.from_dict(cfg_dict)
        bundles = list_remote_bundles(cfg)
    except RemoteBackupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(bundles)
        return
    if not bundles:
        console.print("[yellow]No remote bundles found[/yellow]")
        return
    for b in bundles:
        console.print(f"  {b['key']}  {b['size_bytes']}B  {b['last_modified']}")


@app.command("remote-restore")
def remote_restore(
    key: str = typer.Option(None, "--key", help="Specific bundle key (default: latest)"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing database"),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Download and import a remote backup bundle."""
    from kompany.config.workspaces import resolve_data_dir
    from kompany.state.remote_backup import RemoteBackupConfig, RemoteBackupError, restore_from_remote

    cfg_dict = _get_engine(None).settings.remote_backup
    if not cfg_dict:
        console.print("[red]No remote_backup config in config.yaml[/red]")
        raise typer.Exit(1)
    data_dir = resolve_data_dir()
    try:
        cfg = RemoteBackupConfig.from_dict(cfg_dict)
        result = restore_from_remote(cfg, data_dir, key=key, force=force)
    except RemoteBackupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(result)
        return
    console.print(Panel(
        f"Restored from: {result['restored_from_key']}\n"
        f"Data dir: {result['data_dir']}\n"
        f"Files: {', '.join(result['files'])}\n"
        f"Bundle created: {result['bundle_created_at']}",
        title="Remote Restore",
    ))
