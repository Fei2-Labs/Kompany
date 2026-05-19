"""One-line install + onboarding wizard.

The :mod:`kompany.installer` package wraps the existing engine init,
template apply, and directive paths in a single four-step interactive
flow exposed as ``kompany onboard``. See
``docs/context/onboarding.md`` for the player-facing flow description
and ``.trellis/tasks/05-19-one-line-install/prd.md`` for the design.
"""

from __future__ import annotations

from kompany.installer.onboard import (
    OnboardError,
    OnboardResult,
    is_onboarded,
    onboard_headless,
    run_onboard,
)

__all__ = [
    "OnboardError",
    "OnboardResult",
    "is_onboarded",
    "onboard_headless",
    "run_onboard",
]
