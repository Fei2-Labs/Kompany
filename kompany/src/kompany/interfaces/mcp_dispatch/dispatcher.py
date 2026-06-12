"""MCP tool dispatch — company, channel, project, and runtime tools.

Extracted mechanically from ``mcp_server.call_tool`` so the stdio MCP
server and the sidecar REST bridge (``mcp_bridge.py``) execute tools
through one shared code path — tool parity across transports is
structural, not maintained by hand.

:func:`dispatch_tool` is intentionally synchronous (engine work is
synchronous; async wrapping happens at the transport boundary) and
returns the plain JSON-able payload. Transports apply their own
serialization (the stdio server uses ``json.dumps(..., default=str)``).

This package must stay importable without the optional ``mcp`` SDK:
the PyInstaller sidecar bundle ships the REST surface only.
"""

from __future__ import annotations

from typing import Any

from kompany.core.engine import KompanyEngine
from kompany.interfaces.mcp_dispatch.governance import dispatch_governance_tool


def _session_to_dict(session: Any) -> dict[str, Any]:
    """Serialize a ``ConversationSession`` to the channel parity dict.

    Same shape REST/SDK emit so the channel surface stays key-identical
    across interfaces (interfaces.md equivalence rule).
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


def dispatch_tool(engine: KompanyEngine, name: str, arguments: dict) -> Any:
    """Execute one MCP tool against ``engine`` and return its payload.

    Raises :class:`UnknownToolError` for unrecognized tool names.
    """
    if name == "kompany_init":
        engine.initialize_company(
            name=arguments["name"],
            capital=arguments.get("capital", 0.0),
            goal=arguments.get("goal", ""),
            time_horizon=arguments.get("time_horizon", ""),
            exclusions=arguments.get("exclusions", ""),
        )
        return {
            "status": "initialized",
            "name": arguments["name"],
            "capital": arguments.get("capital", 0.0),
            "goal": arguments.get("goal", ""),
            "time_horizon": arguments.get("time_horizon", ""),
            "exclusions": arguments.get("exclusions", ""),
            "stage": "solo",
        }

    if name == "kompany_directive":
        result = engine.process_directive(
            arguments["text"],
            session_id=arguments.get("session_id"),
        )
        return result.to_dict()

    if name == "kompany_channel_sessions":
        limit = max(1, min(int(arguments.get("limit", 50)), 200))
        sessions = engine.channel.list_sessions(
            state=arguments.get("state"),
            limit=limit,
        )
        return {"sessions": [_session_to_dict(s) for s in sessions]}

    if name == "kompany_channel_session":
        session = engine.channel.get_session(arguments["session_id"])
        if session is None:
            return (
                {"error": f"Unknown channel session {arguments['session_id']!r}"}
            )
        turns = engine.channel.session_turns(arguments["session_id"])
        return {
            "session": _session_to_dict(session),
            "turns": [_turn_to_dict(t) for t in turns],
        }

    if name == "kompany_channel_go":
        return engine.channel_go(arguments["session_id"]).to_dict()

    if name == "kompany_channel_abandon":
        return engine.channel_abandon(arguments["session_id"]).to_dict()

    if name == "kompany_status":
        # Canonical dict shared by all four interfaces — core/status_ops.py.
        from kompany.core.status_ops import build_status

        return build_status(engine)

    if name == "kompany_override":
        return engine.process_override(arguments["text"])

    if name == "kompany_decision_packet":
        return engine.prepare_decision_packet(
            arguments["text"],
            target_amount=arguments.get("target_amount"),
        )

    if name == "kompany_projects":
        active = engine.projects.list_active()
        return [
            {
                "id": p.id, "name": p.name,
                "type": p.type.value, "status": p.status.value,
                "target_amount": p.target_amount,
                "funded_amount": p.funded_amount,
            }
            for p in active
        ]

    if name == "kompany_agent_work_summary":
        return engine.agent_work_summary()

    if name == "kompany_channels_status":
        return engine.channels_status()

    if name == "kompany_channels_outbox":
        return engine.outbox_list(limit=int(arguments.get("limit", 20)))

    if name == "kompany_anima_state":
        return engine.anima_state()

    if name == "kompany_anima_diary":
        return engine.anima_diary_list(limit=int(arguments.get("limit", 30)))

    if name == "kompany_project":
        p = engine.projects.get(arguments["project_id"])
        if not p:
            return {"error": f"Project '{arguments['project_id']}' not found"}
        tasks = engine.projects.list_tasks(p.id)
        return {
            "id": p.id, "name": p.name,
            "type": p.type.value, "status": p.status.value,
            "target_amount": p.target_amount,
            "funded_amount": p.funded_amount,
            "plan": p.plan, "assigned_agents": p.assigned_agents,
            "tasks": [
                {"id": t.id, "title": t.title, "agent": t.assigned_agent, "status": t.status.value}
                for t in tasks
            ],
        }

    if name == "kompany_ledger":
        entries = engine.ledger.get_recent(limit=arguments.get("limit", 10))
        return entries

    if name == "kompany_debate":
        from kompany.core.debate import DebateEngine
        stage = engine.settings.company_stage or "solo"
        de = DebateEngine(engine.registry, stage=stage)
        result = de.run(
            question=arguments["question"],
            company_state=engine.get_company_state(),
        )
        return {
            "question": result.question,
            "rounds": [[pos.model_dump() for pos in rnd] for rnd in result.rounds],
            "synthesis": result.synthesis.model_dump() if result.synthesis else None,
            "decision": result.decision.model_dump() if result.decision else None,
        }

    if name == "kompany_execute":
        result = engine.execute_project(arguments["project_id"])
        return result

    if name == "kompany_resume_project":
        try:
            result = engine.resume_project(arguments["project_id"])
            return result
        except ValueError as exc:
            return {"error": str(exc)}

    if name == "kompany_execute_decision_packet":
        try:
            result = engine.execute_decision_packet(arguments["approval_id"])
            return result
        except ValueError as exc:
            return {"error": str(exc)}

    if name == "kompany_remote_command":
        return engine.handle_remote_command({
            "source": arguments.get("source", "mobile"),
            "text": arguments["text"],
            "chat_id": arguments.get("chat_id", ""),
            "bearer_token": arguments.get("bearer_token", ""),
            "payload": arguments.get("payload", {}),
        })

    if name == "kompany_remote_replay_cleanup":
        return engine.cleanup_remote_replays(
            ttl_seconds=arguments.get("ttl_seconds"),
        )

    if name == "kompany_observability":
        return engine.observability_snapshot()

    if name == "kompany_runtime_status":
        return engine.get_runtime_state()

    if name == "kompany_heartbeat":
        return engine.heartbeat_once(
            dispatch=arguments.get("dispatch", False),
            adapter=arguments.get("adapter", "dry-run"),
        )

    if name == "kompany_notifications_dispatch":
        return engine.dispatch_notifications(
            arguments.get("events", []),
            adapter=arguments.get("adapter", "dry-run"),
        )

    if name == "kompany_runtime_suspend":
        return engine.suspend(reason=arguments.get("reason", "manual"))

    if name == "kompany_runtime_resume":
        return engine.resume()

    if name == "kompany_backup_create":
        return engine.create_backup(label=arguments.get("label", "manual"))

    if name == "kompany_backups":
        return engine.list_backups()

    if name == "kompany_backup_restore":
        try:
            return engine.restore_backup(arguments["backup_id"])
        except FileNotFoundError as exc:
            return {"error": str(exc)}

    if name == "kompany_retrospective":
        return engine.run_retrospective(arguments["project_id"])

    if name == "kompany_health_list":
        try:
            return engine.list_health_events(
                status=arguments.get("status"),
                kind=arguments.get("kind"),
                project_id=arguments.get("project_id"),
                limit=arguments.get("limit", 100),
            )
        except ValueError as exc:
            return {"error": str(exc)}

    if name == "kompany_health_get":
        row = engine.get_health_event(arguments["event_id"])
        return row or {"error": f"Health event not found: {arguments['event_id']}"}

    if name == "kompany_health_resolve":
        try:
            row = engine.resolve_health_event(
                event_id=arguments["event_id"],
                action=arguments["action"],
                snooze_minutes=arguments.get("snooze_minutes"),
                resolved_by=arguments.get("resolved_by", "player"),
            )
        except ValueError as exc:
            return {"error": str(exc)}
        return row or {"error": f"Health event not found: {arguments['event_id']}"}

    if name == "kompany_episodes_list":
        return engine.list_episodes(
            retention_tier=arguments.get("retention_tier"),
        )

    if name == "kompany_episodes_get":
        row = engine.get_episode(arguments["project_id"])
        return row or {"error": f"Episode not found: {arguments['project_id']}"}

    if name == "kompany_episodes_rebuild":
        try:
            return engine.rebuild_episode(arguments["project_id"])
        except LookupError as exc:
            return {"error": str(exc)}

    if name == "kompany_memories":
        return engine.list_memories(
            arguments["agent_role"],
            limit=arguments.get("limit", 20),
            include_stale=arguments.get("include_stale", False),
            knowledge_type=arguments.get("knowledge_type"),
            category=arguments.get("category"),
        )

    if name == "kompany_distill":
        from datetime import timedelta

        since_text = arguments.get("since")
        window: timedelta | None = None
        if since_text:
            text = str(since_text).strip().lower()
            units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
            try:
                if text and text[-1] in units:
                    window = timedelta(seconds=float(text[:-1]) * units[text[-1]])
                elif text:
                    window = timedelta(seconds=float(text))
            except ValueError:
                return {"error": f"Invalid 'since' value: {since_text!r}"}
        try:
            return engine.distill(
                since=window,
                dry_run=bool(arguments.get("dry_run", False)),
                episode_ids=arguments.get("episode_ids"),
            )
        except ValueError as exc:
            return {"error": str(exc)}

    if name == "kompany_release_delivery":
        try:
            result = engine.release_delivery(arguments["approval_id"])
            return result
        except ValueError as exc:
            return {"error": str(exc)}

    return dispatch_governance_tool(engine, name, arguments)
