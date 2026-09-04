"""Workflow registry — discover Core's built-in workflows + Pro plugins.

Core workflows live as YAML files under ``kompany/src/kompany/workflows/``
(packaged in the wheel via ``importlib.resources``). Pro workflows
register via the ``kompany.workflows`` entry-point group; the plugin
loader fetches them.

The registry is read-only — workflows are loaded once on demand. To pick
up newly installed plugins, restart the engine. (MVP scope, mirrors
:mod:`kompany.plugins.loader`.)
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any

from kompany.core.workflow_runner import WorkflowRunner

_PACKAGE = "kompany"
_WORKFLOWS_DIR = "workflows"


class WorkflowNotFound(LookupError):
    """Raised when ``get`` cannot locate a workflow id."""


def _builtin_yaml_paths() -> list[Any]:
    """Walk the packaged workflows directory; return traversables for each
    ``*.yaml`` file found.
    """
    root = importlib.resources.files(_PACKAGE).joinpath(_WORKFLOWS_DIR)
    out: list[Any] = []
    try:
        for child in root.iterdir():
            if child.is_file() and child.name.endswith(".yaml"):
                out.append(child)
    except (FileNotFoundError, NotADirectoryError):
        pass
    return out


def _pro_workflows() -> list[Any]:
    """Pull Pro Workflow plugin instances via the entry-point loader.

    Returns ``[]`` if the loader can't be imported (very early bootstrap)
    or no Pro plugins are installed.
    """
    try:
        from kompany.plugins.loader import discover
    except Exception:
        return []
    try:
        return discover().get("workflow", [])
    except Exception:
        return []


def plugin_for(workflow_id: str) -> Any | None:
    """Return the Pro ``Workflow`` plugin instance declaring ``workflow_id``.

    ``None`` for built-in YAML workflows (they have no Python side) and for
    unknown ids. Callers use it to reach ``python_callables`` / ``bind``.
    """
    for plugin in _pro_workflows():
        if getattr(plugin, "workflow_id", "") == workflow_id:
            return plugin
    return None


def list_workflows() -> list[str]:
    """Return all known workflow ids — built-in + Pro."""
    ids: list[str] = []
    for path in _builtin_yaml_paths():
        wf = _load_builtin(path)
        if wf is not None:
            ids.append(wf.workflow_id)
    for plugin in _pro_workflows():
        wid = getattr(plugin, "workflow_id", "")
        if wid:
            ids.append(wid)
    return sorted(set(ids))


def get(
    workflow_id: str,
    python_callables=None,
    step_executor=None,
) -> WorkflowRunner:
    """Load a workflow by id and return a configured :class:`WorkflowRunner`."""
    for path in _builtin_yaml_paths():
        wf = _load_builtin(path)
        if wf is not None and wf.workflow_id == workflow_id:
            # Reload with the caller's executor / callables so the
            # returned runner is fully configured.
            yaml_text = path.read_text(encoding="utf-8")
            import yaml as _yaml

            data = _yaml.safe_load(yaml_text)
            return WorkflowRunner(
                data,
                python_callables=python_callables,
                step_executor=step_executor,
            )

    for plugin in _pro_workflows():
        if getattr(plugin, "workflow_id", "") != workflow_id:
            continue
        yaml_source = getattr(plugin, "yaml_path", None)
        if yaml_source is None:
            raise WorkflowNotFound(
                f"Pro workflow {workflow_id!r} declared but yaml_path is unset"
            )
        return WorkflowRunner(
            Path(yaml_source) if not isinstance(yaml_source, Path) else yaml_source,
            python_callables=python_callables,
            step_executor=step_executor,
        )

    raise WorkflowNotFound(
        f"workflow not found: {workflow_id!r}. "
        f"Run kompany.core.workflows_registry.list_workflows() for available ids."
    )


def _load_builtin(path: Any) -> WorkflowRunner | None:
    """Parse a built-in workflow YAML without executor / callables.

    Used by ``list_workflows`` to enumerate ids. Returns ``None`` on
    parse failure so one corrupt file doesn't kill listing.
    """
    try:
        import yaml as _yaml

        data = _yaml.safe_load(path.read_text(encoding="utf-8"))
        return WorkflowRunner(data)
    except Exception:
        return None
