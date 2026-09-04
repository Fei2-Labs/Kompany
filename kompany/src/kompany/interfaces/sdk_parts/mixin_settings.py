"""KompanySettingsOps — settings, governance, inbox, and utility methods (ADR-0003)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kompany.core.engine import KompanyEngine

from .namespaces import (
    _ApprovalsNamespace,
    _EpisodesNamespace,
    _GlossaryNamespace,
    _HealthNamespace,
)


class KompanySettingsOps:
    """Mixin: glossary, episodes, health, approvals, inbox, runtime, credentials,
    tool policies, memories, model source, founder profile/rules, workspaces,
    anima, channels, self-update, backup, heartbeat, notifications.
    """

    _engine: "KompanyEngine"

    @property
    def glossary(self) -> "_GlossaryNamespace":
        """Company-glossary operations: ``list``, ``show``, ``add``,
        ``update``, ``remove``.

        Glossary-and-drift-detection task (05-19). Returns the founder-
        defined canonical terms + forbidden synonyms the CoS retrospective
        scans for drift.
        """
        return _GlossaryNamespace(self._engine)

    @property
    def episodes(self) -> "_EpisodesNamespace":
        """Project-episode operations: ``list``, ``get``, ``rebuild``."""
        return _EpisodesNamespace(self._engine)

    @property
    def health(self) -> "_HealthNamespace":
        """Health-event operations: ``list``, ``get``, ``resolve``."""
        return _HealthNamespace(self._engine)

    def runtime_status(self) -> dict[str, Any]:
        """Return engine runtime state."""
        return self._engine.get_runtime_state()

    def heartbeat(
        self,
        dispatch: bool = False,
        adapter: str = "dry-run",
    ) -> dict[str, Any]:
        """Run one heartbeat check."""
        return self._engine.heartbeat_once(dispatch=dispatch, adapter=adapter)

    def dispatch_notifications(
        self,
        events: list[dict[str, Any]],
        adapter: str = "dry-run",
    ) -> list[dict[str, Any]]:
        """Dispatch notification events."""
        return self._engine.dispatch_notifications(events, adapter=adapter)

    def suspend(self, reason: str = "manual") -> dict[str, Any]:
        """Suspend the engine."""
        return self._engine.suspend(reason=reason)

    def resume(self) -> dict[str, Any]:
        """Resume the engine."""
        return self._engine.resume()

    def create_backup(self, label: str = "manual") -> dict[str, Any]:
        """Create a labeled SQLite snapshot."""
        return self._engine.create_backup(label=label)

    def list_backups(self) -> list[dict[str, Any]]:
        """List SQLite snapshots, newest first."""
        return self._engine.list_backups()

    def restore_backup(self, backup_id: str) -> dict[str, Any]:
        """Restore a SQLite snapshot."""
        return self._engine.restore_backup(backup_id)

    def model_source(self) -> dict[str, Any] | None:
        """Active model source as a plain dict; ``None`` = legacy billing.

        ModelSource founder surface (06-11-harness-execution-leg). The
        dict carries ``kind`` / ``billing_mode`` / ``monthly_fee_usd``
        plus the derived (read-only) ``vehicle`` — same shape as REST
        ``GET /settings/model-source`` and MCP ``kompany_model_source_show``.
        """
        return self._engine.get_model_source()

    def set_model_source(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        """Set the active model source; ``None`` clears it (legacy billing).

        ``payload`` takes ``kind`` (custom_api | claude_subscription |
        openai_subscription) plus optional ``billing_mode`` /
        ``monthly_fee_usd`` / ``price_overrides``. Raises ``ValueError``
        on validation failure (e.g. subscription without a monthly fee).
        """
        return self._engine.set_model_source(payload)

    def detect_clis(self) -> dict[str, Any]:
        """Probe PATH for agent CLIs that unlock zero-key model sources."""
        return self._engine.detect_agent_clis()

    def founder_profile(self) -> dict[str, Any] | None:
        """Founder profile dict, or ``None`` when unset (#7).

        Same shape as REST ``GET /founder/profile`` and MCP
        ``kompany_founder_profile_show``: address / pronouns /
        comms_style / language / working_hours / timezone /
        risk_tolerance (only the fields the founder set).
        """
        return self._engine.get_founder_profile()

    def set_founder_profile(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        """Merge-set the founder profile; ``None`` clears it.

        A partial payload merges over the stored profile. Raises
        ``ValueError`` on validation failure (unknown field).
        Returns ``{"profile": dict|None}``.
        """
        return self._engine.set_founder_profile(payload)

    def founder_rules(self) -> dict[str, Any] | None:
        """Founder rules dict ``{hard, soft}``, or ``None`` when unset (#6)."""
        return self._engine.get_founder_rules()

    def set_founder_rules(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        """Merge-set the founder rules; ``None`` clears them.

        ``payload`` carries ``hard`` (list of ``{kind, match, action}``,
        kind ∈ exclude_capability | budget_cap | forbid_paid_category)
        and/or ``soft`` (free text). Top-level merge; ``ValueError`` on
        validation failure. Returns ``{"rules": dict|None}``.
        """
        return self._engine.set_founder_rules(payload)

    def agent_work_summary(self) -> dict[str, dict[str, Any]]:
        """Per-agent task-history summary keyed by lowercase role.

        Same dict REST ``GET /agents/work-summary`` and MCP
        ``kompany_agent_work_summary`` return (06-12-panel-truthfulness
        #22): ``delivered`` / ``completed`` / ``failed`` / ``total`` /
        ``last_active`` per role.
        """
        return self._engine.agent_work_summary()

    def tools_list(self) -> list[dict[str, Any]]:
        """Registered native tools with side_effect / tier / connection
        state. Same shape as REST ``GET /tools`` and MCP
        ``kompany_tools_list`` (action pipeline #4/#5)."""
        return self._engine.tools_list()

    def workflows_list(self) -> list[dict[str, Any]]:
        """Workflow catalog (built-in + plugin) with cost preview. Same
        shape as REST ``GET /workflows`` and MCP ``kompany_workflows_list``."""
        return self._engine.workflows_list()

    def run_workflow(
        self,
        workflow_id: str,
        inputs: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Run a workflow now; returns per-step outputs + cost. Gated steps
        file inbox cards. Raises ``WorkflowNotFound`` for an unknown id."""
        return self._engine.run_workflow(workflow_id, inputs or {}, project_id=project_id)

    def integrations_list(self) -> list[dict[str, Any]]:
        """Registered integrations with required credentials + connection
        state. Same shape as REST ``GET /integrations`` and MCP
        ``kompany_integrations`` (#8)."""
        return self._engine.integrations_list()

    def tools_propose(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        summary: str = "",
        reason: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Propose a tool action — files a ``tool_action`` approval card.

        Nothing executes now; approving the card runs the action for
        real. PAID actions can ONLY run through this path. Raises
        ``ValueError`` for an unknown tool."""
        return self._engine.propose_action(
            tool_name,
            inputs,
            summary=summary or f"Run {tool_name}",
            reason=reason,
            project_id=project_id,
            task_id=task_id,
        )

    def workspaces_list(self) -> dict[str, Any]:
        """Workspace registry (issue #15): active brand + entries. Same
        shape as REST ``GET /workspaces`` and MCP ``kompany_workspaces``."""
        return self._engine.workspaces_list()

    def workspace_switch(self, name: str) -> dict[str, Any]:
        """Mark ``name`` as the active workspace. The wrapped engine
        stays bound to its original data dir — build a new SDK instance
        after switching. Raises ``WorkspaceError`` for unknown names."""
        return self._engine.workspace_switch(name)

    def workspace_create(self, name: str, label: str = "") -> dict[str, Any]:
        """Create + register a fresh workspace dir (not yet active)."""
        return self._engine.workspace_create(name, label=label)

    def anima_state(self) -> dict[str, Any]:
        """Current Anima persona state (06-12-anima-persona PRD D5).

        Same dict REST ``GET /anima/state`` and MCP ``kompany_anima_state``
        return: ``valence`` / ``energy`` / ``tone`` / ``last_diary_date`` /
        ``updated_at`` / ``enabled``.
        """
        return self._engine.anima_state()

    def anima_diary(self, limit: int = 30) -> list[dict[str, Any]]:
        """Recent Anima diary entries, newest first (REST ``GET /anima/diary``)."""
        return self._engine.anima_diary_list(limit=limit)

    def channels_status(self) -> dict[str, Any]:
        """Channel adapter health + outbox counts (06-12-channels PRD D5).

        Same dict REST ``GET /channels/status`` and MCP
        ``kompany_channels_status`` return.
        """
        return self._engine.channels_status()

    def channels_outbox(self, limit: int = 20) -> list[dict[str, Any]]:
        """Recent channel outbox rows (REST ``GET /channels/outbox``)."""
        return self._engine.outbox_list(limit=limit)

    def self_update_propose(self, instruction: str) -> dict[str, Any]:
        """Governed self-update propose flow (06-12-self-update-pipeline).

        Runs a harness session in the dedicated clone, enforces the T3
        tier guard on the real diff, runs tests, and files a
        ``self_update_proposal`` approval card. Same dict shape as REST
        ``POST /self-update/propose`` and MCP ``kompany_self_update_propose``.
        """
        return self._engine.self_update_propose(instruction)

    def self_update_list(self, limit: int = 20) -> list[dict[str, Any]]:
        """Recent self-update proposals, newest first."""
        return self._engine.self_update_list(limit=limit)

    def self_update_show(self, proposal_id: str) -> dict[str, Any] | None:
        """One proposal row by id; ``None`` when unknown."""
        return self._engine.self_update_show(proposal_id)

    def list_credentials(self) -> list[dict[str, Any]]:
        return self._engine.list_credentials()

    def set_credential(self, name: str, value: str) -> dict[str, Any]:
        return self._engine.set_credential(name, value)

    def delete_credential(self, name: str) -> dict[str, Any]:
        return self._engine.delete_credential(name)

    def rotate_credential_key(self, new_vault_key: str) -> dict[str, Any]:
        return self._engine.rotate_credential_key(new_vault_key)

    def list_tool_policies(self, agent_role: str | None = None) -> list[dict[str, Any]]:
        """List tool authorization policies."""
        return self._engine.list_tool_policies(agent_role=agent_role)

    def set_tool_policy(
        self,
        agent_role: str,
        tool_name: str,
        allowed: bool,
        reason: str = "",
        requires_approval: bool = False,
    ) -> dict[str, Any]:
        """Create or update a tool authorization policy."""
        return self._engine.set_tool_policy(
            agent_role,
            tool_name,
            allowed,
            reason=reason,
            requires_approval=requires_approval,
        )

    def authorize_tool(
        self,
        agent_role: str,
        tool_name: str,
        purpose: str = "",
    ) -> dict[str, Any]:
        """Check whether an agent may use a tool."""
        return self._engine.authorize_tool(agent_role, tool_name, purpose=purpose)

    def use_tool(
        self,
        agent_role: str,
        tool_name: str,
        purpose: str = "",
        arguments: dict[str, Any] | None = None,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Authorize a tool use without attaching an execution handler."""
        return self._engine.use_tool(
            agent_role,
            tool_name,
            purpose=purpose,
            arguments=arguments,
            approval_id=approval_id,
        )

    def list_memories(
        self,
        agent_role: str,
        limit: int = 20,
        include_stale: bool = False,
        knowledge_type: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """List memories for an agent."""
        return self._engine.list_memories(
            agent_role,
            limit=limit,
            include_stale=include_stale,
            knowledge_type=knowledge_type,
            category=category,
        )

    def override(self, text: str) -> dict[str, Any]:
        """Request an override with a risk briefing."""
        return self._engine.process_override(text)

    def approvals(self) -> list[dict[str, Any]]:
        """List pending approval requests."""
        return self._engine.list_approvals()

    def approve(self, approval_id: str) -> dict[str, Any] | None:
        """Approve a pending request."""
        return self._engine.approve_request(approval_id)

    def reject(self, approval_id: str, reason: str = "") -> dict[str, Any] | None:
        """Reject a pending request."""
        return self._engine.reject_request(approval_id, reason=reason)

    def inbox(
        self,
        statuses: tuple[str, ...] = ("pending", "snoozed"),
    ) -> list[dict[str, Any]]:
        """RPG-style inbox of actionable approvals.

        Default surfaces both ``pending`` and ``snoozed`` rows; terminal
        approvals (``approved``/``rejected``/``revision_requested``/
        ``cancelled``) are read via ``approvals_ns.show(id)``.
        """
        return self._engine.inbox(statuses=statuses)

    @property
    def approvals_ns(self) -> "_ApprovalsNamespace":
        """Approval-thread operations: ``show``, ``approve``, ``reject``,
        ``revise``, ``snooze``, ``cancel``, ``comment``."""
        return _ApprovalsNamespace(self._engine)
