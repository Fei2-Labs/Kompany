"""UI preferences, answer context, glossary + drift scan.

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations

from typing import Any

from kompany.core.answer_context import compose_answer_context
from kompany.state.models import ApprovalRequest
from kompany.state.targets import compose_summary as compose_targets_summary
from kompany.state.ui_preferences import (
    UIPreferences,
    get_preferences as get_ui_preferences,
    set_preferences as set_ui_preferences,
)



class GovernanceMixin:
    # ------------------------------------------------------------------
    # UI preferences (theme system, feature A — 05-27)
    # ------------------------------------------------------------------

    def get_ui_preferences(self) -> UIPreferences:
        """Return the founder's dashboard appearance preferences (or defaults)."""
        return get_ui_preferences(self.db)

    def set_ui_preferences(
        self,
        *,
        theme_id: str | None = None,
        auto_enabled: bool | None = None,
        reduce_motion: str | None = None,
    ) -> UIPreferences:
        """Patch UI preferences; ``ValueError`` on a bad ``reduce_motion``."""
        prefs = set_ui_preferences(
            self.db,
            theme_id=theme_id,
            auto_enabled=auto_enabled,
            reduce_motion=reduce_motion,
        )
        self.audit.record(
            event_type="preferences.updated",
            action="Updated UI preferences",
            detail=prefs.model_dump(mode="json"),
        )
        return prefs

    def _compose_targets_summary(self) -> str:
        """One-paragraph string injected into CEO/CFO/CoS system prompts.

        Reads the authoritative targets + current cash so the agent sees
        the same numbers the watchdog uses to fire ``runway_alert``.
        """
        try:
            cash = self.ledger.get_balance()
        except Exception:  # pragma: no cover — ledger errors don't kill prompts
            cash = None
        return compose_targets_summary(self.get_targets(), cash=cash)

    def _compose_glossary_summary(self) -> str:
        """Render the company glossary as a system-prompt block.

        Returns ``""`` when the glossary is empty so callers can splice
        the value into prompts unconditionally without an awkward blank
        section. Used by CEO classify / CFO target review / CoS distill
        / CoS retrospect prompts. Reads via the cached
        :class:`GlossaryService` so concurrent edits don't tear the snapshot.

        Glossary-and-drift-detection task 05-19.
        """
        try:
            glossary = self.glossary.load()
        except Exception:  # pragma: no cover — never let glossary kill prompts
            return ""
        return glossary.compose_summary()

    def _compose_answer_context(self) -> tuple[str, bool]:
        """Bounded real-state snapshot for the CEO ``answer`` route (PR7/PR8)."""
        return compose_answer_context(
            self,
            targets_summary=self._compose_targets_summary(),
        )

    # ------------------------------------------------------------------
    # Company glossary (glossary-and-drift-detection task 05-19)
    # ------------------------------------------------------------------

    def list_glossary(self) -> list[dict[str, Any]]:
        """Return every glossary entry as a JSON-ready dict."""
        return [
            entry.model_dump(mode="json") for entry in self.glossary.list_terms()
        ]

    def get_glossary_term(self, term: str) -> dict[str, Any] | None:
        """Look up one term (case-insensitive). Returns ``None`` if missing."""
        entry = self.glossary.get(term)
        return entry.model_dump(mode="json") if entry is not None else None

    def add_glossary_term(
        self,
        term: str,
        definition: str,
        forbidden_synonyms: list[str] | None = None,
        added_by: str = "founder",
        source_episode_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new glossary term. Raises ``ValueError`` if it exists."""
        # The service uses Literal["founder","cos_proposal","template"]; we
        # validate the string here so callers get a clear error instead of
        # a deep Pydantic ValidationError.
        valid_sources = {"founder", "cos_proposal", "template"}
        if added_by not in valid_sources:
            raise ValueError(
                f"added_by must be one of {sorted(valid_sources)}, got {added_by!r}"
            )
        entry = self.glossary.add(
            term=term,
            definition=definition,
            forbidden_synonyms=forbidden_synonyms,
            added_by=added_by,  # type: ignore[arg-type]
            source_episode_id=source_episode_id,
        )
        self.audit.record(
            event_type="glossary.term_added",
            action=f"Added glossary term {entry.term!r}",
            detail={
                "term": entry.term,
                "added_by": entry.added_by,
                "forbidden_synonyms": entry.forbidden_synonyms,
                "source_episode_id": entry.source_episode_id,
            },
        )
        return entry.model_dump(mode="json")

    def update_glossary_term(
        self,
        term: str,
        definition: str | None = None,
        forbidden_synonyms: list[str] | None = None,
    ) -> dict[str, Any]:
        """Mutate an existing glossary entry."""
        entry = self.glossary.update(
            term,
            definition=definition,
            forbidden_synonyms=forbidden_synonyms,
        )
        self.audit.record(
            event_type="glossary.term_updated",
            action=f"Updated glossary term {entry.term!r}",
            detail={
                "term": entry.term,
                "definition": entry.definition,
                "forbidden_synonyms": entry.forbidden_synonyms,
            },
        )
        return entry.model_dump(mode="json")

    def remove_glossary_term(self, term: str) -> bool:
        """Drop one glossary term. Returns ``True`` if a row was deleted."""
        removed = self.glossary.remove(term)
        if removed:
            self.audit.record(
                event_type="glossary.term_removed",
                action=f"Removed glossary term {term!r}",
                detail={"term": term},
            )
        return removed

    def _runway_snapshot(self) -> dict[str, Any] | None:
        """Build the dict the watchdog's runway scanner consumes.

        Returns ``None`` when no agreed/founder deadline is set, when the
        ledger or targets table is unreachable, or when burn rate hasn't
        produced enough signal yet. The watchdog treats ``None`` as
        "skip this tick"; it never raises.
        """
        try:
            targets = self.get_targets()
        except Exception:  # noqa: BLE001
            return None
        if not targets.deadline:
            return None
        try:
            cash = self.ledger.get_balance()
        except Exception:  # noqa: BLE001
            cash = 0.0
        try:
            burn_rate = self.ledger.recent_burn_rate(window_hours=24)
        except Exception:  # noqa: BLE001
            burn_rate = 0.0
        return {
            "cash": float(cash),
            "burn_rate": float(burn_rate),
            "deadline": targets.deadline,
            "targets": targets.model_dump(mode="json"),
        }

    def _finalize_glossary_review(
        self,
        request: ApprovalRequest,
        *,
        outcome: str,
    ) -> None:
        """Post-resolve hook for ``glossary_review`` approvals.

        On approve → close the matching ``glossary_drift_alert`` health
        events as resolved and write a ``glossary.drift_resolved`` audit
        event. No glossary mutation occurs by default — the founder is
        acknowledging that the drift was real and the canonical terms
        are correct as-is; if they wanted to change the canonical word
        they would edit the glossary directly.

        On reject → also close the health events, but the audit detail
        tags the outcome as ``"dismissed_false_positive"`` so distillation
        learns this synonym pair is fine in practice.
        """
        payload = request.payload or {}
        project_id = request.project_id
        drift_count = 0
        drifts = payload.get("drifts")
        if isinstance(drifts, list):
            drift_count = len(drifts)

        # Close matching open ``glossary_drift_alert`` rows. We match by
        # ``project_id`` + ``approval_id`` to avoid clobbering unrelated
        # drift alerts on the same project.
        closed = 0
        if project_id is not None:
            try:
                events = self.health_events.list_for_project(project_id)
            except Exception:  # pragma: no cover - defensive
                events = []
            for ev in events:
                if ev.get("kind") != "glossary_drift_alert":
                    continue
                if ev.get("status") != "open":
                    continue
                detail = ev.get("detail") if isinstance(ev.get("detail"), dict) else {}
                if detail.get("approval_id") and detail.get("approval_id") != request.id:
                    continue
                try:
                    self.health_events.resolve(
                        event_id=ev["id"],
                        action="continue" if outcome == "approved" else "dismiss",
                        resolved_by="founder",
                    )
                    closed += 1
                except Exception:  # pragma: no cover - defensive
                    continue

        self.audit.record(
            event_type=(
                "glossary.drift_resolved"
                if outcome == "approved"
                else "glossary.drift_dismissed"
            ),
            action=(
                f"Founder {'accepted' if outcome == 'approved' else 'dismissed'} "
                f"{drift_count} drift hit(s) for approval {request.id}"
            ),
            detail={
                "approval_id": request.id,
                "outcome": outcome,
                "drift_count": drift_count,
                "events_closed": closed,
            },
            project_id=project_id,
        )

    # ------------------------------------------------------------------
    # Glossary drift scan + approval (glossary-and-drift-detection 05-19)
    # ------------------------------------------------------------------

    def _run_glossary_drift_scan(
        self,
        *,
        project_id: str,
        reflections: list[Any],
    ) -> dict[str, Any] | None:
        """Detect glossary drift on the just-finished retrospective output.

        Pulls the audit events tied to this project (for stringified
        ``detail`` scans) and the project's decisions, then defers to
        :func:`kompany.agents.cos_glossary_scan.scan_drift`.

        If any drift is detected:

        * Write one ``glossary_drift_alert`` health event via
          :meth:`Watchdog.record_glossary_drift`.
        * Create one ``approval_request(action_type='glossary_review')``
          carrying ``drifts`` + ``suggested_corrections``.
        * Mirror an audit event for the timeline.

        Returns a summary dict ``{"drift_count", "approval_id"}`` or
        ``None`` when the scan was a no-op (empty glossary or zero hits).
        """
        from kompany.agents.cos_glossary_scan import (
            build_suggested_corrections,
            scan_drift,
        )

        glossary = self.glossary.load()
        if len(glossary) == 0:
            return None

        # Pull decisions and audit events tied to the project. We use the
        # raw DB rows (rather than the materialised episode payload)
        # because materialisation hasn't happened yet — that's the point
        # of running the scan first.
        decisions_rows = self.db.execute(
            "SELECT id, agents_involved, result FROM decisions "
            "WHERE result LIKE ? ORDER BY timestamp",
            (f"%{project_id}%",),
        ).fetchall()
        decisions: list[dict[str, Any]] = []
        for row in decisions_rows:
            try:
                result_obj = (
                    __import__("json").loads(row["result"]) if row["result"] else {}
                )
            except (TypeError, ValueError):
                result_obj = {}
            try:
                agents_involved = __import__("json").loads(row["agents_involved"])
                if not isinstance(agents_involved, list):
                    agents_involved = []
            except (TypeError, ValueError):
                agents_involved = []
            summary_text = (
                result_obj.get("message")
                if isinstance(result_obj, dict) and "message" in result_obj
                else str(result_obj)
            )
            decisions.append({
                "summary": str(summary_text or ""),
                "agents_involved": agents_involved,
            })

        audit_rows = self.db.execute(
            "SELECT event_type, detail FROM audit_log WHERE project_id = ? "
            "ORDER BY id",
            (project_id,),
        ).fetchall()
        audit_events: list[dict[str, Any]] = []
        for row in audit_rows:
            detail_obj: dict[str, Any] = {}
            if row["detail"]:
                try:
                    parsed = __import__("json").loads(row["detail"])
                    if isinstance(parsed, dict):
                        detail_obj = parsed
                    else:
                        detail_obj = {"value": parsed}
                except (TypeError, ValueError):
                    detail_obj = {"raw": row["detail"]}
            audit_events.append({
                "type": row["event_type"],
                "detail": detail_obj,
            })

        drifts = scan_drift(
            glossary=glossary,
            reflections=reflections,
            decisions=decisions,
            audit_events=audit_events,
        )
        if not drifts:
            return None

        suggestions = build_suggested_corrections(drifts, glossary)
        payload = {
            "project_id": project_id,
            "drifts": [d.model_dump(mode="json") for d in drifts],
            "suggested_corrections": suggestions,
        }
        # Build a short founder-facing summary line for the inbox.
        roles_seen = ", ".join(sorted({d.agent_role for d in drifts}))
        summary = (
            f"Glossary drift in episode {project_id}: "
            f"{len(drifts)} hit(s) across {roles_seen or 'unknown agents'}."
        )
        approval = self.approvals.create(
            ApprovalRequest(
                action_type="glossary_review",
                summary=summary,
                payload=payload,
                project_id=project_id,
                severity="medium",
                requested_by="cos",
            )
        )
        # Record the matching health event after the approval exists so we
        # can cross-link them in both directions.
        self.watchdog.record_glossary_drift(
            episode_id=project_id,
            drifts=drifts,
            project_id=project_id,
            approval_id=approval.id,
        )
        self.audit.record(
            event_type="glossary.drift_detected",
            action=f"CoS detected {len(drifts)} glossary drift hit(s)",
            detail={
                "project_id": project_id,
                "drift_count": len(drifts),
                "approval_id": approval.id,
                "roles": sorted({d.agent_role for d in drifts}),
            },
            project_id=project_id,
        )
        return {
            "drift_count": len(drifts),
            "approval_id": approval.id,
        }

    def _glossary_review_revision_handler(
        self,
        original: ApprovalRequest,
        hint: str,
    ) -> ApprovalRequest:
        """Founder counter-proposal on a glossary drift alert.

        Copies the payload, stamps the hint into ``revision_hint`` and
        re-issues as ``pending``. The founder can then approve a slimmer
        list ("just the customer correction, drop the MRR one") on the
        next pass. We deliberately don't try to parse partial-accept
        instructions from free-form text — the founder gets another
        round of approve / reject / revise on the successor.
        """
        new_payload = {**(original.payload or {}), "revision_hint": hint}
        successor = ApprovalRequest(
            action_type=original.action_type,
            summary=f"[Revised] {original.summary}",
            payload=new_payload,
            directive_id=original.directive_id,
            project_id=original.project_id,
            requested_by=original.requested_by,
            severity=original.severity,
            predecessor_id=original.id,
        )
        return self.approvals.create(successor)

