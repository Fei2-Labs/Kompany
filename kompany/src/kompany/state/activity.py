"""activity_kind — the *kind* of work an agent is doing, for visualisation.

This is the advisory half of the agent-activity contract shared between the
dashboard and the future sprite-world client. It is ORTHOGONAL to *activity
status* (idle/working/dispatching — the lifecycle phase): status says "is the
agent busy", activity_kind says "what sort of work" (coding vs marketing vs
researching) so a client can pick an icon, a label, or a sprite animation.

Derivation is role-primary (the most direct signal of work type), with the
project's :class:`ProjectType` available as flavour for clients that want it.
There is intentionally no separate task-type field in the engine — the kind is
inferred from who is acting. See glossary "Visual style & theming".
"""

from __future__ import annotations

# role -> activity_kind. Subagents map to concrete crafts; C-suite to their
# domain of work. Keep this the single source of the vocabulary.
ROLE_ACTIVITY: dict[str, str] = {
    # subagents (execution tier)
    "researcher": "researching",
    "writer": "writing",
    "analyst": "analyzing",
    "builder": "coding",
    "procurement": "sourcing",
    # C-suite (strategy)
    "ceo": "planning",
    "cfo": "budgeting",
    "coo": "operating",
    "cos": "synthesizing",
    # C-suite (product)
    "cto": "architecting",
    "cpo": "designing",
    "csa": "modeling",
    "ciso": "securing",
    # C-suite (growth)
    "cmo": "marketing",
    "cro": "selling",
    "cv": "supporting",
}

# Statuses that mean "not doing work right now" → activity_kind "idle".
_IDLE_STATUSES = {"idle", "", None}


def derive_activity_kind(
    agent_role: str | None,
    status: str | None,
    project_type: str | None = None,
) -> str:
    """Return the advisory activity_kind for an agent.

    ``idle``/empty status → ``"idle"`` regardless of role. Otherwise map the
    role to its craft; an unknown role falls back to the generic ``"working"``.
    ``project_type`` is accepted for future flavouring but does not currently
    change the result (role is the primary signal by design).
    """
    if status in _IDLE_STATUSES:
        return "idle"
    role = (agent_role or "").lower()
    return ROLE_ACTIVITY.get(role, "working")


__all__ = ["ROLE_ACTIVITY", "derive_activity_kind"]
