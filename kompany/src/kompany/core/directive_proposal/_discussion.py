"""discuss_first_directives public method."""

from __future__ import annotations

from typing import Any

from kompany.state.targets import get_state as get_targets_state

from ._models import DISCUSSION_PROMPT_TEMPLATE, _DiscussionResponse


class _DirectiveDiscussionMixin:
    """``KompanyEngine`` mixin — CEO follow-up Q&A on first directives."""

    def discuss_first_directives(self, question: str) -> dict[str, Any]:
        """Founder follow-up Q&A on the current first-week directives.

        Loads agreed_targets + the current draft directives, runs ONE
        CEO LLM call, returns ``{ status, answer, directives_changed,
        directives, error_code, error_message, provider }``.

        When the CEO decides the question warrants a revised plan
        (``directives_changed=True``), the existing drafts are deleted
        and the new ones persisted with source
        ``team_proposal_first_week_revised`` so the timeline shows the
        founder's Q&A triggered the change.
        """
        provider = self._active_provider_name()

        question = (question or "").strip()
        if not question:
            return {
                "status": "team_failed",
                "answer": "",
                "directives_changed": False,
                "directives": [],
                "error_code": "empty_question",
                "error_message": "Question is empty.",
                "provider": provider,
            }

        agreed = get_targets_state(self.db, "agreed")
        if agreed is None:
            return {
                "status": "no_targets",
                "answer": "",
                "directives_changed": False,
                "directives": [],
                "error_code": "no_targets",
                "error_message": "Agreed targets not set; complete the team review first.",
                "provider": provider,
            }
        existing = self._existing_draft_projects()
        if not existing:
            return {
                "status": "no_directives",
                "answer": "",
                "directives_changed": False,
                "directives": [],
                "error_code": "no_directives",
                "error_message": "No draft directives to discuss yet.",
                "provider": provider,
            }

        try:
            ceo = self.registry.get(
                "ceo", company_state=self.get_company_state()
            )
            existing_block = "\n".join(
                f"  {i+1}. [{(d.get('proposer_role') or 'ceo').upper()}] "
                f"{d['name']} — {d.get('rationale','')}"
                for i, d in enumerate(existing)
            )
            prompt = DISCUSSION_PROMPT_TEMPLATE.format(
                existing_directives=existing_block,
                question=question,
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
            resp = ceo.call_structured(
                prompt=prompt,
                output_schema=_DiscussionResponse,
                max_tokens=1200,
                action_type="first_directive_discussion",
            )
        except Exception as exc:  # noqa: BLE001
            from kompany.interfaces.api import _classify_ping_error

            detail = f"{type(exc).__name__}: {exc}"
            return {
                "status": "team_failed",
                "answer": "",
                "directives_changed": False,
                "directives": [],
                "error_code": _classify_ping_error(detail),
                "error_message": detail,
                "provider": provider,
            }

        parsed = getattr(resp, "parsed", None)
        answer = (getattr(parsed, "answer", "") or "").strip()
        changed = bool(getattr(parsed, "directives_changed", False))
        new_directives_raw = list(getattr(parsed, "directives", []) or [])

        if changed and new_directives_raw:
            # Replace the old drafts.
            self.db.execute("DELETE FROM projects WHERE status = 'draft'")
            self.db.commit()
            new_dicts = [self._directive_to_dict(d) for d in new_directives_raw[:3]]
            persisted = self._persist_proposed_directives(
                new_dicts, source="team_proposal_first_week_revised"
            )
        else:
            persisted = existing
            changed = False

        try:
            self.audit.record(
                event_type="first_directive_discussion",
                action="Founder asked a follow-up about first directives",
                detail={
                    "question": question[:200],
                    "directives_changed": changed,
                },
            )
        except Exception:  # noqa: BLE001 — best-effort
            pass

        return {
            "status": "ok",
            "answer": answer,
            "directives_changed": changed,
            "directives": persisted,
            "error_code": None,
            "error_message": None,
            "provider": provider,
        }
