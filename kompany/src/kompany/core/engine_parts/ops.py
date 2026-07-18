"""Health events, templates, config getters, suspend/resume, backups.

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations

from typing import Any

from kompany.core.event_hub import get_event_hub
from kompany.core.run_context import current_run_id, run_scope
from kompany.llm.cost_tracker import CostTracker
from kompany.state.agent_status import AgentStatusStore
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.checkpoints import CheckpointStore
from kompany.state.conversation import ConversationStore
from kompany.state.credentials import CredentialVaultStore
from kompany.state.vault_keys import resolve_vault_key
from kompany.state.database import Database
from kompany.state.journal import Journal
from kompany.state.ledger import Ledger
from kompany.state.debates import Debates
from kompany.state.episodes import Episodes
from kompany.state.health_events import HealthEvents
from kompany.state.projects import Projects
from kompany.state.memory import AgentMemory
from kompany.state.skills import SkillStore
from kompany.state.runtime import RuntimeStateStore
from kompany.state.remote_replay import RemoteReplayStore
from kompany.state.shadow_costs import ShadowCostStore
from kompany.state.templates import TemplateAlreadyApplied, TemplateNotFound
from kompany.state.tool_authorization import ToolAuthorizationStore



class EngineOpsMixin:
    # ------------------------------------------------------------------
    # Health events (resilience watchdog)
    # ------------------------------------------------------------------

    def list_health_events(
        self,
        status: str | None = None,
        project_id: str | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List health events, newest-first, optionally filtered."""
        return self.health_events.list(
            status=status,
            project_id=project_id,
            kind=kind,
            limit=limit,
        )

    def get_health_event(self, event_id: str) -> dict[str, Any] | None:
        """Fetch a single health event by id."""
        return self.health_events.get(event_id)

    def resolve_health_event(
        self,
        event_id: str,
        action: str,
        snooze_minutes: int | None = None,
        resolved_by: str = "player",
    ) -> dict[str, Any] | None:
        """Apply a player action (``continue`` / ``snooze`` / ``dismiss``)."""
        if action == "snooze" and snooze_minutes is None:
            snooze_minutes = self._get_int_config(
                "health_default_snooze_minutes", default=30
            )
        return self.watchdog.resolve(
            event_id=event_id,
            action=action,
            resolved_by=resolved_by,
            snooze_minutes=snooze_minutes,
        )

    def scan_stranded_tasks(self) -> list[dict[str, Any]]:
        """One-shot stranded-task sweep. Returns the events written."""
        if current_run_id() is None:
            with run_scope():
                return self.watchdog.scan_once()
        return self.watchdog.scan_once()

    def mark_task_stranded(
        self,
        task_id: str,
        project_id: str | None = None,
        reason: str = "llm_unavailable",
    ) -> dict[str, Any]:
        """Flip a task to ``stranded_in_progress`` + write health event.

        Called by the engine when an ``LLMUnavailable`` is caught after
        the watchdog's retry budget is exhausted.
        """
        if current_run_id() is None:
            with run_scope():
                return self._mark_task_stranded_inner(task_id, project_id, reason)
        return self._mark_task_stranded_inner(task_id, project_id, reason)

    def _mark_task_stranded_inner(
        self,
        task_id: str,
        project_id: str | None,
        reason: str,
    ) -> dict[str, Any]:
        try:
            self.projects.update_task_status_raw(
                task_id=task_id,
                status="stranded_in_progress",
            )
        except Exception:
            pass
        return self.watchdog.record_stranded_in_progress(
            task_id=task_id,
            project_id=project_id,
            detail={"reason": reason},
        )

    # ------------------------------------------------------------------
    # Company templates (ready-to-play scenarios)
    # ------------------------------------------------------------------

    def list_templates(self) -> list[dict[str, Any]]:
        """Return all available company templates as dicts."""
        return [tpl.model_dump() for tpl in self.templates.list_templates()]

    def show_template(self, template_id: str) -> dict[str, Any]:
        """Return one template's manifest + rendered mission body."""
        try:
            tpl, mission = self.templates.show_with_mission(template_id)
        except TemplateNotFound as exc:
            raise ValueError(str(exc)) from exc
        payload = tpl.model_dump()
        payload["mission"] = mission
        return payload

    def apply_template(
        self,
        template_id: str,
        force: bool = False,
        override_budget: float | None = None,
        override_directive: str | None = None,
        override_revenue_target: float | None = None,
        override_customer_target: int | None = None,
        override_deadline: str | None = None,
    ) -> dict[str, Any]:
        """Apply a ready-to-play template to the current company.

        Wraps :meth:`Templates.apply` in an audit-friendly run scope and
        normalizes the structured errors into ``ValueError`` so every
        surface (CLI, REST, MCP, SDK) handles them uniformly.
        """
        if current_run_id() is None:
            with run_scope():
                return self._apply_template_inner(
                    template_id,
                    force=force,
                    override_budget=override_budget,
                    override_directive=override_directive,
                    override_revenue_target=override_revenue_target,
                    override_customer_target=override_customer_target,
                    override_deadline=override_deadline,
                )
        return self._apply_template_inner(
            template_id,
            force=force,
            override_budget=override_budget,
            override_directive=override_directive,
            override_revenue_target=override_revenue_target,
            override_customer_target=override_customer_target,
            override_deadline=override_deadline,
        )

    def _apply_template_inner(
        self,
        template_id: str,
        *,
        force: bool,
        override_budget: float | None,
        override_directive: str | None,
        override_revenue_target: float | None = None,
        override_customer_target: int | None = None,
        override_deadline: str | None = None,
    ) -> dict[str, Any]:
        try:
            result = self.templates.apply(
                template_id,
                force=force,
                override_budget=override_budget,
                override_directive=override_directive,
                override_revenue_target=override_revenue_target,
                override_customer_target=override_customer_target,
                override_deadline=override_deadline,
            )
        except TemplateNotFound as exc:
            raise ValueError(str(exc)) from exc
        except TemplateAlreadyApplied as exc:
            raise ValueError(str(exc)) from exc
        return result.model_dump()

    def _get_int_config(self, key: str, default: int) -> int:
        """Read an integer config value from ``company_config``.

        Falls back to ``default`` if the row is missing or unparseable.
        """
        row = self.db.execute(
            "SELECT value FROM company_config WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return default

    def _get_float_config(self, key: str, default: float) -> float:
        """Read a float config value from ``company_config``.

        Falls back to ``default`` if the row is missing or unparseable.
        ``company_config`` is the founder-configurable settings store (cf.
        ``targets.*`` rows, watchdog interval overrides); this is the same
        seam the channel spend threshold uses.
        """
        row = self.db.execute(
            "SELECT value FROM company_config WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return float(row["value"])
        except (TypeError, ValueError):
            return default

    def suspend(self, reason: str = "manual") -> dict:
        """Suspend the engine. Idempotent: re-suspending is a no-op."""
        current = self.runtime.get()
        if current["state"] == "suspended":
            return {**current, "status": "already_suspended"}
        new_state = self.runtime.set("suspended", reason=reason)
        self.audit.record(
            "runtime.suspended",
            f"Engine suspended ({reason})",
            detail={"reason": reason},
        )
        return {**new_state, "status": "suspended"}

    def resume(self) -> dict:
        """Resume the engine. Idempotent: re-resuming is a no-op."""
        current = self.runtime.get()
        if current["state"] == "running":
            return {**current, "status": "already_running"}
        new_state = self.runtime.set("running", reason=None)
        self.audit.record(
            "runtime.resumed",
            "Engine resumed",
            detail={"previous_reason": current["reason"]},
        )
        return {**new_state, "status": "resumed"}

    def create_backup(self, label: str = "manual") -> dict:
        """Create a labeled SQLite snapshot of the live database."""
        meta = self.backups.create_backup(label=label, kind="manual")
        self.audit.record(
            "backup.created",
            f"Backup created: {meta['id']}",
            detail={
                "id": meta["id"],
                "label": label,
                "size_bytes": meta["size_bytes"],
            },
        )
        return meta

    def list_backups(self) -> list[dict]:
        """List all SQLite snapshots, newest first."""
        return self.backups.list_backups()

    def restore_backup(self, backup_id: str) -> dict:
        """Restore a snapshot, automatically creating a pre-restore backup.

        Re-binds dependent stores so subsequent queries see the restored state
        without restarting the process. Raises ``FileNotFoundError`` if the
        backup does not exist.
        """
        meta = self.backups.get(backup_id)
        if meta is None:
            raise FileNotFoundError(f"Backup '{backup_id}' not found")

        # 1. Auto pre-restore snapshot (recorded in current live audit).
        auto_meta = self.backups.create_backup(
            label=f"pre-restore-{backup_id}", kind="auto"
        )
        self.audit.record(
            "backup.auto_created",
            f"Auto-pre-restore backup: {auto_meta['id']}",
            detail={"id": auto_meta["id"], "for_restore": backup_id},
        )

        # 2. Close live DB and swap file.
        self.db.close()
        result = self.backups.restore_backup(backup_id)

        # 3. Rebind all dependent stores to the new connection.
        self.db = Database(self.settings.data_dir)
        self.ledger = Ledger(self.db)
        self.journal = Journal(self.db)
        self.projects = Projects(self.db)
        self.memory = AgentMemory(self.db)
        self.skills = SkillStore(self.db)
        self.audit = AuditLog(self.db)
        self.debates = Debates(self.db)
        self.episodes = Episodes(self.db)
        self.health_events = HealthEvents(self.db)
        self.approvals = ApprovalRequests(self.db)
        self.channel = ConversationStore(self.db)
        self.agent_status = AgentStatusStore(self.db)
        self.checkpoints = CheckpointStore(self.db)
        self.runtime = RuntimeStateStore(self.db)
        self.remote_replay = RemoteReplayStore(self.db)
        # Resolve the vault key BEFORE constructing the store so the
        # credential decrypt path works on first call. Without this,
        # Tauri sidecar (which doesn't get KOMPANY_VAULT_KEY in env)
        # boots with vault_key="" → _apply_vault_credentials silently
        # no-ops → custom_api_key + custom_base_url stay empty on every
        # subsequent engine instance → LLMClient routes via model-name
        # prefix (gpt-5.5 → openai.com) and the custom-provider key
        # 401s. Keychain lookup carries the key across sidecar restarts.
        if not self.settings.vault_key:
            try:
                vault_key, _source = resolve_vault_key(
                    self.settings.vault_key,
                    keychain_service=getattr(
                        self.settings, "vault_keychain_service", "kompany"
                    ),
                    keychain_account=getattr(
                        self.settings, "vault_keychain_account", "vault-master-key"
                    ),
                    data_dir=self.settings.data_dir,
                )
                self.settings.vault_key = vault_key
            except Exception:  # noqa: BLE001 — first-boot resolution miss is fine
                pass
        self.credentials = CredentialVaultStore(self.db, self.settings.vault_key)
        self._apply_vault_credentials()
        self.tool_authorization = ToolAuthorizationStore(self.db)
        # Re-wire after backup restore: same hub instance, fresh ledger.
        self.shadow_costs = ShadowCostStore(self.db)
        self.cost_tracker = CostTracker(
            self.ledger,
            event_hub=get_event_hub(),
            settings=self.settings,
            shadow_costs=self.shadow_costs,
        )
        # Daemon tick loop (06-12-daemon-tick-loop PR1): the tick store
        # captured the OLD (now closed) Database at construction, and the
        # running Ticker holds that store instance. Swap the connection
        # in place so post-restore ticks keep recording without
        # reconstructing the ticker.
        self.daemon_ticks.db = self.db
        self.self_update_proposals.db = self.db

        # 4. Audit restore in the (now restored) live DB.
        self.audit.record(
            "backup.restored",
            f"Backup restored: {backup_id}",
            detail={
                "id": backup_id,
                "auto_pre_restore_id": auto_meta["id"],
                "restored_from": result["restored_from"],
            },
        )
        return {
            **meta,
            "restored_at": result["restored_at"],
            "auto_pre_restore_id": auto_meta["id"],
        }

    def export_company(
        self,
        passphrase: str,
        out_path: str | None = None,
        handoff: bool = False,
    ) -> dict[str, Any]:
        """Export the full engine state as a passphrase-encrypted bundle.

        Bundle = live DB snapshot + config.yaml + vault master key +
        any ``*.key`` files at the data_dir root. With ``handoff=True``
        the company on THIS machine is suspended and tombstoned so two
        machines never tick the same company (the bundle's new home is
        the live one).
        """
        from pathlib import Path

        from kompany.state.export_bundle import create_bundle, write_exported_marker

        meta = create_bundle(
            self.settings.data_dir,
            passphrase,
            Path(out_path) if out_path else None,
        )
        self.audit.record(
            "export.created",
            f"Company exported to bundle: {meta['path']}",
            detail={
                "path": meta["path"],
                "size_bytes": meta["size_bytes"],
                "files": meta["files"],
                "handoff": handoff,
            },
        )
        if handoff:
            self.suspend(reason=f"exported (handoff) to {meta['path']}")
            marker = write_exported_marker(self.settings.data_dir, meta["path"])
            self.audit.record(
                "export.handoff",
                "Company handed off — this machine is tombstoned",
                detail=marker,
            )
            meta = {**meta, "handoff": True, "exported_at": marker["exported_at"]}
        return meta

