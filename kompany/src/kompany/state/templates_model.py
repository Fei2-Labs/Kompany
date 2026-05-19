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


class TemplateGlossaryEntry(BaseModel):
    """One canonical term shipped inside a template manifest.

    Templates ship a curated set of company-specific terminology so the
    founder lands on day one with a baseline glossary instead of an
    empty drift scanner. The runtime stamps each row with
    ``added_at=now`` + ``added_by="template"`` when the template is
    applied — see :meth:`kompany.state.glossary.GlossaryService.bulk_install_from_template`.

    Kept structurally identical to :class:`kompany.state.glossary.GlossaryEntry`
    minus the audit columns (added by the service) so manifest authors
    don't need to know about per-row timestamps.
    """

    model_config = ConfigDict(extra="forbid")

    term: str = Field(..., min_length=1)
    definition: str = Field(..., min_length=1)
    forbidden_synonyms: list[str] = Field(default_factory=list)


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
    # Quantitative target presets — additive to the v1 manifest schema so
    # older community templates without these fields still parse.
    # ``customer_target = None`` means "revenue-only goal".
    revenue_target: float = Field(default=0.0, ge=0.0)
    customer_target: int | None = Field(default=None, ge=0)
    enabled_agents: list[str] = Field(default_factory=list)
    agent_config_overrides: dict[str, Any] = Field(default_factory=dict)
    suggested_directives: list[str] = Field(default_factory=list)
    rpg_theme: str = ""
    # Pre-populated company glossary (founder-defined canonical terms +
    # forbidden synonyms). Optional + additive — community templates that
    # pre-date this field simply load with an empty glossary, and the
    # founder can curate via ``kompany glossary add`` afterwards. Added
    # by glossary-and-drift-detection task (05-19).
    glossary: list[TemplateGlossaryEntry] = Field(default_factory=list)

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
