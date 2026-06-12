"""Kompany Python SDK — programmatic access to the engine."""

from __future__ import annotations

from typing import Any

from kompany.core.debate import DebateEngine
from kompany.core.engine import KompanyEngine


class Kompany:
    """High-level SDK for interacting with Kompany programmatically.

    Usage:
        from kompany import Kompany
        k = Kompany()
        k.init("MyCompany", capital=50.0, goal="Buy a Mac Studio M4 128GB")
        result = k.directive("Buy a Mac Studio M4 128GB")
        print(result["message"])
    """

    def __init__(self, config_path: str | None = None):
        self._engine = KompanyEngine(config_path=config_path)

    def init(
        self,
        name: str,
        capital: float = 0.0,
        goal: str = "",
        time_horizon: str = "",
        exclusions: str = "",
    ) -> dict[str, Any]:
        """Initialize a new company."""
        self._engine.initialize_company(
            name=name,
            capital=capital,
            goal=goal,
            time_horizon=time_horizon,
            exclusions=exclusions,
        )
        return {
            "status": "initialized",
            "name": name,
            "capital": capital,
            "goal": goal,
            "time_horizon": time_horizon,
            "exclusions": exclusions,
            "stage": "solo",
        }

    def directive(self, text: str, session_id: str | None = None) -> dict[str, Any]:
        """Send a directive and return the result as a dict.

        Backward-compat one-shot entry point. ``session_id`` is optional —
        omit it to open a fresh CEO-channel session (legacy behaviour); pass
        the ``session_id`` from a prior ``clarify``/``gated`` result to
        continue the same session. For ergonomic multi-turn use prefer
        :pyattr:`channel`. Flattens through ``DirectiveResult.to_dict()`` so
        the keys match REST/MCP exactly (status / message / project_id /
        approval_id / total_ai_cost / agents_used / run_id / session_id).
        """
        result = self._engine.process_directive(text, session_id=session_id)
        return result.to_dict()

    @property
    def channel(self) -> "_ChannelNamespace":
        """CEO-channel session object (06-03-ceo-channel parity surface).

        ``client.channel.send(text)`` opens a session and returns the
        flattened result (carrying ``session_id``); ``.send(text,
        session_id=...)`` continues it (clarify reply). ``.go(id)`` /
        ``.abandon(id)`` act on a threshold-gated session; ``.sessions()`` /
        ``.session(id)`` read history. Mirrors REST ``/channel/*``.
        """
        return _ChannelNamespace(self._engine)

    def debate(self, question: str) -> dict[str, Any]:
        """Run a full multi-agent debate on a strategic question."""
        stage = self._engine.settings.company_stage or "solo"
        debate_engine = DebateEngine(self._engine.registry, stage=stage)
        result = debate_engine.run(
            question=question,
            company_state=self._engine.get_company_state(),
        )
        return {
            "question": result.question,
            "rounds": [[pos.model_dump() for pos in rnd] for rnd in result.rounds],
            "synthesis": result.synthesis.model_dump() if result.synthesis else None,
            "decision": result.decision.model_dump() if result.decision else None,
        }

    def remote_command(
        self,
        source: str,
        text: str,
        chat_id: str = "",
        bearer_token: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Handle an authenticated inbound remote command."""
        return self._engine.handle_remote_command({
            "source": source,
            "text": text,
            "chat_id": chat_id,
            "bearer_token": bearer_token,
            "payload": payload or {},
        })

    def cleanup_remote_replays(self, ttl_seconds: int | None = None) -> dict[str, Any]:
        """Delete expired remote replay records."""
        return self._engine.cleanup_remote_replays(ttl_seconds=ttl_seconds)

    def observability(self) -> dict[str, Any]:
        """Return an operational observability/RPG snapshot."""
        return self._engine.observability_snapshot()

    def status(self) -> dict[str, Any]:
        """Get company status (canonical dict — see core/status_ops.py)."""
        from kompany.core.status_ops import build_status

        return build_status(self._engine)

    def projects(self) -> list[dict[str, Any]]:
        """List active projects."""
        active = self._engine.projects.list_active()
        return [
            {
                "id": p.id,
                "name": p.name,
                "type": p.type.value,
                "status": p.status.value,
                "target_amount": p.target_amount,
                "funded_amount": p.funded_amount,
            }
            for p in active
        ]

    def project(self, project_id: str) -> dict[str, Any] | None:
        """Get a specific project by ID."""
        p = self._engine.projects.get(project_id)
        if not p:
            return None
        tasks = self._engine.projects.list_tasks(p.id)
        return {
            "id": p.id,
            "name": p.name,
            "type": p.type.value,
            "status": p.status.value,
            "target_amount": p.target_amount,
            "funded_amount": p.funded_amount,
            "plan": p.plan,
            "assigned_agents": p.assigned_agents,
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "agent": t.assigned_agent,
                    "status": t.status.value,
                }
                for t in tasks
            ],
        }

    def balance(self) -> float:
        """Get current balance."""
        return self._engine.ledger.get_balance()

    def ledger(self, limit: int = 10) -> list[dict]:
        """Get recent ledger entries."""
        return self._engine.ledger.get_recent(limit=limit)

    def execute_project(self, project_id: str) -> dict:
        """Execute a revenue project's tasks autonomously."""
        return self._engine.execute_project(project_id)

    def resume_project(self, project_id: str) -> dict[str, Any]:
        """Resume a project from persisted task/checkpoint state."""
        return self._engine.resume_project(project_id)

    def prepare_decision_packet(
        self,
        text: str,
        target_amount: float | None = None,
    ) -> dict[str, Any]:
        """Prepare a full decision-chain packet without executing it."""
        return self._engine.prepare_decision_packet(text, target_amount=target_amount)

    def execute_decision_packet(self, approval_id: str) -> dict[str, Any]:
        """Execute an approved decision-chain packet under governance."""
        return self._engine.execute_decision_packet(approval_id)

    def release_delivery(self, approval_id: str) -> dict[str, Any]:
        """Release a delivery package after delivery_approval is approved."""
        return self._engine.release_delivery(approval_id)

    def run_retrospective(self, project_id: str) -> dict[str, Any]:
        """Run (or replay) the CoS retrospective for a project."""
        return self._engine.run_retrospective(project_id)

    def distill(
        self,
        since: Any = None,
        dry_run: bool = False,
        episode_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run CoS cross-episode distillation.

        ``since`` accepts a ``timedelta`` (or numeric seconds). ``None``
        falls back to the engine's 30-day default. Pass ``episode_ids``
        to distil an explicit subset and bypass the window + 50-episode
        ceiling.
        """
        return self._engine.distill(
            since=since,
            dry_run=dry_run,
            episode_ids=episode_ids,
        )

    @property
    def templates(self) -> "_TemplatesNamespace":
        """Company-template operations: ``list``, ``show``, ``apply``."""
        return _TemplatesNamespace(self._engine)

    @property
    def targets(self) -> "_TargetsNamespace":
        """Company-target operations: ``show``, ``review``.

        Mission-targets task (05-19). Surfaces the founder / proposal /
        agreed triple and the team feasibility review hook.
        """
        return _TargetsNamespace(self._engine)

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


def _session_to_dict(session: Any) -> dict[str, Any]:
    """Serialize a ``ConversationSession`` to the channel parity dict.

    Same shape REST's ``_session_to_dict`` emits so SDK/REST stay
    key-identical for the channel surface (interfaces.md equivalence rule).
    """
    return {
        "session_id": session.id,
        "state": session.state.value,
        "route": session.route,
        "clarify_turns": session.clarify_turns,
        "created_at": str(session.created_at) if session.created_at is not None else None,
        "closed_at": str(session.closed_at) if session.closed_at is not None else None,
        "run_id": session.run_id,
        "directive_id": session.directive_id,
        "project_id": session.project_id,
        "approval_id": session.approval_id,
    }


def _turn_to_dict(turn: Any) -> dict[str, Any]:
    """Serialize a ``ConversationTurn`` to the channel parity dict."""
    return {
        "turn_index": turn.turn_index,
        "role": turn.role,
        "content": turn.content,
        "kind": turn.kind,
        "cost": turn.cost,
        "run_id": turn.run_id,
        "directive_id": turn.directive_id,
        "created_at": str(turn.created_at) if turn.created_at is not None else None,
    }


class _ChannelNamespace:
    """SDK sub-namespace for the CEO channel — a session object surface.

    The same conversation capabilities the REST ``/channel/*`` routes expose,
    as plain sync methods returning dicts (no Pydantic instances leak — SDK
    contract). ``send`` flattens a ``DirectiveResult`` via ``to_dict()`` so a
    caller can read ``result["session_id"]`` and pass it back on the next
    ``send`` to continue a clarify/gated session.
    """

    def __init__(self, engine: KompanyEngine):
        self._engine = engine

    def send(self, text: str, session_id: str | None = None) -> dict[str, Any]:
        """Founder message into the channel.

        Omit ``session_id`` to open a new session; pass it to continue an
        existing one (clarify reply / gated context). Returns the flattened
        result; ``result["status"]`` may be ``clarify`` (CEO asks back),
        ``gated`` (awaiting GO), or any execute/answer status.
        """
        return self._engine.process_directive(text, session_id=session_id).to_dict()

    def go(self, session_id: str) -> dict[str, Any]:
        """Founder GO on a threshold-gated session — execute the held directive."""
        return self._engine.channel_go(session_id).to_dict()

    def abandon(self, session_id: str) -> dict[str, Any]:
        """Abandon a session — close it without executing."""
        return self._engine.channel_abandon(session_id).to_dict()

    def sessions(
        self,
        state: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List channel sessions, newest first, optionally filtered by state."""
        limit = max(1, min(int(limit), 200))
        return [
            _session_to_dict(s)
            for s in self._engine.channel.list_sessions(state=state, limit=limit)
        ]

    def session(self, session_id: str) -> dict[str, Any] | None:
        """One session plus its ordered turns (the full thread).

        Returns ``None`` if the session is unknown.
        """
        session = self._engine.channel.get_session(session_id)
        if session is None:
            return None
        turns = self._engine.channel.session_turns(session_id)
        return {
            "session": _session_to_dict(session),
            "turns": [_turn_to_dict(t) for t in turns],
        }


class _TemplatesNamespace:
    """SDK sub-namespace exposing company-template operations."""

    def __init__(self, engine: KompanyEngine):
        self._engine = engine

    def list(self) -> list[dict[str, Any]]:
        """Return all available company templates as dicts."""
        return self._engine.list_templates()

    def show(self, template_id: str) -> dict[str, Any]:
        """Return one template's manifest + rendered mission body."""
        return self._engine.show_template(template_id)

    def apply(
        self,
        template_id: str,
        force: bool = False,
        override_budget: float | None = None,
        override_directive: str | None = None,
    ) -> dict[str, Any]:
        """Apply a template to the current company."""
        return self._engine.apply_template(
            template_id,
            force=force,
            override_budget=override_budget,
            override_directive=override_directive,
        )


class _TargetsNamespace:
    """SDK sub-namespace for the four-knob mission-targets contract.

    ``show()`` returns the founder / team_proposal / agreed triple plus
    the review approval id. ``review()`` re-runs the CEO+CFO+CoS
    feasibility pass and returns the freshly-created approval payload.
    """

    def __init__(self, engine: KompanyEngine):
        self._engine = engine

    def show(self) -> dict[str, Any]:
        """Return the three-state targets snapshot."""
        bundle = self._engine.get_targets_bundle()
        return {
            "founder": bundle.founder.model_dump(mode="json"),
            "proposal": (
                bundle.proposal.model_dump(mode="json")
                if bundle.proposal is not None
                else None
            ),
            "agreed": (
                bundle.agreed.model_dump(mode="json")
                if bundle.agreed is not None
                else None
            ),
            "review_thread_id": bundle.review_thread_id,
            "authoritative": self._engine.get_targets().model_dump(mode="json"),
        }

    def review(self) -> dict[str, Any] | None:
        """Kick off a fresh team feasibility review.

        Returns ``None`` if no founder targets are set yet (the founder
        must complete onboarding first).
        """
        return self._engine.run_target_feasibility_review()


class _GlossaryNamespace:
    """SDK sub-namespace for the company glossary CRUD surface.

    All methods round-trip through the engine so the audit log, the
    SSE event stream, and the CoS retrospective scanner see the same
    glossary the SDK caller does. Glossary-and-drift-detection task
    (05-19).
    """

    def __init__(self, engine: KompanyEngine):
        self._engine = engine

    def list(self) -> list[dict[str, Any]]:
        """Return every glossary entry."""
        return self._engine.list_glossary()

    def show(self, term: str) -> dict[str, Any] | None:
        """Look up one term (case-insensitive). Returns ``None`` if missing."""
        return self._engine.get_glossary_term(term)

    def add(
        self,
        term: str,
        definition: str,
        forbidden_synonyms: list[str] | None = None,
    ) -> dict[str, Any]:
        """Insert a brand-new glossary entry (founder-sourced)."""
        return self._engine.add_glossary_term(
            term=term,
            definition=definition,
            forbidden_synonyms=forbidden_synonyms,
            added_by="founder",
        )

    def update(
        self,
        term: str,
        definition: str | None = None,
        forbidden_synonyms: list[str] | None = None,
    ) -> dict[str, Any]:
        """Mutate an existing glossary entry's definition or synonyms."""
        return self._engine.update_glossary_term(
            term=term,
            definition=definition,
            forbidden_synonyms=forbidden_synonyms,
        )

    def remove(self, term: str) -> dict[str, Any]:
        """Drop a glossary entry. Returns ``{"removed": bool}``."""
        return {"removed": self._engine.remove_glossary_term(term)}


class _EpisodesNamespace:
    """SDK sub-namespace exposing episode operations on the engine."""

    def __init__(self, engine: KompanyEngine):
        self._engine = engine

    def list(
        self,
        retention_tier: str | None = None,
    ) -> list[dict[str, Any]]:
        """List materialized project episodes (no payload).

        ``retention_tier`` may be ``"full"`` or ``"summary"``.
        """
        return self._engine.list_episodes(retention_tier=retention_tier)

    def get(self, project_id: str) -> dict[str, Any] | None:
        """Fetch one episode including its ``payload_json`` string."""
        return self._engine.get_episode(project_id)

    def rebuild(self, project_id: str) -> dict[str, Any]:
        """Force re-materialization of a project's episode payload."""
        return self._engine.rebuild_episode(project_id)


class _HealthNamespace:
    """SDK sub-namespace exposing watchdog health-event operations."""

    def __init__(self, engine: KompanyEngine):
        self._engine = engine

    def list(
        self,
        status: str | None = None,
        kind: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List health events, newest-first.

        Filters: ``status`` in ``open|resolved|snoozed|dismissed``;
        ``kind`` in ``silent_run|recovered|retry_exhausted|stranded_in_progress|stranded_todo``.
        """
        return self._engine.list_health_events(
            status=status,
            kind=kind,
            project_id=project_id,
            limit=limit,
        )

    def get(self, event_id: str) -> dict[str, Any] | None:
        """Fetch one health event by id."""
        return self._engine.get_health_event(event_id)

    def resolve(
        self,
        event_id: str,
        action: str = "continue",
        snooze_minutes: int | None = None,
        resolved_by: str = "player",
    ) -> dict[str, Any] | None:
        """Apply a player action (``continue`` / ``snooze`` / ``dismiss``)."""
        return self._engine.resolve_health_event(
            event_id=event_id,
            action=action,
            snooze_minutes=snooze_minutes,
            resolved_by=resolved_by,
        )


class _ApprovalsNamespace:
    """SDK sub-namespace for the approval-thread RPG inbox actions."""

    def __init__(self, engine: KompanyEngine):
        self._engine = engine

    def show(self, approval_id: str) -> dict[str, Any] | None:
        """Return one approval with its full thread + comments."""
        return self._engine.get_approval(approval_id)

    def approve(
        self,
        approval_id: str,
        comment: str | None = None,
        approved_by: str = "master",
    ) -> dict[str, Any] | None:
        return self._engine.approve_request(
            approval_id,
            approved_by=approved_by,
            comment_body=comment,
        )

    def reject(
        self,
        approval_id: str,
        reason: str = "",
        comment: str | None = None,
        rejected_by: str = "master",
    ) -> dict[str, Any] | None:
        return self._engine.reject_request(
            approval_id,
            rejected_by=rejected_by,
            reason=reason or None,
            comment_body=comment,
        )

    def revise(
        self,
        approval_id: str,
        counter: str,
        comment: str | None = None,
        by_type: str = "user",
        by_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Player counter-proposal; returns ``{original, successor}``."""
        return self._engine.request_approval_revision(
            approval_id,
            counter=counter,
            by_type=by_type,
            by_id=by_id,
            comment_body=comment,
        )

    def snooze(
        self,
        approval_id: str,
        minutes: int,
        comment: str | None = None,
        by_type: str = "user",
        by_id: str | None = None,
    ) -> dict[str, Any] | None:
        return self._engine.snooze_approval(
            approval_id,
            minutes=minutes,
            by_type=by_type,
            by_id=by_id,
            comment_body=comment,
        )

    def cancel(
        self,
        approval_id: str,
        reason: str = "",
        comment: str | None = None,
        by_type: str = "user",
        by_id: str | None = None,
    ) -> dict[str, Any] | None:
        return self._engine.cancel_approval(
            approval_id,
            reason=reason or None,
            by_type=by_type,
            by_id=by_id,
            comment_body=comment,
        )

    def comment(
        self,
        approval_id: str,
        body: str,
        by_type: str = "user",
        by_id: str | None = None,
    ) -> dict[str, Any] | None:
        return self._engine.comment_on_approval(
            approval_id,
            body=body,
            by_type=by_type,
            by_id=by_id,
        )
