"""Entry-point discovery for Kompany plugins.

Plugin packages register their contributions via ``[project.entry-points]``
groups in their ``pyproject.toml``. This module scans those groups using
``importlib.metadata`` (no third-party scanner) and instantiates each
contribution.

Discovery is one-shot at engine init. There is no hot-reload — restart Core
to pick up newly installed plugins. (MVP scope; revisit if multi-tenant
Cloud emerges.)
"""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kompany.plugins.contract import (
        AgentSoul,
        Integration,
        OutwardExecutor,
        Template,
        Tool,
        Workflow,
    )


# Builtin contributions shipped inside the kompany package itself —
# no entry point needed (the package's own pyproject entry points are
# only visible when installed, and the engine must work from a source
# checkout). Each value is "module:ClassName".
_BUILTIN_CONTRIBUTIONS: dict[str, tuple[str, ...]] = {
    "integration": (
        "kompany.integrations.email_smtp:EmailIntegration",
        "kompany.integrations.email_smtp:ResendIntegration",
    ),
}


_GROUP_TO_KIND = {
    "kompany.workflows": "workflow",
    "kompany.souls": "soul",
    "kompany.integrations": "integration",
    "kompany.templates": "template",
    "kompany.tools": "tool",
    # ADR-0008: project-supplied outward executors. The engine ships none,
    # so an engine with no plugin installed discovers [] — the outward lane
    # then parks every action with reason "no executor".
    "kompany.outward": "outward_executor",
}


def discover(data_dir: "Path | str | None" = None, *, include_workspace: bool = True) -> dict[str, list]:
    """Scan installed packages for Kompany plugin entry points, then the
    workspace artifact layer (``<data_dir>/artifacts/``, 08-29).

    Returns a dict keyed by plugin kind (``"workflow"``, ``"soul"``,
    ``"integration"``, ``"template"``, ``"tool"``, ``"outward_executor"``);
    each value is a list of instantiated plugin objects (or callables, for
    plugins exported as factories rather than classes).

    Loading failures are caught and logged via a sentinel error entry so
    one broken third-party wheel does not block the rest.
    """
    found: dict[str, list] = {kind: [] for kind in _GROUP_TO_KIND.values()}
    errors: list[tuple[str, str, str]] = []

    # Builtins first — plugins merge in after (never replace).
    from importlib import import_module

    for kind, paths in _BUILTIN_CONTRIBUTIONS.items():
        for path in paths:
            try:
                module_name, class_name = path.split(":")
                cls = getattr(import_module(module_name), class_name)
                found[kind].append(cls())
            except Exception as exc:  # noqa: BLE001 — surfaced via errors list
                errors.append(("builtin", path, repr(exc)))

    for group, kind in _GROUP_TO_KIND.items():
        for ep in entry_points(group=group):
            try:
                obj = ep.load()
                instance = obj() if callable(obj) and not isinstance(obj, type) else obj
                if isinstance(obj, type):
                    instance = obj()
                found[kind].append(instance)
            except Exception as exc:  # noqa: BLE001 — surfaced via errors list
                errors.append((group, ep.name, repr(exc)))

    if include_workspace:
        _merge_workspace(found, errors, data_dir)

    if errors:
        found["_errors"] = errors  # type: ignore[assignment]
    return found


def _merge_workspace(found: dict[str, list], errors: list, data_dir: "Path | str | None") -> None:
    """Workspace souls/workflows merge last; ids already taken are refused."""
    try:
        from kompany.core.artifact_evolution.contributions import load_workspace_contributions
        from kompany.core.artifact_evolution.workspace import workspace_root

        root = workspace_root(data_dir)
        if not root.is_dir():
            return
        taken_roles = {getattr(s, "role", "") for s in found.get("soul", [])}
        taken_workflows = {getattr(w, "workflow_id", "") for w in found.get("workflow", [])}
        try:  # builtin YAML workflows live in the registry, not in entry points
            from kompany.core.workflows_registry import _builtin_yaml_paths, _load_builtin

            for path in _builtin_yaml_paths():
                wf = _load_builtin(path)
                if wf is not None:
                    taken_workflows.add(wf.workflow_id)
        except Exception:  # noqa: BLE001
            pass
        souls, workflows, ws_errors = load_workspace_contributions(root, taken_roles, taken_workflows)
        found["soul"].extend(souls)
        found["workflow"].extend(workflows)
        errors.extend(ws_errors)
    except Exception as exc:  # noqa: BLE001 — workspace problems never block plugin discovery
        errors.append(("workspace", str(data_dir or ""), repr(exc)))


def registered(kind: str, data_dir: "Path | str | None" = None) -> list:
    """Convenience: return plugins of a single kind, freshly discovered."""
    found = discover() if data_dir is None else discover(data_dir)
    return found.get(kind, [])
