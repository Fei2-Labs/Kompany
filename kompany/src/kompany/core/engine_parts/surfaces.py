"""ModelSource/founder/anima/workspaces/channels/credentials/tool surfaces.

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations

from typing import Any

from kompany.state.credentials import CredentialVaultStore
from kompany.state.models import ApprovalRequest, ApprovalStatus



class FounderSurfacesMixin:
    # ----- ModelSource founder surface (06-11-harness-execution-leg PR5b)
    # Thin wrappers — logic lives in ``core/model_source_ops.py``.

    def get_model_source(self) -> dict | None:
        """Active model source as a plain dict; ``None`` = legacy billing."""
        from kompany.core import model_source_ops

        return model_source_ops.get_model_source(self)

    def set_model_source(self, payload: dict | None) -> dict:
        """Set (or clear, with ``None``) the active model source."""
        from kompany.core import model_source_ops

        return model_source_ops.set_model_source(self, payload)

    def detect_agent_clis(self) -> dict:
        """Probe PATH for agent CLIs that unlock zero-key model sources."""
        from kompany.core import model_source_ops

        return model_source_ops.detect_agent_clis()

    # ----- Founder profile + rules (#6/#7)
    # Thin wrappers — logic lives in ``core/founder_config.py``.

    def get_founder_profile(self) -> dict | None:
        """Founder profile dict (address/comms prefs); ``None`` = unset."""
        from kompany.core import founder_config

        return founder_config.get_founder_profile(self)

    def set_founder_profile(self, payload: dict | None) -> dict:
        """Merge-set (or clear, with ``None``) the founder profile."""
        from kompany.core import founder_config

        return founder_config.set_founder_profile(self, payload)

    def get_founder_rules(self) -> dict | None:
        """Founder rules dict (``{hard, soft}``); ``None`` = unset."""
        from kompany.core import founder_config

        return founder_config.get_founder_rules(self)

    def set_founder_rules(self, payload: dict | None) -> dict:
        """Merge-set (or clear, with ``None``) the founder rules."""
        from kompany.core import founder_config

        return founder_config.set_founder_rules(self, payload)

    # ----- Anima persona surface (06-12-anima-persona PRD D5)
    # Thin wrappers — logic lives in ``core/anima.py``.

    def anima_state(self) -> dict:
        """Current persona state (valence, energy, tone, last_diary_date)."""
        from kompany.core import anima

        return anima.anima_state_op(self)

    def anima_diary_list(self, limit: int = 30) -> list[dict]:
        """Most recent diary entries, newest first."""
        from kompany.core import anima

        return anima.anima_diary_list_op(self, limit=limit)

    # ----- Workspaces (issue #15, multi-brand). Thin wrappers — logic
    # lives in ``config/workspaces.py``. A workspace IS a data dir;
    # switching only flips the registry — callers (REST switch endpoint,
    # CLI) must rebuild their engine so stores rebind to the new dir.

    def workspaces_list(self) -> dict:
        """Registry payload: active name, env_override flag, entries."""
        from kompany.config import workspaces

        return workspaces.workspaces_list()

    def workspace_switch(self, name: str) -> dict:
        """Mark ``name`` active. THIS engine instance stays bound to its
        original data dir — the payload says so honestly: callers must
        re-init (REST calls ``reset_engine()``; CLI exits anyway).

        ``restart_required`` is True when ``KOMPANY_DATA_DIR`` pins the
        process to a dir the registry cannot change (daemon plist), or
        for any long-lived consumer that cannot rebuild its engine."""
        import os

        from kompany.config import workspaces

        entry = workspaces.set_active(name)
        entry["restart_required"] = bool(
            os.environ.get("KOMPANY_DATA_DIR", "").strip()
        )
        return entry

    def workspace_create(self, name: str, label: str = "") -> dict:
        """Create + register a fresh workspace dir (not yet active)."""
        from kompany.config import workspaces

        return workspaces.create(name, label=label)

    # ----- Channels surface (06-12-channels PRD D5)
    # Thin wrappers — logic lives in ``channels/ops.py``.

    def channels_status(self) -> dict:
        """Adapter health (telegram worker, email poller) + outbox counts."""
        from kompany.channels import ops as channel_ops

        return channel_ops.channels_status_op(self)

    def outbox_list(self, limit: int = 20) -> list[dict]:
        """Most recent channel outbox rows, newest first."""
        from kompany.channels import ops as channel_ops

        return channel_ops.outbox_list_op(self, limit=limit)

    def list_credentials(self) -> list[dict]:
        return [entry.model_dump(mode="json") for entry in self.credentials.list()]

    def set_credential(self, name: str, value: str) -> dict:
        entry = self.credentials.set(name, value)
        if not getattr(self.settings, name, ""):
            setattr(self.settings, name, value)
        self.audit.record(
            "credential_vault.updated",
            f"Credential updated: {name}",
            detail={"name": name},
        )
        return entry.model_dump(mode="json")

    def delete_credential(self, name: str) -> dict:
        result = self.credentials.delete(name)
        self.audit.record(
            "credential_vault.deleted",
            f"Credential deleted: {name}",
            detail={"name": name, "deleted": result["deleted"]},
        )
        return result

    def rotate_credential_key(self, new_vault_key: str) -> dict:
        result = self.credentials.rotate_key(new_vault_key)
        self.settings.vault_key = new_vault_key
        self.credentials = CredentialVaultStore(self.db, self.settings.vault_key)
        try:
            from kompany.state.vault_keys import set_vault_key_in_keychain

            set_vault_key_in_keychain(
                new_vault_key,
                service=getattr(self.settings, "vault_keychain_service", "kompany"),
                account=getattr(self.settings, "vault_keychain_account", "vault-master-key"),
            )
        except ImportError:
            pass
        self.audit.record(
            "credential_vault.key_rotated",
            "Credential vault key rotated",
            detail={"rotated": result["rotated"], "names": result["names"]},
        )
        return result

    def list_tool_policies(self, agent_role: str | None = None) -> list[dict]:
        """List tool authorization policies."""
        return [
            policy.model_dump(mode="json")
            for policy in self.tool_authorization.list(agent_role=agent_role)
        ]

    def set_tool_policy(
        self,
        agent_role: str,
        tool_name: str,
        allowed: bool,
        reason: str = "",
        requires_approval: bool = False,
    ) -> dict:
        """Create or update a tool authorization policy."""
        policy = self.tool_authorization.set(
            agent_role=agent_role,
            tool_name=tool_name,
            allowed=allowed,
            reason=reason,
            requires_approval=requires_approval,
        )
        payload = policy.model_dump(mode="json")
        self.audit.record(
            "tool_authorization.policy_updated",
            f"Tool policy updated for {agent_role}:{tool_name}",
            detail=payload,
            agent_role=agent_role,
        )
        return payload

    def authorize_tool(
        self,
        agent_role: str,
        tool_name: str,
        purpose: str = "",
    ) -> dict:
        """Check whether an agent role may use a named tool."""
        from kompany.state.models import ToolAuthorizationResult

        policy = self.tool_authorization.get(agent_role, tool_name)
        allowed = bool(policy and policy.allowed)
        reason = (
            policy.reason
            if policy
            else "No policy exists for this agent role and tool."
        )
        status = "allowed" if allowed else "denied"
        if allowed and policy and policy.requires_approval:
            status = "approval_required"
        result = ToolAuthorizationResult(
            agent_role=agent_role,
            tool_name=tool_name,
            allowed=allowed,
            status=status,
            reason=reason,
        ).model_dump(mode="json")
        self.audit.record(
            f"tool_authorization.{status}",
            f"Tool authorization {status}: {agent_role} -> {tool_name}",
            detail={**result, "purpose": purpose},
            agent_role=agent_role,
        )
        return result

    def use_tool(
        self,
        agent_role: str,
        tool_name: str,
        purpose: str = "",
        arguments: dict | None = None,
        handler=None,
        approval_id: str | None = None,
    ) -> dict:
        """Authorize and optionally execute a tool through the engine gate."""
        from kompany.state.models import ApprovalRequest, ApprovalStatus, ToolAuthorizationResult

        auth = self.authorize_tool(agent_role, tool_name, purpose=purpose)
        if not auth["allowed"]:
            return auth
        if auth["status"] == "approval_required":
            approval = self._matching_tool_use_approval(
                approval_id,
                agent_role,
                tool_name,
                purpose,
            )
            if not approval or approval.status != ApprovalStatus.APPROVED:
                request = approval or self.approvals.create(ApprovalRequest(
                    action_type="tool_use",
                    summary=f"Approve tool use: {agent_role} -> {tool_name}",
                    payload={
                        "agent_role": agent_role,
                        "tool_name": tool_name,
                        "purpose": purpose,
                    },
                    requested_by="ToolAuthorizationGate",
                    severity="critical",
                ))
                result = ToolAuthorizationResult(
                    agent_role=agent_role,
                    tool_name=tool_name,
                    allowed=True,
                    status="approval_required",
                    reason=auth["reason"],
                    approval_id=request.id,
                ).model_dump(mode="json")
                if approval is None:
                    self.audit.record(
                        "tool_authorization.approval_requested",
                        f"Tool use approval requested: {agent_role} -> {tool_name}",
                        detail={
                            "agent_role": agent_role,
                            "tool_name": tool_name,
                            "purpose": purpose,
                            "approval_id": request.id,
                        },
                        agent_role=agent_role,
                    )
                return result
        if handler is None:
            return ToolAuthorizationResult(
                agent_role=agent_role,
                tool_name=tool_name,
                allowed=True,
                status="allowed",
                reason=auth["reason"],
                approval_id=approval_id,
            ).model_dump(mode="json")
        try:
            output = handler(arguments or {})
        except Exception as exc:
            failure = ToolAuthorizationResult(
                agent_role=agent_role,
                tool_name=tool_name,
                allowed=True,
                status="failed",
                reason=f"{type(exc).__name__}: tool execution failed",
            ).model_dump(mode="json")
            self.audit.record(
                "tool_authorization.execution_failed",
                f"Tool execution failed: {agent_role} -> {tool_name}",
                detail=failure,
                agent_role=agent_role,
            )
            return failure
        executed = ToolAuthorizationResult(
            agent_role=agent_role,
            tool_name=tool_name,
            allowed=True,
            status="executed",
            reason=auth["reason"],
            approval_id=approval_id,
            result=output if isinstance(output, dict) else {"value": output},
        ).model_dump(mode="json")
        self.audit.record(
            "tool_authorization.executed",
            f"Tool executed: {agent_role} -> {tool_name}",
            detail={k: v for k, v in executed.items() if k != "result"},
            agent_role=agent_role,
        )
        return executed

    def _matching_tool_use_approval(
        self,
        approval_id: str | None,
        agent_role: str,
        tool_name: str,
        purpose: str,
    ):
        if not approval_id:
            return None
        request = self.approvals.get(approval_id)
        if request is None or request.action_type != "tool_use":
            return None
        payload = request.payload or {}
        if (
            payload.get("agent_role") == agent_role
            and payload.get("tool_name") == tool_name
            and payload.get("purpose", "") == purpose
        ):
            return request
        return None

    # ------------------------------------------------------------------
    # Deferred tool actions (#5) — propose → approval queue → execute
    # ------------------------------------------------------------------

    def _tool_registry(self) -> dict:
        """Map tool name → Tool instance (loader: builtins + plugins)."""
        from kompany.core import tool_actions

        return {
            name: entry["tool"]
            for name, entry in tool_actions.tool_registry(self).items()
        }

    def tools_list(self) -> list[dict]:
        """Registered tools with side_effect / tier / connection state."""
        from kompany.core import tool_actions

        return tool_actions.tools_list(self)

    def integrations_list(self) -> list[dict]:
        """Registered integrations with required credentials + connection
        state. Same shape on REST ``GET /integrations``, MCP
        ``kompany_integrations`` and the SDK (#8)."""
        from kompany.core import tool_actions

        return tool_actions.integrations_list(self)

    def execute_tool(self, tool_name: str, inputs: dict) -> dict:
        """Inline execution — read-only zero-cost tools only. Anything
        side-effecting or paid is refused with ``requires_approval``."""
        from kompany.core import tool_actions

        return tool_actions.execute_tool(self, tool_name, inputs)

    def propose_action(
        self,
        tool_name: str,
        inputs: dict,
        summary: str,
        *,
        severity: str = "medium",
        requested_by: str = "team",
        directive_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        reason: str | None = None,
    ) -> dict:
        """Queue a deferred external action for founder approval.

        The action does NOT run now — it lands in the inbox as a
        ``tool_action`` approval carrying the tool + inputs + cost
        preview. On approve, ``approve_request`` executes it for real.
        This is the founder's money/decision gate: nothing external
        happens without a yes. PAID actions are hard-gated here too —
        they can ONLY reach execution through this card.

        Founder-tunable auto-approve (#4): if a ``tool_authorization``
        policy exists for ``(requested_by, tool_name)`` with
        ``allowed=True`` and ``requires_approval=False``, the card is
        filed AND immediately auto-approved through the exact same
        ``approve_request`` pipeline a human click would use — so the
        founder still gets a full audit trail (visible in the inbox
        history as approved by ``auto_approve_policy``), they just
        don't have to tap it. Zero-cost READ/WRITE_LOCAL tools never
        reach this path (they run inline via ``execute_tool``); any
        real cost (SPEND or a non-zero estimate) is NEVER auto-approved
        regardless of policy — same hard invariant as
        ``AutonomyGate.check_tool``.
        """
        from kompany.core import tool_actions
        from kompany.state.models import ApprovalRequest

        entry = tool_actions.tool_registry(self).get(tool_name)
        if entry is None:
            raise ValueError(f"unknown tool: {tool_name}")
        tool = entry["tool"]
        # Founder hard rules (#6): a blocked action never even reaches
        # the inbox — refuse with the founder-readable reason.
        try:
            parsed_for_rules = (
                tool.input_schema(**inputs) if tool.input_schema else inputs
            )
            rule_cost = tool.estimate_cost(parsed_for_rules).total_usd
        except Exception:  # noqa: BLE001 — estimate failure ≠ rule bypass
            rule_cost = 0.0
        refusal = self.autonomy.check_rules(
            self.get_founder_rules(),
            tool_name=tool_name,
            side_effect=tool.side_effect.value,
            estimated_cost_usd=rule_cost,
            description=tool.description,
        )
        if refusal:
            self.audit.record(
                "tool_action.refused",
                f"Founder rule refused proposed action: {tool_name}",
                detail={"tool_name": tool_name, "reason": refusal},
                directive_id=directive_id,
                project_id=project_id,
            )
            raise ValueError(refusal)
        payload: dict = {
            "tool_name": tool_name,
            "inputs": inputs,
            "side_effect": tool.side_effect.value,
            "autonomy_tier": tool.autonomy_tier.value,
        }
        if reason:
            payload["reason"] = reason
        if task_id:
            payload["task_id"] = task_id
        try:
            parsed = tool.input_schema(**inputs) if tool.input_schema else inputs
            payload["estimated_cost_usd"] = tool.estimate_cost(parsed).total_usd
        except Exception as exc:  # noqa: BLE001 — preview must not block
            payload["estimate_error"] = f"{type(exc).__name__}: {exc}"
        request = self.approvals.create(ApprovalRequest(
            action_type="tool_action",
            summary=summary,
            payload=payload,
            requested_by=requested_by,
            severity=severity,
            directive_id=directive_id,
            project_id=project_id,
        ))
        self.audit.record(
            "tool_action.proposed",
            f"Proposed action for approval: {tool_name}",
            detail={"approval_id": request.id, "tool_name": tool_name},
            directive_id=directive_id,
            project_id=project_id,
        )
        if self._auto_approve_eligible(tool, payload, requested_by):
            auto = self.approve_request(
                request.id, approved_by="auto_approve_policy"
            )
            if auto is not None:
                return auto
        return request.model_dump(mode="json")

    def _auto_approve_eligible(
        self, tool: Any, payload: dict, requested_by: str
    ) -> bool:
        """Founder-tunable auto-approve (#4) eligibility check.

        Consults the same ``tool_authorization`` policy the founder
        edits via ``kompany set-tool-policy`` / ``POST
        /tools/policies`` — a role+tool row with ``allowed=True`` and
        ``requires_approval=False`` means "run this inline, don't make
        me click approve". Never eligible for SPEND tools or any
        non-zero estimated/failed cost estimate, matching
        ``AutonomyGate.check_tool``'s hard no-auto-pay invariant.
        """
        from kompany.plugins.contract import SideEffect

        if tool.side_effect == SideEffect.SPEND:
            return False
        if payload.get("estimate_error"):
            return False
        if float(payload.get("estimated_cost_usd") or 0.0) > 0.0:
            return False
        policy = self.tool_authorization.get(requested_by, tool.name)
        return bool(policy and policy.allowed and not policy.requires_approval)

