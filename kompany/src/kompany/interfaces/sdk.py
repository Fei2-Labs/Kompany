"""Kompany Python SDK — programmatic access to the engine.

Public surface: ``from kompany.interfaces.sdk import Kompany`` (and the
namespace/helper re-exports below) continues to work unchanged.

Internal layout (ADR-0003, ≤500 lines per file):

    sdk_parts/helpers.py       — _session_to_dict, _turn_to_dict
    sdk_parts/namespaces.py    — _ChannelNamespace … _ApprovalsNamespace
    sdk_parts/mixin_core.py    — KompanyCoreOps (init/directive/projects/…)
    sdk_parts/mixin_settings.py — KompanySettingsOps (glossary/approvals/…)
"""

from __future__ import annotations

from kompany.core.engine import KompanyEngine

# Re-exported so ``kompany.interfaces.sdk.DebateEngine`` stays a valid patch
# target (the SDK's debate() resolves the class from this module namespace).
from kompany.core.debate import DebateEngine  # noqa: F401

# Re-export helpers and namespaces so any existing import from this module
# (e.g. ``from kompany.interfaces.sdk import _session_to_dict``) keeps working.
from kompany.interfaces.sdk_parts.helpers import (  # noqa: F401
    _session_to_dict,
    _turn_to_dict,
)
from kompany.interfaces.sdk_parts.namespaces import (  # noqa: F401
    _ApprovalsNamespace,
    _ChannelNamespace,
    _EpisodesNamespace,
    _GlossaryNamespace,
    _HealthNamespace,
    _TargetsNamespace,
    _TemplatesNamespace,
)
from kompany.interfaces.sdk_parts.mixin_core import KompanyCoreOps
from kompany.interfaces.sdk_parts.mixin_settings import KompanySettingsOps


class Kompany(KompanyCoreOps, KompanySettingsOps):
    """High-level SDK for interacting with Kompany programmatically.

    Usage:
        from kompany import Kompany
        k = Kompany()
        k.init("MyCompany", capital=50.0, goal="Buy a Mac Studio M4 128GB")
        result = k.directive("Buy a Mac Studio M4 128GB")
        print(result["message"])
    """

    def __init__(self, config_path: str | None = None):
        self._engine = KompanyEngine(config_path=config_path)
