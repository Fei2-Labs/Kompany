"""MCP tool dispatch — credentials, policies, approvals, templates, glossary.

Second half of the mechanical extraction of ``mcp_server.call_tool``
(see ``dispatcher.py``); reached via fall-through when no company /
channel / runtime tool matched. Branch order and bodies are unchanged
from the original dispatch.
"""

from __future__ import annotations

from typing import Any

from kompany.core.engine import KompanyEngine


class UnknownToolError(LookupError):
    """Raised when a tool name matches no dispatch branch.

    Each transport translates this itself: the stdio MCP server returns
    the legacy ``Unknown tool: <name>`` text, the REST bridge a 404.
    """


def dispatch_governance_tool(engine: KompanyEngine, name: str, arguments: dict) -> Any:
    """Dispatch the governance/admin tool group; raise on unknown names."""
    if name == "kompany_credentials":
        return engine.list_credentials()

    if name == "kompany_set_credential":
        return engine.set_credential(
            arguments["name"],
            arguments["value"],
        )

    if name == "kompany_delete_credential":
        return engine.delete_credential(arguments["name"])

    if name == "kompany_rotate_credential_key":
        return engine.rotate_credential_key(arguments["new_vault_key"])

    if name == "kompany_tool_policies":
        return engine.list_tool_policies(
            agent_role=arguments.get("agent_role"),
        )

    if name == "kompany_set_tool_policy":
        return engine.set_tool_policy(
            arguments["agent_role"],
            arguments["tool_name"],
            arguments["allowed"],
            reason=arguments.get("reason", ""),
            requires_approval=arguments.get("requires_approval", False),
        )

    if name == "kompany_authorize_tool":
        return engine.authorize_tool(
            arguments["agent_role"],
            arguments["tool_name"],
            purpose=arguments.get("purpose", ""),
        )

    if name == "kompany_use_tool":
        return engine.use_tool(
            arguments["agent_role"],
            arguments["tool_name"],
            purpose=arguments.get("purpose", ""),
            arguments=arguments.get("arguments", {}),
            approval_id=arguments.get("approval_id") or None,
        )

    if name == "kompany_approvals":
        return engine.list_approvals()

    if name == "kompany_approve":
        result = engine.approve_request(arguments["approval_id"])
        return result or {"error": f"Approval '{arguments['approval_id']}' not found"}

    if name == "kompany_reject":
        result = engine.reject_request(
            arguments["approval_id"],
            reason=arguments.get("reason", ""),
        )
        return result or {"error": f"Approval '{arguments['approval_id']}' not found"}

    if name == "kompany_inbox":
        return engine.inbox()

    if name == "kompany_approval_show":
        result = engine.get_approval(arguments["approval_id"])
        return result or {"error": f"Approval '{arguments['approval_id']}' not found"}

    if name == "kompany_approval_approve":
        result = engine.approve_request(
            arguments["approval_id"],
            comment_body=arguments.get("comment") or None,
        )
        return result or {"error": f"Approval '{arguments['approval_id']}' not found"}

    if name == "kompany_approval_reject":
        result = engine.reject_request(
            arguments["approval_id"],
            reason=arguments["reason"],
            comment_body=arguments.get("comment") or None,
        )
        return result or {"error": f"Approval '{arguments['approval_id']}' not found"}

    if name == "kompany_approval_revise":
        result = engine.request_approval_revision(
            arguments["approval_id"],
            counter=arguments["counter"],
            comment_body=arguments.get("comment") or None,
        )
        return result or {"error": f"Approval '{arguments['approval_id']}' not found"}

    if name == "kompany_approval_snooze":
        result = engine.snooze_approval(
            arguments["approval_id"],
            minutes=int(arguments["minutes"]),
            comment_body=arguments.get("comment") or None,
        )
        return result or {"error": f"Approval '{arguments['approval_id']}' not found"}

    if name == "kompany_approval_cancel":
        result = engine.cancel_approval(
            arguments["approval_id"],
            reason=arguments.get("reason") or None,
            comment_body=arguments.get("comment") or None,
        )
        return result or {"error": f"Approval '{arguments['approval_id']}' not found"}

    if name == "kompany_approval_comment":
        result = engine.comment_on_approval(
            arguments["approval_id"],
            body=arguments["body"],
            by_type=arguments.get("by_type", "user"),
            by_id=arguments.get("by_id"),
        )
        return result or {"error": f"Approval '{arguments['approval_id']}' not found"}

    if name == "kompany_template_list":
        return engine.list_templates()

    if name == "kompany_template_show":
        try:
            return engine.show_template(arguments["template_id"])
        except ValueError as exc:
            return {"error": str(exc)}

    if name == "kompany_template_apply":
        try:
            result = engine.apply_template(
                arguments["template_id"],
                force=bool(arguments.get("force", False)),
                override_budget=arguments.get("override_budget"),
                override_directive=arguments.get("override_directive"),
            )
        except ValueError as exc:
            return {"error": str(exc)}
        return result

    # Mission-targets task (05-19) — surface the four-knob contract.
    if name == "kompany_targets_show":
        bundle = engine.get_targets_bundle()
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
            "authoritative": engine.get_targets().model_dump(mode="json"),
        }

    if name == "kompany_targets_review":
        payload = engine.run_target_feasibility_review()
        if payload is None:
            return {
                "error": "No founder targets set; complete onboarding first.",
            }
        return payload

    # Glossary-and-drift-detection task (05-19).
    if name == "kompany_glossary_list":
        return {"entries": engine.list_glossary()}

    if name == "kompany_glossary_show":
        entry = engine.get_glossary_term(arguments["term"])
        if entry is None:
            return {"error": f"term not found: {arguments['term']!r}"}
        return entry

    if name == "kompany_glossary_add":
        try:
            return engine.add_glossary_term(
                term=arguments["term"],
                definition=arguments["definition"],
                forbidden_synonyms=arguments.get("forbidden_synonyms"),
                added_by="founder",
            )
        except ValueError as exc:
            return {"error": str(exc)}

    if name == "kompany_glossary_update":
        try:
            return engine.update_glossary_term(
                term=arguments["term"],
                definition=arguments.get("definition"),
                forbidden_synonyms=arguments.get("forbidden_synonyms"),
            )
        except LookupError as exc:
            return {"error": str(exc)}
        except ValueError as exc:
            return {"error": str(exc)}

    if name == "kompany_glossary_remove":
        removed = engine.remove_glossary_term(arguments["term"])
        return {"removed": removed, "term": arguments["term"]}

    raise UnknownToolError(name)
