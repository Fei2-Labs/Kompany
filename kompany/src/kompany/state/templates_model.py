"""Pydantic schema for company-template manifests.

Each shipped template lives at ``kompany/templates/<id>/manifest.json`` with
a sibling ``mission.md`` and optional ``suggested_directives.md``. This
module defines the contract that those JSON files must satisfy.

The model uses ``extra="forbid"`` so a typo'd key in a manifest fails fast
at load time rather than silently dropping data.

See ``docs/context/templates.md`` for authoring rules.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The 11 currently-implemented C-suite agent roles. Kept as a frozenset so
# ``CompanyTemplate.validate_enabled_agents`` can reject typos without
# importing the agent registry (which would create a circular module
# dependency at template-load time).
KNOWN_AGENT_ROLES: frozenset[str] = frozenset({
    "ceo", "cfo", "cto", "cpo", "cmo",
    "cro", "coo", "csa", "ciso", "cos", "cv",
})


class CompanyTemplate(BaseModel):
    """A ready-to-play company preset.

    ``mission_md_path`` is **relative to the manifest's own directory** so
    templates can be moved freely without rewriting paths. The runtime
    service is responsible for resolving it against the on-disk location.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    mission_title: str = Field(..., min_length=1)
    mission_md_path: str = Field(..., min_length=1)
    initial_budget: float = Field(..., ge=0.0)
    enabled_agents: list[str] = Field(default_factory=list)
    agent_config_overrides: dict[str, Any] = Field(default_factory=dict)
    suggested_directives: list[str] = Field(default_factory=list)
    rpg_theme: str = ""

    @field_validator("enabled_agents")
    @classmethod
    def _validate_enabled_agents(cls, value: list[str]) -> list[str]:
        unknown = [role for role in value if role not in KNOWN_AGENT_ROLES]
        if unknown:
            raise ValueError(
                f"unknown agent role(s) in enabled_agents: {unknown}. "
                f"Known: {sorted(KNOWN_AGENT_ROLES)}"
            )
        # Normalize: dedupe while preserving order
        seen: set[str] = set()
        result: list[str] = []
        for role in value:
            if role not in seen:
                seen.add(role)
                result.append(role)
        return result


class TemplateApplyResult(BaseModel):
    """Returned by :meth:`Templates.apply` so callers (CLI/REST/MCP/SDK) get a
    stable, type-checked payload they can serialize without per-surface
    custom shaping."""

    template_id: str
    name: str
    mission: str
    initial_budget: float
    enabled_agents: list[str]
    project_ids: list[str]
    force: bool = False
