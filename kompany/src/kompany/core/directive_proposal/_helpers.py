"""Stateless helpers shared across directive proposal sub-mixins."""

from __future__ import annotations

import json
from typing import Any


class _DirectiveProposalHelpersMixin:
    """Pure-helper methods with no mutual dependencies."""

    def _active_provider_name(self) -> str:
        """Best-effort guess at which provider name is active for
        labelling errors. Reads vault-loaded settings; falls back to
        'unknown'. Display-only — never used for routing."""
        if getattr(self.settings, "custom_base_url", ""):
            return "custom"
        for name in ("anthropic", "openai", "gemini", "glm", "kimi"):
            attr = f"{name}_api_key"
            if getattr(self.settings, attr, ""):
                return name
        return "unknown"

    @staticmethod
    def _directive_to_dict(d) -> dict[str, Any]:
        """Coerce a ``_ProposedDirective`` Pydantic instance to the plain
        dict shape the rest of the engine + UI work with."""
        return {
            "title": getattr(d, "title", "") or "",
            "rationale": getattr(d, "rationale", "") or "",
            "proposer_role": (getattr(d, "proposer_role", "") or "ceo").lower(),
            "week_plan": list(getattr(d, "week_plan", []) or []),
            "success_metric": getattr(d, "success_metric", "") or "",
            "expected_cost_usd": float(getattr(d, "expected_cost_usd", 0.0) or 0.0),
            "other_agents_involved": [
                str(r).lower()
                for r in (getattr(d, "other_agents_involved", []) or [])
            ],
        }

    def _existing_draft_projects(self) -> list[dict[str, Any]]:
        """Return drafts already in the DB so the call is idempotent."""
        rows = self.db.execute(
            "SELECT id, name, type, plan "
            "FROM projects "
            "WHERE status = 'draft' "
            "ORDER BY created_at"
        ).fetchall()
        if not rows:
            return []
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                plan = json.loads(r["plan"] or "{}")
            except (TypeError, json.JSONDecodeError):
                plan = {}
            out.append({
                "id": r["id"],
                "name": r["name"],
                "type": r["type"],
                "status": "draft",
                "rationale": plan.get("rationale", ""),
                "proposer_role": plan.get("proposer_role", ""),
                "week_plan": plan.get("week_plan", []) or [],
                "success_metric": plan.get("success_metric", ""),
                "expected_cost_usd": float(plan.get("expected_cost_usd", 0.0) or 0.0),
                "other_agents_involved": plan.get("other_agents_involved", []) or [],
            })
        return out

    def _heuristic_first_directives(self, agreed) -> list[dict[str, Any]]:
        """Three safe defaults when LLM is unavailable / disabled.

        Each is intentionally generic enough to apply to any company
        shape; the LLM path is what produces founder-specific wording.
        Fields match the LLM schema 1:1 so the UI renders them
        identically — but the source marker upstream tells consumers
        these are starter-pack seeds, not real team output.
        """
        rev = float(agreed.revenue_target or 0)
        return [
            {
                "title": "Run 5 customer interviews to lock the ICP",
                "rationale": (
                    "Cheapest unknown-reducing move this week. Without "
                    "a sharp ICP, every later move spends money guessing. "
                    "5 calls reveal whether the founder's hypothesis "
                    "matches the market's pain."
                ),
                "proposer_role": "cpo",
                "week_plan": [
                    "Mon: CRO drafts a 4-question discovery script",
                    "Tue-Wed: founder books + runs 3 calls",
                    "Thu: runs 2 more calls; transcripts collected",
                    "Fri: CV writes a 1-page ICP synthesis",
                ],
                "success_metric": "5 interview write-ups + one ICP statement the team agrees with",
                "expected_cost_usd": 0.0,
                "other_agents_involved": ["cro", "cv"],
            },
            {
                "title": "Ship a landing page + waitlist (1 week)",
                "rationale": (
                    f"Concrete artefact starts moving toward the ${rev:,.0f} "
                    "revenue target. Even no purchases this week, a "
                    "waitlist gives the team an audience to test pricing + "
                    "messaging against in week 2."
                ),
                "proposer_role": "cmo",
                "week_plan": [
                    "Mon: CMO writes copy from ICP / value prop",
                    "Tue: CTO deploys static page (Vercel / Netlify)",
                    "Wed: CRO wires form → email capture",
                    "Thu: founder shares to 3 communities + LinkedIn",
                    "Fri: measure signup rate, log refusals",
                ],
                "success_metric": "≥ 30 waitlist signups OR a clear refusal pattern documented",
                "expected_cost_usd": 20.0,
                "other_agents_involved": ["cto", "cro"],
            },
            {
                "title": "Define pricing + payment rail (Stripe sandbox)",
                "rationale": (
                    "Clears the path for first paid customer without "
                    "committing real money. CFO sizes the unit economics "
                    "so pricing isn't pulled out of thin air."
                ),
                "proposer_role": "cro",
                "week_plan": [
                    "Mon: CFO models 3 pricing tiers vs cost-of-delivery",
                    "Tue: CRO picks recommended tier with rationale",
                    "Wed-Thu: CTO wires Stripe sandbox + test checkout",
                    "Fri: end-to-end dry run with founder as customer",
                ],
                "success_metric": "Stripe sandbox processes a test charge; pricing memo signed off",
                "expected_cost_usd": 0.0,
                "other_agents_involved": ["cfo", "cto"],
            },
        ]
