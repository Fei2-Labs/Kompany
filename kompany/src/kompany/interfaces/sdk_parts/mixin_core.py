"""KompanyCoreOps — core company + project methods for the SDK (ADR-0003)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kompany.core.engine import KompanyEngine

from .namespaces import _ChannelNamespace, _TemplatesNamespace, _TargetsNamespace


class KompanyCoreOps:
    """Mixin: company init, directive, channel, debate, projects, ledger."""

    _engine: "KompanyEngine"

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

    def debate(
        self, question: str, decision_type: str | None = None
    ) -> dict[str, Any]:
        """Run a full multi-agent debate on a strategic question.

        ADR-0006: pass ``decision_type`` (go_to_market|pricing|hiring|infra|
        legal|fundraising|product_scope|general) to convene the right
        executives and prime round 1 with frame challenges + recalled
        learnings. Omit it for the legacy stage-only debate.
        """
        stage = self._engine.settings.company_stage or "solo"
        # Resolve from the sdk module namespace so tests can monkeypatch
        # ``kompany.interfaces.sdk.DebateEngine``.
        from kompany.interfaces import sdk as _sdk

        debate_engine = _sdk.DebateEngine(
            self._engine.registry,
            stage=stage,
            memory=self._engine.memory,
            episodes=self._engine.episodes,
        )
        result = debate_engine.run(
            question=question,
            company_state=self._engine.get_company_state(),
            decision_type=decision_type,
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

    def abandon_project(self, project_id: str, reason: str = "") -> dict[str, Any]:
        """Abandon a plan: cancel project + tasks, withdraw cards, release envelope."""
        return self._engine.abandon_project(project_id, reason=reason)

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
