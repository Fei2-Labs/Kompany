"""Ticker — the engine's autonomous heartbeat (daemon tick loop).

PRD ``06-12-daemon-tick-loop`` D1: the ticker lives **in the engine**,
not in a separate scheduler process — any host that boots the engine's
background workers (sidecar server, daemon server) gets ticking for
free. It follows the :class:`kompany.core.watchdog.Watchdog` pattern
exactly: asyncio task, injectable sleeper/clock for tests, idempotent
``start``, ``contextlib.suppress(CancelledError)`` stop.

Tick actions (PRD D3, strictly bounded, each individually guarded):

1. **Runtime gate** — runtime suspended → record an idle tick, nothing
   else runs (the founder brake is ``kompany runtime suspend``).
2. **Heartbeat** — ``engine.heartbeat_once()`` (pending approvals,
   active projects, monthly subscription fee booking). Reused, never
   duplicated.
3. **Advance work** — at most ONE pending task of ONE active project
   per tick via :meth:`kompany.core.runner.ProjectRunner.run_one_pending`
   (envelope guards, per-task caps, permission gate already enforce
   spend safety). Gated by ``daemon_auto_execute``; a project with a
   pending ``project_envelope_topup`` / ``harness_budget_increase``
   approval is skipped — don't grind against a closed gate.
4. **Housekeeping** — ``daemon_ticks.prune(keep=500)`` plus the EXISTING
   episodes retention hook (``engine.episodes.trim_to_retention_window``
   with the ``episode_retention_full_count`` config, the same call the
   engine's episode materialization path uses). Approval auto-unsnooze
   already runs every Watchdog scanner pass
   (``Watchdog._scan_snoozed_approvals``) — deliberately NOT duplicated
   here (code-reuse guide).
5. **Record** — one ``daemon_ticks`` row + an SSE ``daemon.tick`` event
   with ``activity_kind: "tick"`` (additive to the activity contract).

``self.actions`` is an ordered ``(name, callable)`` list so PRD 3/4
persona intents (emotion, diary, posting) can append their own steps
without touching the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from kompany.core.event_hub import get_event_hub
from kompany.core.harness_execution import (
    ACTION_BUDGET_INCREASE,
    ACTION_ENVELOPE_TOPUP,
)
from kompany.state.daemon_ticks import DaemonTickStore
from kompany.state.export_bundle import read_exported_marker
from kompany.state.models import TaskStatus

log = logging.getLogger(__name__)

# Tick history retention (PRD: keep the last 500 ticks).
TICK_HISTORY_KEEP = 500


def _status_value(status: Any) -> str:
    return status.value if isinstance(status, TaskStatus) else str(status)


class Ticker:
    """Engine-scoped 24/7 tick loop (Watchdog precedent)."""

    def __init__(
        self,
        engine: Any,
        ticks: DaemonTickStore,
        tick_interval_seconds: int = 300,
        auto_execute: bool = True,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
        hub: Any = None,
    ):
        self._engine = engine
        self._ticks = ticks
        self.tick_interval_seconds = max(1, int(tick_interval_seconds))
        self.auto_execute = bool(auto_execute)
        # ``sleeper``/``clock`` are injectable so tests can drive the loop
        # without real waiting (watchdog style). Production keeps the
        # defaults: ``asyncio.sleep`` + ``time.monotonic``.
        self._sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._clock = clock if clock is not None else time.monotonic
        self._hub = hub if hub is not None else get_event_hub()
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self.last_tick_at: str | None = None
        self.tick_count: int = 0
        # Ordered tick steps — PRD 3/4 persona intents extend this list.
        self.actions: list[tuple[str, Callable[[], list[str]]]] = [
            ("heartbeat", self._action_heartbeat),
            ("soul_cycles", self._action_soul_cycles),
            ("advance", self._action_advance),
            ("housekeeping", self._action_housekeeping),
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the tick loop if not already running. Idempotent.

        The caller must already be inside a running asyncio loop; the
        engine wires this from its async entry points.
        """
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.debug("ticker.start called outside a running loop; deferring")
            return
        self._stopped.clear()
        self._task = loop.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel and await the tick loop. Idempotent."""
        task = self._task
        self._task = None
        self._stopped.set()
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------
    # One tick
    # ------------------------------------------------------------------

    def tick_once(self) -> dict[str, Any]:
        """Run one full tick pass synchronously. Returns the tick row.

        Public so tests (and later surfaces) can drive a tick without
        the asyncio timer — same contract as ``Watchdog.scan_once``.
        """
        started_at = datetime.now(UTC).isoformat()
        t0 = self._clock()
        actions: list[str] = []
        errors: dict[str, str] = {}
        runtime = self._engine.runtime.get() or {}
        if read_exported_marker(self._engine.settings.data_dir) is not None:
            # Handoff tombstone: this company moved to another machine.
            # Never tick here — the imported copy is the live one.
            outcome = "idle_exported"
        elif runtime.get("state") == "suspended":
            outcome = "idle_suspended"
        else:
            for name, action in self.actions:
                try:
                    actions.extend(action() or [])
                except Exception as exc:  # noqa: BLE001 — one step must not kill the tick
                    log.exception("ticker action %r failed", name)
                    actions.append(f"{name}:error")
                    errors[name] = str(exc)
            outcome = "error" if errors else "ok"
        duration_ms = int(max(0.0, self._clock() - t0) * 1000)
        row = self._ticks.record(
            started_at=started_at,
            duration_ms=duration_ms,
            actions=actions,
            outcome=outcome,
            detail={"errors": errors} if errors else None,
        )
        self.last_tick_at = started_at
        self.tick_count += 1
        self._publish(outcome, actions, duration_ms)
        return row

    # ------------------------------------------------------------------
    # Tick actions (D3 order)
    # ------------------------------------------------------------------

    def _action_heartbeat(self) -> list[str]:
        """Existing engine heartbeat: approvals, projects, monthly fee."""
        self._engine.heartbeat_once()
        return ["heartbeat"]

    def _action_advance(self) -> list[str]:
        """Advance AT MOST one pending task of the oldest eligible project.

        ADR-0005: when the engine wires a lane dispatcher, delegate to it
        so several independent lanes can advance per tick. With the single
        default ``main`` lane the dispatcher reproduces this method's
        oldest-eligible-project, one-task-per-tick behaviour exactly — the
        legacy path below stays in place for engines without a dispatcher.
        """
        if not self.auto_execute:
            return []
        dispatcher = getattr(self._engine, "lane_dispatcher", None)
        if dispatcher is not None:
            return dispatcher.dispatch_once()
        actions: list[str] = []
        blocked = self._projects_awaiting_budget_approval()
        candidates = sorted(
            self._engine.projects.list_active(), key=lambda p: p.created_at
        )
        for project in candidates:
            tasks = self._engine.projects.list_tasks(project.id)
            # A project with NO tasks at all stays eligible: the slice
            # decomposes-if-empty (run_one_pending), so the ticker can
            # START new projects, not only continue kicked-off ones. Only
            # skip when tasks exist but none is pending (all done/failed).
            if tasks and not any(
                _status_value(t.status) == TaskStatus.PENDING.value
                for t in tasks
            ):
                continue
            if project.id in blocked:
                actions.append(f"skipped_pending_approval:{project.id}")
                continue
            task_id = self._run_one_pending(project.id)
            if task_id:
                actions.append(f"advanced_task:{task_id}")
                return actions
        if not actions:
            actions.append("no_work")
        return actions

    def _action_soul_cycles(self) -> list[str]:
        """File recurring cycle tasks for souls that declare ``cycle_cadence``.

        Scans discovered ``AgentSoul`` plugins for a ``cycle_cadence`` block
        in their YAML. For each soul whose ``hours_local`` (or legacy
        ``hours_cet``) contains the current Stockholm-local hour, finds the
        active project whose name contains ``project_name_substring`` and —
        if no pending cycle task for that role already exists in it — files
        one. The task is then picked up by ``_action_advance`` like any
        other pending task.

        This is the single authoritative external-action scheduler (e.g.
        LinkedIn growth cycles): a soul's YAML declares its own cadence, so
        no separate systemd timer / cron process should also drive the same
        integration — that duplicates external actions (posts, comments).

        Idempotent within an hour: a second tick in the same local hour
        will see the just-filed task still pending and skip. Recurrence
        across days falls out of "task completes → next day's hour hits →
        new task filed" — no recurring-task model needed.

        Best-effort: a broken soul YAML or missing project is logged and
        skipped, never fatal to the tick.
        """
        actions: list[str] = []
        try:
            from kompany.plugins.loader import registered

            souls = registered("soul")
        except Exception:  # noqa: BLE001
            return actions
        local_hour = _current_local_hour()
        if local_hour is None:
            return actions
        for soul in souls:
            try:
                cadence = _soul_cycle_cadence(soul)
            except Exception:  # noqa: BLE001
                continue
            if not cadence:
                continue
            hours = cadence.get("hours_local") or cadence.get("hours_cet") or []
            if local_hour not in hours:
                continue
            role = getattr(soul, "role", "")
            if not role:
                continue
            project = _find_cycle_project(self._engine, cadence)
            if project is None:
                continue
            if _has_pending_cycle_task(self._engine, project.id, role):
                continue
            integration_id = _soul_integration_id(soul)
            task_id = _file_cycle_task(
                self._engine, project, role, cadence,
                integration_id=integration_id,
            )
            if task_id:
                actions.append(f"soul_cycle_filed:{role}:{task_id}")
        return actions

    def _action_housekeeping(self) -> list[str]:
        """Prune tick history + reuse the existing episodes retention hook."""
        actions: list[str] = []
        removed = self._ticks.prune(keep=TICK_HISTORY_KEEP)
        if removed:
            actions.append(f"pruned_ticks:{removed}")
        episodes = getattr(self._engine, "episodes", None)
        trim = getattr(episodes, "trim_to_retention_window", None)
        if callable(trim):
            get_cfg = getattr(self._engine, "_get_int_config", None)
            max_full = (
                get_cfg("episode_retention_full_count", default=50)
                if callable(get_cfg)
                else 50
            )
            trimmed = trim(max_full)
            if trimmed:
                actions.append(f"episodes_trimmed:{len(trimmed)}")
        # Remote backup (07-14 step 5): upload an encrypted bundle every
        # ~24h (288 ticks at 300s). Best-effort — failures are logged,
        # never fatal to the tick.
        if self.tick_count % 288 == 0 and self.tick_count > 0:
            rb = self._maybe_remote_backup()
            if rb:
                actions.append(rb)
        return actions

    def _maybe_remote_backup(self) -> str | None:
        """Upload an encrypted bundle to remote storage if configured."""
        cfg_dict = getattr(self._engine.settings, "remote_backup", None)
        if not cfg_dict or not isinstance(cfg_dict, dict):
            return None
        try:
            from kompany.state.remote_backup import (
                RemoteBackupConfig,
                RemoteBackupError,
                upload_bundle,
            )
            cfg = RemoteBackupConfig.from_dict(cfg_dict)
            result = upload_bundle(cfg, self._engine.settings.data_dir)
            return f"remote_backup:{result['key']}"
        except Exception as exc:  # noqa: BLE001 — best-effort
            log.warning("remote backup failed: %s", exc)
            return "remote_backup:error"

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _projects_awaiting_budget_approval(self) -> set[str]:
        """Project ids with an open top-up / budget-increase approval."""
        blocking = {ACTION_ENVELOPE_TOPUP, ACTION_BUDGET_INCREASE}
        return {
            req.project_id
            for req in self._engine.approvals.list_pending()
            if req.action_type in blocking and req.project_id
        }

    def _run_one_pending(self, project_id: str) -> str | None:
        # Lazy import mirrors engine.py's own ProjectRunner imports and
        # keeps core/ticker.py free of an import-time runner dependency.
        from kompany.core.runner import ProjectRunner

        return ProjectRunner(self._engine).run_one_pending(project_id)

    def _publish(
        self, outcome: str, actions: list[str], duration_ms: int
    ) -> None:
        try:
            self._hub.publish(
                "daemon.tick",
                {
                    "activity_kind": "tick",
                    "outcome": outcome,
                    "actions": actions,
                    "duration_ms": duration_ms,
                },
            )
        except Exception:  # noqa: BLE001 — live feed is best-effort
            pass

    async def _loop(self) -> None:
        """Background task: sleep + tick + repeat until cancelled."""
        log.debug(
            "ticker loop started (interval=%ds, auto_execute=%s)",
            self.tick_interval_seconds,
            self.auto_execute,
        )
        try:
            while not self._stopped.is_set():
                await self._sleeper(self.tick_interval_seconds)
                if self._stopped.is_set():
                    break
                try:
                    self.tick_once()
                except Exception as exc:  # noqa: BLE001 — never let a tick kill the loop
                    log.exception("ticker tick_once failed")
                    with contextlib.suppress(Exception):
                        self._ticks.record(
                            started_at=datetime.now(UTC).isoformat(),
                            duration_ms=0,
                            actions=[],
                            outcome="error",
                            detail={"error": str(exc)},
                        )
        except asyncio.CancelledError:
            log.debug("ticker loop cancelled")
            raise


# ---------------------------------------------------------------------------
# Soul-cycle helpers (module-level so they're testable without a Ticker)
# ---------------------------------------------------------------------------

_CYCLE_TASK_TITLE_PREFIX = "Soul cycle:"


def _current_local_hour() -> int | None:
    """Return the current hour (0-23) in Europe/Stockholm, or None if the
    zoneinfo data is unavailable (best-effort — never fatal to the tick).

    Named ``_local`` not ``_cet`` because Europe/Stockholm observes
    summer time (CEST, UTC+2) — the hour returned is Stockholm local,
    which is CET only outside DST. Soul YAMLs should declare
    ``hours_local`` (not ``hours_cet``) to match; ``hours_cet`` is read
    as a fallback for backwards compatibility.
    """
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Stockholm")).hour
    except Exception:  # noqa: BLE001
        return None


def _soul_cycle_cadence(soul: Any) -> dict[str, Any] | None:
    """Read a soul's ``cycle_cadence`` block from its YAML. Returns None if
    the soul has no ``soul_yaml`` or no ``cycle_cadence`` key."""
    import yaml as _yaml

    path = getattr(soul, "soul_yaml", None)
    if not path:
        return None
    from pathlib import Path

    p = Path(path) if not isinstance(path, Path) else path
    if not p.is_file():
        return None
    data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cadence = data.get("cycle_cadence")
    if not isinstance(cadence, dict):
        return None
    return cadence


def _soul_integration_id(soul: Any) -> str | None:
    """Read a soul's top-level ``integration_id`` from its YAML.

    Tool names are ``{integration_id}.{action}`` (e.g. ``linkedin.feed``),
    not ``{role}.{action}`` — the integration_id is the link between a
    soul and the integration whose tools it calls in-loop.
    """
    import yaml as _yaml
    from pathlib import Path

    path = getattr(soul, "soul_yaml", None)
    if not path:
        return None
    p = Path(path) if not isinstance(path, Path) else path
    if not p.is_file():
        return None
    data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data.get("integration_id")


def _find_cycle_project(engine: Any, cadence: dict[str, Any]) -> Any:
    """Find the active project whose name contains
    ``cadence["project_name_substring"]`` (case-insensitive). Returns None
    if no such project or no substring declared."""
    substring = (cadence.get("project_name_substring") or "").strip()
    if not substring:
        return None
    needle = substring.lower()
    for project in engine.projects.list_active():
        if needle in (project.name or "").lower():
            return project
    return None


def _has_pending_cycle_task(
    engine: Any, project_id: str, role: str
) -> bool:
    """True if the project already has a PENDING task assigned to ``role``
    whose title starts with the cycle marker — prevents filing a second
    cycle task in the same hour."""
    marker = _CYCLE_TASK_TITLE_PREFIX
    for task in engine.projects.list_tasks(project_id):
        if (
            _status_value(task.status) == TaskStatus.PENDING.value
            and task.assigned_agent == role
            and (task.title or "").startswith(marker)
        ):
            return True
    return False


def _file_cycle_task(
    engine: Any, project: Any, role: str, cadence: dict[str, Any],
    integration_id: str | None = None,
) -> str | None:
    """File one cycle task for ``role`` in ``project``. Returns the task id
    or None on failure (best-effort).

    ``integration_id`` is threaded into the cycle prompt so tool names are
    correct (``linkedin.feed``, not ``linkedin_growth.feed``). Defaults to
    ``role`` when not provided (backwards compat).
    """
    from kompany.state.models import Task

    title = f"{_CYCLE_TASK_TITLE_PREFIX} {role} daily growth cycle"
    if integration_id and "integration_id" not in cadence:
        cadence = {**cadence, "integration_id": integration_id}
    prompt_body = _cycle_task_prompt(role, cadence)
    task = Task(
        project_id=project.id,
        title=title,
        assigned_agent=role,
    )
    try:
        engine.projects.create_task(task)
        # Stash the prompt body in the task result so the runner/harness
        # can pick it up as the cycle instruction. The runner builds its
        # own prompt from task.title + project.name; the soul agent reads
        # its YAML for the deep playbook, so this body is a fallback hint
        # rather than the primary prompt.
        engine.projects.update_task_status(
            task.id, TaskStatus.PENDING, result={"cycle_prompt": prompt_body}
        )
        engine.audit.record(
            "soul_cycle.filed",
            f"Filed recurring cycle task for {role}",
            detail={"task_id": task.id, "role": role, "project_id": project.id},
            agent_role=role,
            project_id=project.id,
        )
        return task.id
    except Exception:  # noqa: BLE001
        return None


def _cycle_task_prompt(role: str, cadence: dict[str, Any]) -> str:
    """Build the cycle instruction body for the task.

    ``role`` is the soul's role (e.g. ``linkedin_growth``). Tool names are
    ``{integration_id}.{action}`` (e.g. ``linkedin.feed``), NOT
    ``{role}.{action}`` — the integration_id is read from the soul YAML
    and threaded in via ``cadence["integration_id"]`` by the caller. Falls
    back to ``role`` as the integration_id for backwards compatibility.
    """
    integration_id = cadence.get("integration_id") or role
    max_comments = cadence.get("max_comments_per_cycle", 5)
    max_posts = cadence.get("max_original_posts_per_day", 2)
    anti_repeat = cadence.get("anti_repeat_days", 7)
    return (
        f"Run one {role} growth cycle now:\n"
        f"1. Call {integration_id}.feed to discover on-theme posts (use a content-search query, not the home feed).\n"
        f"2. Read the engagement ledger (engaged.jsonl) and skip authors engaged in the last {anti_repeat} days.\n"
        f"3. Call {integration_id}.engage (comment) on up to {max_comments} on-theme posts — substantive, peer tone, no fabrication.\n"
        f"4. If a clearly on-topic, non-pitch, zero-fabrication original post is ready, call {integration_id}.post (max {max_posts}/day).\n"
        f"5. Call {integration_id}.metrics and record the snapshot in the journal.\n"
        f"6. If any tool returns NOT_LOGGED_IN, stop and surface a system alert — do not retry.\n"
        f"Every engage/post is EXTERNAL_ACTION at APPROVAL tier — the engine gates it; propose, do not force.\n"
    )


__all__ = ["TICK_HISTORY_KEEP", "Ticker"]
