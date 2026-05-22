"""Plugin contract — the five ABCs Pro / community packages implement.

Stability promise: every public symbol in this module is part of the
plugin contract and follows the version pinned by
:data:`kompany.plugins.__contract_version__`. Breaking changes bump the
major; additive changes bump the minor; doc-only changes bump the patch.
Plugin authors pin against the major in their ``pyproject.toml``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Shared value objects
# ---------------------------------------------------------------------------


class SideEffect(str, Enum):
    """How invasive is a Tool call.

    Drives ledger / audit / AutonomyGate behavior. The engine refuses to
    auto-execute Tools whose declared ``SideEffect`` exceeds the operator's
    current autonomy tier without explicit approval.
    """

    READ = "read"
    """No state mutation, no external action. Free to invoke."""

    WRITE_LOCAL = "write_local"
    """Mutates local DB / ledger / audit. Logged but auto-approved."""

    EXTERNAL_ACTION = "external_action"
    """Calls an external service (e.g. send Slack message). May be reversible."""

    SPEND = "spend"
    """Costs the operator real money. ALWAYS routed through AutonomyGate."""


class AutonomyTier(str, Enum):
    """Required operator involvement for a Tool call."""

    AUTO = "auto"
    """Engine may invoke without prompting."""

    APPROVAL = "approval"
    """Engine must obtain founder approval before invoking."""

    HUMAN_ONLY = "human_only"
    """Engine may only surface the suggestion; founder must execute manually."""


@dataclass(frozen=True)
class CostEstimate:
    """Pre-execution cost projection for a Tool call.

    All values in USD. ``llm_usd`` covers Kompany-internal LLM spend that
    would happen as part of executing the tool (most Tools don't trigger
    additional LLM calls; leave 0). ``external_usd`` covers money paid to
    the third-party service (e.g. Stripe processing fee, ad spend).
    """

    llm_usd: float = 0.0
    external_usd: float = 0.0
    confidence: float = 1.0
    """0..1 — how confident the plugin is in this estimate."""

    @property
    def total_usd(self) -> float:
        return self.llm_usd + self.external_usd


@dataclass
class ToolContext:
    """Runtime services injected into ``Tool.execute``.

    Plugin authors receive this so a Tool can: read the ledger, write
    audit events, propagate the active run id, look up credentials by
    Integration id. Concrete attribute set is intentionally narrow — add
    fields here when a new contract minor bumps.
    """

    run_id: str
    ledger: Any  # kompany.state.ledger.Ledger
    audit: Any  # kompany.state.audit.AuditLog
    credentials: Any  # kompany.state.credentials.Credentials
    settings: Any  # kompany.config.settings.KompanySettings


# ---------------------------------------------------------------------------
# The 5 plugin ABCs
# ---------------------------------------------------------------------------


class Tool(ABC):
    """A callable action an agent can invoke.

    Thick by design: every Tool declares its ``side_effect``, an
    ``autonomy_tier`` floor, and a cost preview hook so the engine can
    apply cost-visibility discipline (PREVIEW / STREAM / LEDGER) and
    AutonomyGate without the Tool author writing that plumbing.
    """

    name: str = ""
    """Dotted identifier, e.g. ``"stripe.create_invoice"``."""

    description: str = ""
    """LLM-facing description; agents use this to decide whether to call."""

    input_schema: type[BaseModel] | None = None
    output_schema: type[BaseModel] | None = None

    side_effect: SideEffect = SideEffect.READ
    autonomy_tier: AutonomyTier = AutonomyTier.AUTO

    @abstractmethod
    def estimate_cost(self, inputs: BaseModel) -> CostEstimate:
        """Return a pre-execution cost projection for ``inputs``."""

    @abstractmethod
    def execute(self, inputs: BaseModel, ctx: ToolContext) -> BaseModel:
        """Run the tool. Engine has already applied AutonomyGate by here."""


class AgentSoul(ABC):
    """A new role (C-level or subagent) Pro / community packages add.

    Two flavors:

    * **YAML-only (recommended for 80% of cases):** subclass ``AgentSoul``,
      set ``soul_yaml`` to the path of a YAML file describing persona,
      tone, allowed tools, debate behavior. The engine runs it via
      :class:`kompany.agents.soul_agent.SoulAgent` — no extra Python.
    * **Python escape hatch:** override ``run`` to inject custom behavior
      (specialized debate participation, custom tool selection logic,
      etc.).

    MUST add a NEW role identifier — never collide with Core's 11 C-suite
    or 5 subagents. Pro is additive, never replacement.
    """

    role: str = ""
    display_name: str = ""
    tier: str = "c_level"  # "c_level" | "subagent"
    squad: str = ""  # only meaningful for c_level
    soul_yaml: "Path | str | None" = None

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Default = delegate to SoulAgent (loaded from ``soul_yaml``).

        Subclass and override only if YAML cannot express the behavior.
        Core wires the default path; subclassing is the escape hatch.
        """
        raise NotImplementedError(
            "AgentSoul.run() must be overridden, OR ``soul_yaml`` set "
            "so the engine's SoulAgent runtime can drive this role."
        )


class Workflow(ABC):
    """A multi-step business recipe (e.g. ``14-day-saas-launch``).

    Hybrid shape: top-level orchestration is declarative (YAML step list)
    so the engine can compute cost previews, AutonomyGate decisions,
    audit trails, and evidence-traced debate hooks BEFORE running. Steps
    that need imperative logic point to a Python callable via
    ``python_callables``.
    """

    workflow_id: str = ""
    display_name: str = ""
    yaml_path: "Path | str | None" = None

    python_callables: dict[str, Callable[..., Any]] | None = None
    """Map of ``step_id -> callable`` for YAML steps that need Python."""

    @abstractmethod
    def estimate_cost(self) -> CostEstimate:
        """Sum of all step cost estimates. Engine calls before run."""


class Integration(ABC):
    """A connector to an external service (Stripe, Polar, Notion, Slack...).

    Pure tool source + credential declaration: Integration declares which
    credentials it needs and which Tools it exposes. No passive state, no
    scheduled tasks (deferred — add via additive minor bump if ever needed).

    The engine prompts the operator to fill declared credentials once;
    they land in :class:`kompany.state.credentials.Credentials` (OS-keychain
    backed) and are injected via :class:`ToolContext`.
    """

    integration_id: str = ""
    display_name: str = ""

    required_credentials: tuple[str, ...] = ()
    """Credential keys this integration needs, e.g. ``("api_key",)``."""

    @abstractmethod
    def tools(self) -> list[Tool]:
        """Return the Tools this Integration exposes to agents."""


class Template(ABC):
    """A pre-bundled company starter (e.g. ``saas-pro-starter``).

    Extends Core's ``manifest.json`` schema with Pro-specific fields. Bundles
    workflows / souls / integrations by ID (reference, not embed) so a
    single Workflow can be referenced by multiple Templates without
    duplication.

    The engine surfaces Pro Templates in the same onboarding faction-card
    list as Core templates, marked with a Pro badge.
    """

    template_id: str = ""
    display_name: str = ""
    manifest_path: "Path | str | None" = None

    bundled_workflow_ids: tuple[str, ...] = ()
    enabled_pro_soul_ids: tuple[str, ...] = ()
    required_integration_ids: tuple[str, ...] = ()
