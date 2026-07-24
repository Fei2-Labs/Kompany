"""Outward-execution lane-worker (ADR-0008 Step 4).

The daemon can *think* unattended (plan/decide/draft) but, before this lane,
could not *act* outward (publish/post/reply/submit) with no founder session
open. This lane closes that gap WITHOUT sacrificing quality: it drains the
outward queue (the generalized ``channel_outbox``), resolves each action's
pre-authorization mode, runs the mandatory pre-flight quality gates, and
either executes via a project-supplied :class:`OutwardExecutor` or PARKS the
action for ``kompany approve``. It NEVER silently drops or silently sends.

Per-dispatch flow (``OutwardLane.dispatch_once``):

1. **Suspend gate** — a suspended runtime is the founder brake; no dispatch.
2. **Lease** — take the outward lane's ADR-0005 lease so two ticks cannot
   double-send the same action. A live lease ⇒ ``lane_busy``.
3. **Pull** — the oldest queued outward action (``draft`` / ``queued``).
   Empty queue ⇒ no-op (the engine's default behaviour is unchanged).
4. **Resolve** — ``resolve_outward_mode`` (policy + hard floors).
   ``gated`` ⇒ PARK (file an approval card, mark the row ``parked``).
5. **Pre-flight** — ``auto`` runs ``run_preflight``. Any HOLD ⇒ PARK carrying
   the holds. ``LLMUnavailable`` during pre-flight ⇒ health event + PARK
   (never a silent success), consistent with ``lane_dispatcher``.
6. **Execute** — a passing ``auto`` action is routed to the discovered
   executor for its channel. No executor ⇒ PARK with reason ``no executor``.
   Result marks the row ``sent`` / ``failed``.

Logic lives here; the engine wires one :class:`OutwardLane` and the ticker
calls :meth:`dispatch_once` (a no-op on an empty queue, so existing
ticker/lane tests are unaffected).
"""

from __future__ import annotations

import logging
from typing import Any

from kompany.channels.outbox import OutboxStore
from kompany.core.drain import get_drain_registry
from kompany.core.outward_policy import MODE_AUTO, resolve_outward_mode
from kompany.core.outward_preflight import run_preflight
from kompany.core.run_context import run_scope
from kompany.core.watchdog import LLMUnavailable
from kompany.state.models import ApprovalRequest

log = logging.getLogger(__name__)

# Dedicated lane id so the outward lane's lease never collides with the
# ``main`` work lane (ADR-0005). The lane is registered lazily (it is not a
# "healthy work lane" the dispatcher schedules — it is driven by the ticker).
OUTWARD_LANE_ID = "outward"

# Approval action type for a parked outward action. Wired into
# core/approval_effects.py so `kompany approve` re-enters / discards it.
ACTION_OUTWARD_PARK = "outward_action"

# Lease TTL: a single send is short, but a slow executor (CDP/browser) can
# take a while; long enough to cover that, short enough that a crashed lane
# frees within a few ticks.
DEFAULT_OUTWARD_LEASE_TTL_SECONDS = 600


class OutwardLane:
    """Drains the outward queue one action per dispatch, gated + leased."""

    def __init__(
        self,
        engine: Any,
        registry: Any,
        lease_ttl_seconds: int = DEFAULT_OUTWARD_LEASE_TTL_SECONDS,
    ):
        self._engine = engine
        self._registry = registry
        self.lease_ttl_seconds = int(lease_ttl_seconds)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch_once(self) -> list[str]:
        """Process AT MOST one queued outward action. Returns action tags."""
        runtime = self._engine.runtime.get() or {}
        if runtime.get("state") == "suspended":
            return ["idle_suspended"]

        self._ensure_lane()
        if self._registry.has_active_lease(OUTWARD_LANE_ID):
            return [f"lane_busy:{OUTWARD_LANE_ID}"]

        store = OutboxStore(self._engine.db)
        row = store.next_queued()
        if row is None:
            return []  # empty queue → no-op (no behaviour change)

        if not self._registry.acquire_lease(
            OUTWARD_LANE_ID,
            task_id=None,
            run_id=None,
            ttl_seconds=self.lease_ttl_seconds,
        ):
            # Lost the lease race; leave the row queued for the next pass.
            return [f"lane_busy:{OUTWARD_LANE_ID}"]
        try:
            return self._process(store, row)
        finally:
            self._registry.release_lease(OUTWARD_LANE_ID)

    # ------------------------------------------------------------------
    # One action
    # ------------------------------------------------------------------

    def _process(self, store: OutboxStore, row: dict[str, Any]) -> list[str]:
        row_id = row["id"]
        action_class = (row.get("action_class") or "").strip()
        mode = resolve_outward_mode(
            self._engine.outward_policies,
            action_class,
            side_effect=row.get("side_effect") or "",
            estimated_cost_usd=float(row.get("estimated_cost_usd") or 0.0),
            founder_cost_cap=self._founder_cost_cap(),
        )
        if mode != MODE_AUTO:
            self._park(store, row, reason="gated by policy", holds=[])
            return [f"outward_parked:{row_id}"]

        # auto → mandatory pre-flight gates. LLMUnavailable is a FAILURE,
        # never a silent success (ADR-0005 / lane_dispatcher parity).
        try:
            with run_scope():
                result = run_preflight(self._engine, row)
        except LLMUnavailable as exc:
            self._record_health(row, exc)
            self._park(
                store, row,
                reason=f"pre-flight unavailable: {exc}", holds=[],
            )
            return [f"outward_failed:{row_id}"]

        if not result.ok:
            self._park(
                store, row,
                reason="pre-flight HOLD",
                holds=self._holds_summary(result.holds),
            )
            return [f"outward_parked:{row_id}"]

        executor = self._executor_for(row.get("channel") or "")
        if executor is None:
            self._park(store, row, reason="no executor", holds=[])
            return [f"outward_parked:{row_id}"]

        return self._execute(store, row, executor)

    def _execute(
        self, store: OutboxStore, row: dict[str, Any], executor: Any
    ) -> list[str]:
        row_id = row["id"]
        try:
            # Drain tracking: this is the real "connector call" the
            # deployment plan's ready_for_restart check waits on.
            with get_drain_registry().track("connector_call"):
                outcome = executor.execute(dict(row)) or {}
        except Exception as exc:  # noqa: BLE001 — executor failure is loud, not silent
            store.set_status(row_id, "failed")
            self._audit(
                "outward_queue.failed",
                f"Outward executor raised for {row.get('channel')}",
                {"outbox_id": row_id, "error": str(exc)[:500]},
            )
            return [f"outward_failed:{row_id}"]

        ok = bool(outcome.get("ok"))
        external_ref = outcome.get("external_ref")
        if external_ref:
            store.set_external_ref(row_id, str(external_ref))
        store.set_status(row_id, "sent" if ok else "failed")
        self._audit(
            "outward_queue.sent" if ok else "outward_queue.failed",
            f"Outward action {'sent' if ok else 'failed'} on "
            f"{row.get('channel')}",
            {
                "outbox_id": row_id,
                "detail": str(outcome.get("detail") or "")[:500],
                "external_ref": external_ref,
            },
        )
        return [f"outward_sent:{row_id}" if ok else f"outward_failed:{row_id}"]

    # ------------------------------------------------------------------
    # Park (reuse the approval-card mechanism)
    # ------------------------------------------------------------------

    def _park(
        self,
        store: OutboxStore,
        row: dict[str, Any],
        *,
        reason: str,
        holds: list[str],
    ) -> None:
        """Mark the row parked and file one ``outward_action`` approval card."""
        row_id = row["id"]
        request = self._engine.approvals.create(
            ApprovalRequest(
                action_type=ACTION_OUTWARD_PARK,
                summary=(
                    f"Approve outward {row.get('channel')} action "
                    f"({reason}): {(row.get('text') or '')[:80]}"
                ),
                payload={
                    "outbox_id": row_id,
                    "channel": row.get("channel"),
                    "text": row.get("text"),
                    "action_class": row.get("action_class"),
                    "reason": reason,
                    "holds": holds,
                },
                requested_by="outward_lane",
                severity="medium",
            )
        )
        store.set_approval_id(row_id, request.id)
        store.set_status(row_id, "parked")
        self._audit(
            "outward_queue.parked",
            f"Outward action parked for approval ({reason})",
            {"outbox_id": row_id, "approval_id": request.id, "reason": reason},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _executor_for(self, channel: str) -> Any | None:
        """First discovered executor whose channel / kind matches ``channel``.

        The engine ships none, so an engine with no outward plugin returns
        ``None`` and the action is parked with reason ``no executor``.
        """
        for ex in self._discovered_executors():
            ex_channel = (
                getattr(ex, "channel", "") or getattr(ex, "kind", "")
            ).strip().lower()
            if ex_channel and ex_channel == (channel or "").strip().lower():
                return ex
        # A single channel-agnostic executor (no channel set) handles all.
        for ex in self._discovered_executors():
            if not (getattr(ex, "channel", "") or "").strip():
                return ex
        return None

    def _discovered_executors(self) -> list[Any]:
        cached = getattr(self._engine, "outward_executors", None)
        if cached is not None:
            return list(cached)
        from kompany.plugins.loader import registered

        try:
            return list(registered("outward_executor"))
        except Exception:  # noqa: BLE001 — a broken plugin scan parks, never crashes
            return []

    def _founder_cost_cap(self) -> float:
        getter = getattr(self._engine, "_get_float_config", None)
        if callable(getter):
            try:
                return float(getter("outward_auto_cost_cap_usd", default=0.0))
            except Exception:  # noqa: BLE001
                return 0.0
        return float(
            getattr(self._engine.settings, "outward_auto_cost_cap_usd", 0.0)
            or 0.0
        )

    @staticmethod
    def _holds_summary(holds: list[Any]) -> list[str]:
        summary: list[str] = []
        for h in holds:
            gate = getattr(h, "gate", "") or ""
            findings = getattr(h, "findings", None) or []
            if findings:
                summary.extend(f"[{gate}] {f}" for f in findings)
            elif gate:
                summary.append(f"[{gate}] held")
        return summary

    def _ensure_lane(self) -> None:
        if self._registry.get(OUTWARD_LANE_ID) is not None:
            return
        try:
            self._registry.db.execute(
                """INSERT OR IGNORE INTO lanes
                       (lane_id, name, status, max_concurrent)
                   VALUES (?, ?, 'healthy', 1)""",
                (OUTWARD_LANE_ID, "outward"),
            )
            self._registry.db.commit()
        except Exception:  # noqa: BLE001 — lease still guards correctness
            log.debug("outward lane registration failed", exc_info=True)

    def _record_health(self, row: dict[str, Any], exc: Exception) -> None:
        health = getattr(self._engine, "health_events", None)
        if health is None:
            return
        try:
            health.record(
                "lane_timeout",
                detail={
                    "lane_id": OUTWARD_LANE_ID,
                    "outbox_id": row.get("id"),
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                },
            )
        except Exception:  # noqa: BLE001 — health write must not mask the park
            log.debug("outward health record failed", exc_info=True)

    def _audit(self, kind: str, message: str, detail: dict[str, Any]) -> None:
        audit = getattr(self._engine, "audit", None)
        if audit is None:
            return
        try:
            audit.record(kind, message, detail=detail)
        except Exception:  # noqa: BLE001 — auditing is best-effort
            log.debug("outward audit record failed", exc_info=True)


__all__ = [
    "ACTION_OUTWARD_PARK",
    "DEFAULT_OUTWARD_LEASE_TTL_SECONDS",
    "OUTWARD_LANE_ID",
    "OutwardLane",
]
