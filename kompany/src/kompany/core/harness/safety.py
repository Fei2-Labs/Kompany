"""Constitution guard shared by every loop vehicle.

Harness sessions must never target Kompany's own source repo — a vehicle
given the engine's checkout as its workspace could rewrite the engine that
is running it. Extracted from ``claude_code_runner`` so all vehicle
adapters enforce the identical check.
"""

from __future__ import annotations

import functools
from pathlib import Path


@functools.lru_cache(maxsize=1)
def kompany_source_root() -> Path:
    """Root of the Kompany source tree this module runs from.

    Walks up from the installed package looking for a ``.git`` directory
    (the dev checkout case); falls back to the package directory itself
    (site-packages installs).
    """
    pkg_dir = Path(__file__).resolve().parents[2]  # .../kompany package
    for ancestor in (pkg_dir, *pkg_dir.parents):
        if (ancestor / ".git").exists():
            return ancestor
    return pkg_dir


def assert_workspace_safe(workspace: Path) -> None:
    """Constitution guard: harness must never target Kompany's own repo."""
    ws = Path(workspace).resolve()
    root = kompany_source_root()
    if ws.is_relative_to(root) or root.is_relative_to(ws):
        raise ValueError(
            f"refusing harness run: workspace {ws} overlaps the Kompany "
            f"source tree at {root} (constitution: harness sessions must "
            "never target Kompany's own source repo)"
        )
