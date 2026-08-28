"""Native tool actions — the universal action pipeline (#4 + #5).

Pipeline (grilled 2026-05-27, Q1 = universal):

    read-only Tool, zero cost            → run inline (``execute_tool``)
    side_effect != READ OR any cost      → proposed action → approval card
    any PAID action (SPEND / cost > 0)   → HARD-GATED: never auto, any
                                            tier, any tool_authorization
                                            config. No auto-pay, ever.

Flow: agent/engine calls ``engine.propose_action(...)`` → a
``tool_action`` approval lands in the founder inbox → approve executes
for real via the Integration (credentials injected from the vault) →
the REAL result (sent id / error) is audited and stamped onto the
approval payload. Reject → audited, nothing executes.

Idempotency: an ``effect_applied`` stamp guards replayed approves
(remote command replay, double-tap CLI). The stamp is NOT written when
execution fails — re-approving later retries instead of refusing
forever.

Scope note: these native tools serve the legacy single-call path and
engine/ticker-side actions. Harness sessions get tools via MCP exposure
later — out of scope here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kompany.plugins import loader
from kompany.plugins.contract import SideEffect, ToolContext

if TYPE_CHECKING:
    from kompany.plugins.contract import Tool
    from kompany.state.models import ApprovalRequest

# Approval action_type for proposed tool actions. Routed through
# core/approval_effects.py like the other post-resolve effects.
ACTION_TOOL_EXECUTE = "tool_action"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def tool_registry(engine: Any) -> dict[str, dict[str, Any]]:
    """Map tool name → ``{"tool": Tool, "integrations": [Integration...]}``.

    Built from the plugin loader (builtin integrations + entry-point
    discovered ones). Two integrations may provide the same tool name
    (e.g. SMTP and Resend both expose ``email.send``); both are kept so
    ``tools_list`` can report per-provider connection state.
    """
    registry: dict[str, dict[str, Any]] = {}
    for integ in loader.registered("integration"):
        try:
            tools = integ.tools()
        except Exception:  # noqa: BLE001 — one broken plugin can't block
            continue
        for tool in tools:
            entry = registry.setdefault(tool.name, {"tool": tool, "integrations": []})
            entry["integrations"].append(integ)
    return registry


def _integration_connected(engine: Any, integ: Any) -> bool:
    try:
        return all(
            bool(engine.credentials.get(key))
            for key in (integ.required_credentials or ())
        )
    except Exception:  # noqa: BLE001 — one broken plugin can't block
        # A plugin declaring a `required_credentials` name outside the
        # vault's allowlist must not 500 the whole tools/integrations
        # listing; surface it as simply "not connected".
        return False


def tools_list(engine: Any) -> list[dict[str, Any]]:
    """All registered tools with gating metadata — the founder-facing
    inventory behind ``kompany tools list`` / ``GET /tools``."""
    out: list[dict[str, Any]] = []
    for name, entry in sorted(tool_registry(engine).items()):
        tool: Tool = entry["tool"]
        providers = [
            {
                "integration_id": integ.integration_id,
                "display_name": integ.display_name,
                "connected": _integration_connected(engine, integ),
            }
            for integ in entry["integrations"]
        ]
        out.append(
            {
                "name": name,
                "description": tool.description,
                "side_effect": tool.side_effect.value,
                "autonomy_tier": tool.autonomy_tier.value,
                "paid": tool.side_effect == SideEffect.SPEND,
                "connected": any(p["connected"] for p in providers),
                "providers": providers,
            }
        )
    return out


def integrations_list(engine: Any) -> list[dict[str, Any]]:
    """All registered integrations with connection state — the
    founder-facing inventory behind ``kompany integrations`` /
    ``GET /integrations``. Loader-driven (builtins + entry-point
    plugins), so a new integration shows up everywhere automatically.
    """
    out: list[dict[str, Any]] = []
    for integ in loader.registered("integration"):
        try:
            tool_names = sorted(t.name for t in integ.tools())
        except Exception:  # noqa: BLE001 — one broken plugin can't block
            tool_names = []
        doc_lines = (integ.__doc__ or "").strip().splitlines()
        description = str(
            getattr(integ, "description", "") or (doc_lines[0] if doc_lines else "")
        )
        out.append(
            {
                "integration_id": integ.integration_id,
                "display_name": integ.display_name,
                "description": description,
                "required_credentials": list(integ.required_credentials or ()),
                "connected": _integration_connected(engine, integ),
                "tools": tool_names,
            }
        )
    return sorted(out, key=lambda r: r["integration_id"])


# ---------------------------------------------------------------------------
# Inline execution (read-only fast path)
# ---------------------------------------------------------------------------


def execute_tool(engine: Any, tool_name: str, inputs: dict) -> dict[str, Any]:
    """Run a tool inline — ONLY for read-only, zero-cost tools.

    Anything side-effecting or costing money is refused with
    ``requires_approval`` so the caller routes through
    ``engine.propose_action`` instead. The PAID hard gate lives in
    ``AutonomyGate.check_tool`` — a SPEND tool can never pass here, no
    matter how tool_authorization is configured.
    """
    entry = tool_registry(engine).get(tool_name)
    if entry is None:
        raise ValueError(f"unknown tool: {tool_name}")
    tool: Tool = entry["tool"]
    parsed = tool.input_schema(**inputs) if tool.input_schema else inputs
    estimate = tool.estimate_cost(parsed)
    # Founder hard rules (#6): excluded capability / per-action budget
    # cap / forbidden paid category → refuse with a clear reason.
    get_rules = getattr(engine, "get_founder_rules", lambda: None)
    refusal = engine.autonomy.check_rules(
        get_rules(),
        tool_name=tool_name,
        side_effect=tool.side_effect.value,
        estimated_cost_usd=estimate.total_usd,
        description=tool.description,
    )
    if refusal:
        engine.audit.record(
            "tool_action.refused",
            f"Founder rule refused inline tool: {tool_name}",
            detail={"tool_name": tool_name, "reason": refusal},
        )
        return {"ok": False, "refused": True, "detail": refusal}
    if not engine.autonomy.check_tool(
        tool.side_effect.value,
        tool.autonomy_tier.value,
        estimate.total_usd,
    ):
        return {
            "ok": False,
            "requires_approval": True,
            "detail": (
                f"{tool_name} is {tool.side_effect.value}"
                + (f" (est ${estimate.total_usd:.2f})" if estimate.total_usd else "")
                + " — propose it instead (kompany tools propose)."
            ),
        }
    result = tool.execute(parsed, _tool_context(engine))
    out = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    engine.audit.record(
        "tool_action.inline",
        f"Executed read-only tool inline: {tool_name}",
        detail={"tool_name": tool_name, "result": out},
    )
    return {"ok": True, "result": out}


# ---------------------------------------------------------------------------
# Approval effects (called from core/approval_effects.py)
# ---------------------------------------------------------------------------


def execute_approved_tool_action(engine: Any, request: "ApprovalRequest") -> dict[str, Any]:
    """Execute an approved ``tool_action`` for real.

    Records the real result (sent id / error) to audit and as an
    approval comment, stamps ``effect_applied`` on success, and books
    any real reported spend (``spent_usd`` in the tool output) to the
    ledger as a ``tool_cost`` expense.
    """
    payload = request.payload or {}
    tool_name = str(payload.get("tool_name", ""))
    inputs = payload.get("inputs", {}) or {}
    entry = tool_registry(engine).get(tool_name)
    if entry is None:
        out: dict[str, Any] = {"ok": False, "detail": f"unknown tool: {tool_name}"}
        engine.audit.record(
            "tool_action.failed",
            f"Unknown tool {tool_name}",
            detail={"approval_id": request.id},
            project_id=request.project_id,
        )
        _comment(engine, request.id, f"Could not execute {tool_name}: unknown tool.")
        return {"status": "failed", "result": out}
    tool: Tool = entry["tool"]
    try:
        parsed = tool.input_schema(**inputs) if tool.input_schema else inputs
        result = tool.execute(parsed, _tool_context(engine))
        out = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    except Exception as exc:  # noqa: BLE001 — surface on the card, never crash
        out = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    succeeded = _tool_result_succeeded(out)
    engine.audit.record(
        "tool_action.executed" if succeeded else "tool_action.failed",
        f"Executed approved action: {tool_name}"
        if succeeded
        else f"Approved action failed: {tool_name}",
        detail={"approval_id": request.id, "tool_name": tool_name, "result": out},
        project_id=request.project_id,
    )
    if succeeded:
        # Stamp only on success — a failed send (missing credentials,
        # provider error) stays re-approvable so fixing the credentials
        # and approving again retries the action.
        engine.approvals.update_payload(
            request.id, {"effect_applied": True, "result": out}
        )
        _book_real_spend(engine, request, tool_name, out)
    _comment(engine, request.id, f"Executed {tool_name}: {out}")
    return {"status": "executed" if succeeded else "failed", "result": out}


def reject_tool_action(engine: Any, request: "ApprovalRequest") -> dict[str, Any]:
    """Rejected proposed action: audit it. Nothing executes."""
    payload = request.payload or {}
    engine.audit.record(
        "tool_action.rejected",
        f"Rejected proposed action: {payload.get('tool_name', '?')}",
        detail={"approval_id": request.id, "tool_name": payload.get("tool_name")},
        project_id=request.project_id,
    )
    return {"status": "rejected"}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _tool_context(engine: Any) -> ToolContext:
    from kompany.core.run_context import current_run_id

    return ToolContext(
        run_id=current_run_id() or "",
        ledger=engine.ledger,
        audit=engine.audit,
        credentials=engine.credentials,
        settings=engine.settings,
    )


def _tool_result_succeeded(out: dict[str, Any]) -> bool:
    """Interpret both legacy boolean and structured-status tool outputs."""
    status = out.get("status")
    if status is not None:
        return str(status).strip().lower() in {"confirmed", "executed", "sent", "ok"}
    return out.get("ok") is not False and out.get("sent") is not False


def _book_real_spend(
    engine: Any, request: "ApprovalRequest", tool_name: str, out: dict[str, Any]
) -> None:
    """Cost-as-expense: a tool that reports REAL spend books a ledger
    expense (category ``tool_cost``)."""
    try:
        spent = float(out.get("spent_usd") or 0.0)
    except (TypeError, ValueError):
        spent = 0.0
    if spent <= 0:
        return
    from kompany.state.models import LedgerCategory

    engine.ledger.record(
        amount=-abs(spent),
        description=f"Tool: {tool_name} (approval {request.id})",
        category=LedgerCategory.TOOL_COST,
        project_id=request.project_id,
        approved_by="master",
    )


def _comment(engine: Any, approval_id: str, body: str) -> None:
    try:
        engine.approvals.add_comment(
            approval_id, body=body, by_type="system", by_id=None
        )
    except Exception:  # noqa: BLE001 — outcome already audited
        pass


__all__ = [
    "ACTION_TOOL_EXECUTE",
    "execute_approved_tool_action",
    "execute_tool",
    "integrations_list",
    "reject_tool_action",
    "tool_registry",
    "tools_list",
]
