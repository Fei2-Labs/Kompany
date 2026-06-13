"""Namespace sub-objects for the Kompany SDK (ADR-0003 split)."""

from __future__ import annotations

from typing import Any

from kompany.core.engine import KompanyEngine

from .helpers import _session_to_dict, _turn_to_dict


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
