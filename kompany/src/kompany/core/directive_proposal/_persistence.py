"""Persistence and founder-rule filtering for directive proposals."""

from __future__ import annotations

from typing import Any

from kompany.state.models import Project, ProjectType


class _DirectiveProposalPersistenceMixin:
    """Persistence helpers: write draft projects + founder-rule filter."""

    def _filter_by_founder_rules(
        self, directives: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Founder hard rules (#6) — deterministic proposal-time filter.

        Drops any proposed directive whose title/rationale/week_plan
        matches an ``exclude_capability`` rule, so the team never debates
        (or persists) work the founder will never allow. Backstops the
        NEVER clause appended to the CEO prompt."""
        from kompany.core import founder_config

        rules = getattr(self, "get_founder_rules", lambda: None)()
        kept, dropped = founder_config.filter_directive_dicts(directives, rules)
        if dropped:
            try:
                self.audit.record(
                    "founder_rules.directives_filtered",
                    f"Founder hard rules dropped {len(dropped)} proposed directive(s)",
                    detail={
                        "dropped_titles": [d.get("title") for d in dropped],
                        "excluded": founder_config.excluded_capabilities(rules),
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort
                pass
        return kept

    def _persist_proposed_directives(
        self,
        directives: list[dict[str, Any]],
        *,
        source: str = "team_proposal_first_week",
    ) -> list[dict[str, Any]]:
        """Write each proposed directive as a status='draft' project,
        then return ``[{id, name, type, status, rationale, proposer}]``
        rows the REST layer + UI can render directly.

        ``source`` distinguishes team-LLM directives from heuristic
        starter packs so downstream consumers (distillation, audit
        timeline) can tell them apart."""
        rows: list[dict[str, Any]] = []
        for d in directives[:3]:
            project = Project(
                name=d["title"][:120],
                type=ProjectType.OPERATIONAL,
                plan={
                    "suggested_directive": d["title"],
                    "rationale": d.get("rationale", ""),
                    "proposer_role": d.get("proposer_role", "ceo"),
                    "week_plan": d.get("week_plan", []) or [],
                    "success_metric": d.get("success_metric", ""),
                    "expected_cost_usd": float(d.get("expected_cost_usd", 0.0) or 0.0),
                    "other_agents_involved": d.get("other_agents_involved", []) or [],
                    "source": source,
                },
                assigned_agents=list(d.get("other_agents_involved", []) or []),
            )
            # Reuse the Templates helper's raw insert so the draft row
            # ends up with status='draft' just like template-staged
            # directives. _insert_draft_project lives on the Templates
            # service.
            self.templates._insert_draft_project(project, d["title"])
            rows.append({
                "id": project.id,
                "name": project.name,
                "type": project.type.value,
                "status": "draft",
                "rationale": d.get("rationale", ""),
                "proposer_role": d.get("proposer_role", "ceo"),
                "week_plan": d.get("week_plan", []) or [],
                "success_metric": d.get("success_metric", ""),
                "expected_cost_usd": float(d.get("expected_cost_usd", 0.0) or 0.0),
                "other_agents_involved": d.get("other_agents_involved", []) or [],
            })

        # Audit event so the action is visible in the timeline.
        try:
            self.audit.record(
                event_type="first_directive_proposal",
                action=f"Team proposed {len(rows)} first-week directives",
                detail={
                    "count": len(rows),
                    "proposers": [r.get("proposer_role") for r in rows],
                },
            )
        except Exception:  # noqa: BLE001 — best-effort
            pass

        return rows
