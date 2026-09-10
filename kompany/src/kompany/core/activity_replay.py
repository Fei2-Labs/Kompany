"""Per-role activity replay (Studio PR1, GAP-1).

The live session stream in Studio is fed by SSE (`harness.event`,
`llm.spend`, `agent.activity`, `audit.*`). Those events are not persisted as
a stream, so a Studio opened late would start blank. This module rebuilds the
recent stream for one role from what IS durable: audit rows tagged with the
role (tool calls, workflow steps, approval effects, status changes) and
AI-cost ledger rows whose description names the role. Lines share the shape
the client builds from live events, so backfill and live merge seamlessly.
"""

from __future__ import annotations

import json
from typing import Any

MAX_LIMIT = 500
_KIND_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("tool_action", "tool"),
    ("tool_authorization", "tool"),
    ("workflow", "turn"),
    ("approval", "approval"),
    ("approval_effect", "approval"),
    ("health", "health"),
    ("skill", "text"),
    ("agent_status", "status"),
    ("harness", "text"),
)


def _kind_for(event_type: str) -> str:
    for prefix, kind in _KIND_BY_PREFIX:
        if event_type == prefix or event_type.startswith(prefix + "."):
            return kind
    return "audit"


def _detail_text(raw: Any, cap: int = 240) -> str:
    if raw in (None, ""):
        return ""
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    try:
        obj = json.loads(text) if isinstance(text, str) and text[:1] in "{[" else None
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        for key in ("summary", "tool_name", "detail", "reason", "message", "error"):
            if obj.get(key):
                text = f"{key}={obj[key]}" if key != "summary" else str(obj[key])
                break
        else:
            text = json.dumps(obj, ensure_ascii=False)
    return text if len(text) <= cap else text[: cap - 1] + "…"


def recent_activity(engine: Any, role: str, limit: int = 200) -> dict[str, Any]:
    """Recent stream lines for ``role`` (lowercase), oldest → newest."""
    role_l = (role or "").strip().lower()
    if not role_l:
        raise ValueError("role is required")
    limit = max(1, min(int(limit), MAX_LIMIT))
    db = engine.db
    lines: list[dict[str, Any]] = []

    rows = db.execute(
        """SELECT id, timestamp, event_type, action, detail, run_id, project_id FROM audit_log
           WHERE lower(agent_role) = ? ORDER BY id DESC LIMIT ?""",
        (role_l, limit),
    ).fetchall()
    for r in rows:
        lines.append({
            "ts": r["timestamp"], "kind": _kind_for(str(r["event_type"])), "source": "audit",
            "text": str(r["action"] or r["event_type"]), "detail": _detail_text(r["detail"]),
            "run_id": r["run_id"], "project_id": r["project_id"], "event_type": r["event_type"],
        })

    # AI spend: the ledger has no agent column; descriptions are "AI: <agent> …"
    # (BaseAgent.call) so a case-insensitive match on the role is the join.
    spend_rows = db.execute(
        """SELECT id, timestamp, amount, description, project_id FROM ledger
           WHERE category = 'ai_cost' AND lower(description) LIKE ? ORDER BY id DESC LIMIT ?""",
        (f"%{role_l}%", limit),
    ).fetchall()
    for r in spend_rows:
        lines.append({
            "ts": r["timestamp"], "kind": "spend", "source": "ledger",
            "text": f"${abs(float(r['amount'])):.3f}", "detail": str(r["description"] or ""),
            "run_id": None, "project_id": r["project_id"], "event_type": "llm.spend",
        })

    lines.sort(key=lambda x: (str(x["ts"]), x["source"]))
    lines = lines[-limit:]
    status = None
    try:
        status = engine.agent_status.get(role_l)
    except Exception:  # noqa: BLE001
        status = None
    return {"role": role_l, "count": len(lines), "lines": lines, "status": status}


__all__ = ["MAX_LIMIT", "recent_activity"]
