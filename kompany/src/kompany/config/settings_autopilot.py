"""Autopilot settings keys (09-26-autopilot-reports) — YAML override helper.

Kept out of ``config/settings.py`` (ADR-0003 file-size cap). Semantics:

- ``founder_report_cadence``: ``daily_plus_weekly`` (default) / ``daily`` /
  ``weekly`` / ``none``. Weekly lands after Monday's first tick.
- ``founder_report_delivery``: ``auto`` (Telegram when configured, else
  stored only) / ``none``.
- ``heartbeat_push_on_change``: push heartbeat events only when the
  pending-approval set or runtime state changes.
- ``distill_auto_enabled`` / ``distill_min_new_episodes`` /
  ``distill_max_gap_days``: automatic distillation trigger.
- ``auto_evolution_enabled`` / ``auto_evolution_failure_threshold`` /
  ``auto_evolution_health_threshold``: automatic soul-evolution triggers
  (artifact lane only; code changes stay approval-gated).
- ``evolution_probation_enabled`` / ``evolution_probation_trials`` /
  ``evolution_probation_window_days`` / ``evolution_probation_max_days``:
  an applied soul proposal is on probation until the role has finished
  ``trials`` tasks; a failure rate above the pre-evolution baseline (last
  ``window_days``) auto-reverts it; no verdict after ``max_days`` is
  recorded as inconclusive and the soul stays.
"""

from __future__ import annotations

from typing import Any

STR_KEYS: tuple[str, ...] = ("founder_report_cadence", "founder_report_delivery")
BOOL_KEYS: tuple[str, ...] = (
    # ADR-0010 consent flag rides along: it was env-only before this helper.
    "external_judgment_enabled",
    "heartbeat_push_on_change",
    "distill_auto_enabled",
    "auto_evolution_enabled",
    "evolution_probation_enabled",
)
INT_KEYS: tuple[str, ...] = (
    "distill_min_new_episodes",
    "distill_max_gap_days",
    "auto_evolution_failure_threshold",
    "auto_evolution_health_threshold",
    "evolution_probation_trials",
    "evolution_probation_window_days",
    "evolution_probation_max_days",
)


def apply_overrides(data: dict[str, Any], overrides: dict[str, Any]) -> None:
    """Copy the autopilot keys present in ``data`` (parsed YAML) into
    ``overrides`` with the right scalar type."""
    for key in STR_KEYS:
        if key in data:
            overrides[key] = str(data[key])
    for key in BOOL_KEYS:
        if key in data:
            overrides[key] = bool(data[key])
    for key in INT_KEYS:
        if key in data:
            overrides[key] = int(data[key])


__all__ = ["BOOL_KEYS", "INT_KEYS", "STR_KEYS", "apply_overrides"]
