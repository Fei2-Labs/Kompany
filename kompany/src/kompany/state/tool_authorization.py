"""Persistent tool authorization policies."""

from __future__ import annotations

from kompany.state.database import Database
from kompany.state.models import ToolAuthorizationPolicy

_DEFAULT_POLICIES = [
    ("ceo", "decision_packet", True, False, "CEO may prepare strategic packets."),
    ("coo", "project_execution", True, False, "COO may dispatch approved project work."),
    ("cos", "retrospective", True, False, "CoS may run retrospectives."),
    ("cfo", "ledger", True, False, "CFO may inspect financial ledger state."),
    ("ciso", "backup_restore", True, True, "Restore requires explicit user approval."),
    ("subagent", "external_network", False, False, "Subagents cannot call external network tools directly."),
]


class ToolAuthorizationStore:
    """SQLite-backed allow/deny policy registry for agent tool use."""

    def __init__(self, db: Database):
        self.db = db
        self.seed_defaults()

    def seed_defaults(self) -> None:
        for agent_role, tool_name, allowed, requires_approval, reason in _DEFAULT_POLICIES:
            self.db.execute(
                """INSERT OR IGNORE INTO tool_authorizations
                   (agent_role, tool_name, allowed, requires_approval, reason)
                   VALUES (?, ?, ?, ?, ?)""",
                (agent_role, tool_name, int(allowed), int(requires_approval), reason),
            )
        self.db.commit()

    def list(self, agent_role: str | None = None) -> list[ToolAuthorizationPolicy]:
        if agent_role:
            rows = self.db.execute(
                """SELECT * FROM tool_authorizations
                   WHERE agent_role = ?
                   ORDER BY agent_role, tool_name""",
                (agent_role,),
            ).fetchall()
        else:
            rows = self.db.execute(
                """SELECT * FROM tool_authorizations
                   ORDER BY agent_role, tool_name""",
            ).fetchall()
        return [self._row_to_policy(row) for row in rows]

    def get(self, agent_role: str, tool_name: str) -> ToolAuthorizationPolicy | None:
        row = self.db.execute(
            """SELECT * FROM tool_authorizations
               WHERE agent_role = ? AND tool_name = ?""",
            (agent_role, tool_name),
        ).fetchone()
        return self._row_to_policy(row) if row else None

    def set(
        self,
        agent_role: str,
        tool_name: str,
        allowed: bool,
        reason: str = "",
        requires_approval: bool = False,
    ) -> ToolAuthorizationPolicy:
        self.db.execute(
            """INSERT INTO tool_authorizations
               (agent_role, tool_name, allowed, requires_approval, reason, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(agent_role, tool_name) DO UPDATE SET
                 allowed = excluded.allowed,
                 requires_approval = excluded.requires_approval,
                 reason = excluded.reason,
                 updated_at = datetime('now')""",
            (agent_role, tool_name, int(allowed), int(requires_approval), reason),
        )
        self.db.commit()
        policy = self.get(agent_role, tool_name)
        if policy is None:
            raise RuntimeError("tool authorization policy was not persisted")
        return policy

    @staticmethod
    def _row_to_policy(row) -> ToolAuthorizationPolicy:
        return ToolAuthorizationPolicy(
            agent_role=row["agent_role"],
            tool_name=row["tool_name"],
            allowed=bool(row["allowed"]),
            requires_approval=bool(row["requires_approval"]),
            reason=row["reason"] or "",
            updated_at=row["updated_at"],
        )
