"""Kompany plugin contract.

Stable surface for Pro / community packages to extend Core via Python entry
points. Pinned by semantic versioning — see ``__contract_version__`` below.

Five plugin kinds: ``Workflow``, ``AgentSoul``, ``Integration``, ``Template``,
``Tool``. Each has a corresponding ABC in :mod:`kompany.plugins.contract` and
an entry-point group declared in plugin ``pyproject.toml``:

    [project.entry-points."kompany.workflows"]
    14-day-saas-launch = "my_pro_pack.workflows:fourteen_day_launch"

    [project.entry-points."kompany.souls"]
    saas-compliance-officer = "my_pro_pack.souls:compliance_officer"

    [project.entry-points."kompany.integrations"]
    stripe = "my_pro_pack.integrations.stripe:integration"

    [project.entry-points."kompany.templates"]
    saas-pro-starter = "my_pro_pack.templates:saas_pro_starter"

    [project.entry-points."kompany.tools"]
    stripe.create_invoice = "my_pro_pack.tools.stripe:create_invoice"

Plugin packages MUST pin a compatible Core range in their ``pyproject.toml``::

    dependencies = ["kompany>=0.2,<0.3"]

See ``docs/context/plugin-contract.md`` for the full reference and
``docs/adr/0002-plugin-contract-design.md`` for the trade-off record.
"""

from __future__ import annotations

from kompany.plugins.contract import (
    AgentSoul,
    AutonomyTier,
    CostEstimate,
    Integration,
    OutwardExecutor,
    SideEffect,
    Template,
    Tool,
    ToolContext,
    Workflow,
)
from kompany.plugins.loader import discover, registered

__contract_version__ = "1.0.0"

ENTRY_POINT_GROUPS = (
    "kompany.workflows",
    "kompany.souls",
    "kompany.integrations",
    "kompany.templates",
    "kompany.tools",
    "kompany.outward",
)

__all__ = [
    "__contract_version__",
    "ENTRY_POINT_GROUPS",
    "AgentSoul",
    "AutonomyTier",
    "CostEstimate",
    "Integration",
    "OutwardExecutor",
    "SideEffect",
    "Template",
    "Tool",
    "ToolContext",
    "Workflow",
    "discover",
    "registered",
]
