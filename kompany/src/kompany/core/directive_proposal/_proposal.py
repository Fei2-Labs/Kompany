"""propose_first_directives public method + _llm_first_directives helper."""

from __future__ import annotations

from typing import Any

from kompany.state.targets import get_state as get_targets_state

from ._models import PROMPT_TEMPLATE, _ProposedDirectiveList


class _DirectiveProposalMixin:
    """``KompanyEngine`` mixin — team-generated first-week directives."""

    def propose_first_directives(
        self,
        *,
        skip_llm: bool | None = None,
        force_heuristic: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        """Read agreed_targets + company state, run a short CEO pass,
        write 3 draft projects, return a structured result. Idempotent.

        Returns a dict with shape::

            {
                "status": "ok" | "team_failed" | "no_targets" | "heuristic",
                "directives": [...],     # may be empty when status != ok
                "error_code": str|None,  # network / unauthorized /
                                         # rate_limited / provider_error
                                         # / unknown; only set on
                                         # team_failed
                "error_message": str|None,
                "provider": str|None,    # which provider was tried
            }

        Distinct from the previous silent-fallback shape — the caller
        (REST endpoint + onboarding UI) needs to know whether the
        AI actually proposed the directives or whether we fell through
        to generic seeds. Lying to the founder ("here's your AI's
        plan!") when the LLM never ran erodes trust. ``force_heuristic``
        explicit-opts into the local fallback (user clicked "use
        starter pack" on the error screen).

        ``skip_llm`` short-circuits to the heuristic without trying
        the LLM; defaults to True under ``KOMPANY_TEST_MODE=1`` so
        tests don't need a live API key.
        """
        import os

        if force:
            # Founder explicitly asked for a fresh proposal — wipe
            # existing drafts so the LLM regenerates instead of
            # short-circuiting on idempotency.
            try:
                self.db.execute("DELETE FROM projects WHERE status = 'draft'")
                self.db.commit()
            except Exception:  # noqa: BLE001 — best-effort
                pass

        existing = self._existing_draft_projects()
        if existing:
            return {
                "status": "ok",
                "directives": existing,
                "error_code": None,
                "error_message": None,
                "provider": None,
            }

        agreed = get_targets_state(self.db, "agreed")
        if agreed is None:
            return {
                "status": "no_targets",
                "directives": [],
                "error_code": "no_targets",
                "error_message": "Agreed targets not set; complete the team review first.",
                "provider": None,
            }

        if skip_llm is None:
            skip_llm = os.environ.get("KOMPANY_TEST_MODE", "") == "1"

        provider = self._active_provider_name()

        if skip_llm or force_heuristic:
            directives = self._filter_by_founder_rules(
                self._heuristic_first_directives(agreed)
            )
            persisted = self._persist_proposed_directives(
                directives, source="team_proposal_first_week_heuristic"
            )
            return {
                "status": "heuristic",
                "directives": persisted,
                "error_code": None,
                "error_message": None,
                "provider": provider,
            }

        try:
            directives = self._filter_by_founder_rules(
                self._llm_first_directives(agreed)
            )
        except Exception as exc:  # noqa: BLE001 — surfaced to UI
            from kompany.interfaces.api import _classify_ping_error

            detail = f"{type(exc).__name__}: {exc}"
            code = _classify_ping_error(detail)
            return {
                "status": "team_failed",
                "directives": [],
                "error_code": code,
                "error_message": detail,
                "provider": provider,
            }

        persisted = self._persist_proposed_directives(directives)
        return {
            "status": "ok",
            "directives": persisted,
            "error_code": None,
            "error_message": None,
            "provider": provider,
        }

    def _llm_first_directives(self, agreed) -> list[dict[str, Any]]:
        from kompany.core.debate import CLAIMS_SCHEMA_HINT  # noqa: F401
        from kompany.core.founder_config import never_propose_clause

        ceo = self.registry.get(
            "ceo", company_state=self.get_company_state()
        )
        prompt = PROMPT_TEMPLATE.format(
            initial_budget=float(agreed.initial_budget or 0),
            revenue_target=float(agreed.revenue_target or 0),
            customer_target=(
                "not set"
                if agreed.customer_target is None
                else str(agreed.customer_target)
            ),
            deadline=str(agreed.deadline or "not set"),
            company_goal=self.settings.company_goal or "(none provided)",
        )
        # Founder hard rules (#6): excluded capabilities are stated up
        # front so the team doesn't waste tokens proposing them.
        prompt += never_propose_clause(
            getattr(self, "get_founder_rules", lambda: None)()
        )
        resp = ceo.call_structured(
            prompt=prompt,
            output_schema=_ProposedDirectiveList,
            max_tokens=900,
            action_type="first_directive_proposal",
        )
        parsed = getattr(resp, "parsed", None)
        items = list(getattr(parsed, "directives", []) or [])
        if not items:
            raise ValueError("LLM returned zero directives")
        out: list[dict[str, Any]] = []
        for d in items[:3]:
            out.append(self._directive_to_dict(d))
        return out
