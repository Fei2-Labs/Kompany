"""Late-bound engine accessors for api_parts route modules.

Tests monkeypatch ``kompany.interfaces.api._engine`` / ``get_engine`` /
``reset_engine`` directly on the ``api`` module, so the canonical
implementations stay there; these wrappers resolve at call time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from kompany.core.engine import KompanyEngine


def get_engine() -> "KompanyEngine":
    from kompany.interfaces import api

    return api.get_engine()


def reset_engine() -> None:
    from kompany.interfaces import api

    return api.reset_engine()
