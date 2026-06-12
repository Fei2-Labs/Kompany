"""Runtime state, heartbeat, notifications, remote commands.

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations


from kompany.core.subscription_fee import book_subscription_fee_if_due
from kompany.notifications import build_notifier
from kompany.remote import RemoteCommandRequest, RemoteCommandResult, parse_remote_text
from kompany.state.models import HeartbeatReport, NotificationEvent



class RuntimeOpsMixin:
    def get_runtime_state(self) -> dict:
        """Return the current persisted runtime state."""
        return self.runtime.get()

    def heartbeat_once(
        self,
        dispatch: bool = False,
        adapter: str = "dry-run",
    ) -> dict:
        """Inspect runtime state and emit notification-ready events."""
        runtime = self.get_runtime_state()
        approvals = self.list_approvals()
        active_projects = self.projects.list_active()
        notifications: list[NotificationEvent] = []

        if runtime["state"] == "suspended":
            notifications.append(NotificationEvent(
                kind="runtime_suspended",
                severity="warning",
                summary=f"Kompany runtime is suspended: {runtime['reason'] or 'unknown'}",
                payload=runtime,
            ))
        if approvals:
            notifications.append(NotificationEvent(
                kind="pending_approvals",
                severity="action_required",
                summary=f"{len(approvals)} approval request(s) awaiting user decision.",
                payload={"approval_ids": [a["id"] for a in approvals]},
            ))
        if active_projects:
            notifications.append(NotificationEvent(
                kind="active_projects",
                severity="info",
                summary=f"{len(active_projects)} active project(s) in progress.",
                payload={"project_ids": [p.id for p in active_projects]},
            ))
        fee_event = self._book_subscription_fee_if_due()
        if fee_event is not None:
            notifications.append(fee_event)

        report = HeartbeatReport(
            runtime=runtime,
            pending_approvals=len(approvals),
            active_projects=len(active_projects),
            notifications=notifications,
        )
        payload = report.model_dump(mode="json")
        self.audit.record(
            "heartbeat.tick",
            "Heartbeat checked runtime, approvals, and projects",
            detail={
                "runtime_state": runtime["state"],
                "pending_approvals": len(approvals),
                "active_projects": len(active_projects),
                "notifications": len(notifications),
            },
        )
        for event in payload["notifications"]:
            self.audit.record(
                "notification.emitted",
                event["summary"],
                detail=event,
            )
        if dispatch:
            payload["deliveries"] = self.dispatch_notifications(
                payload["notifications"],
                adapter=adapter,
            )
        return payload

    def _book_subscription_fee_if_due(self) -> NotificationEvent | None:
        """Book the monthly ModelSource subscription fee, once per month.

        Thin delegate — logic + injectable clock live in
        :mod:`kompany.core.subscription_fee` (engine.py is over the file
        size cap; new concerns go in siblings).
        """
        return book_subscription_fee_if_due(self.settings, self.db, self.ledger)

    def dispatch_notifications(
        self,
        events: list[dict],
        adapter: str = "dry-run",
    ) -> list[dict]:
        """Dispatch notification events through a configured adapter."""
        notifier = build_notifier(self.settings, adapter=adapter)
        deliveries = []
        for event in events:
            delivery = notifier.send(event).model_dump(mode="json")
            audit_detail = {k: v for k, v in delivery.items() if k != "error"}
            if delivery.get("error"):
                audit_detail["error"] = delivery["error"]
            self.audit.record(
                "notification.dispatched",
                f"Notification dispatch {delivery['status']}: {event['summary']}",
                detail=audit_detail,
            )
            deliveries.append(delivery)
        return deliveries

    def handle_remote_command(self, request: RemoteCommandRequest | dict) -> dict:
        """Authenticate and execute a bounded inbound remote command."""
        if isinstance(request, dict):
            request = RemoteCommandRequest.model_validate(request)
        auth_error = self._remote_auth_error(request)
        command, args = parse_remote_text(request.text)
        if auth_error:
            result = RemoteCommandResult(
                source=request.source,
                status="denied",
                command=command,
                message=auth_error,
            ).model_dump(mode="json")
            self.audit.record(
                "remote_command.denied",
                f"Remote command denied: {request.source}:{command}",
                detail={"source": request.source, "command": command, "reason": auth_error},
            )
            return result

        replay_key = self._remote_replay_key(request)
        if replay_key:
            self.cleanup_remote_replays()
            replayed = self.remote_replay.get(request.source, replay_key)
            if replayed is not None:
                self.audit.record(
                    "remote_command.replayed",
                    f"Remote command replayed: {request.source}:{command}",
                    detail={"source": request.source, "command": command},
                )
                return {**replayed, "replayed": True}

        result = self._execute_remote_command(request.source, command, args)
        result["replayed"] = False
        if replay_key:
            self.remote_replay.store(request.source, replay_key, command, result)
        self.audit.record(
            f"remote_command.{result['status']}",
            f"Remote command {result['status']}: {request.source}:{command}",
            detail={"source": request.source, "command": command, "status": result["status"]},
        )
        return result

    def cleanup_remote_replays(self, ttl_seconds: int | None = None) -> dict:
        ttl = ttl_seconds
        if ttl is None:
            ttl = self.settings.remote_replay_ttl_seconds
        result = self.remote_replay.cleanup(ttl)
        self.audit.record(
            "remote_command.replay_cleanup",
            "Remote command replay cache cleaned up",
            detail={
                "deleted": result["deleted"],
                "remaining": result["remaining"],
                "ttl_seconds": result["ttl_seconds"],
                "cutoff": result["cutoff"],
            },
        )
        return result

    def _remote_replay_key(self, request: RemoteCommandRequest) -> str:
        payload = request.payload or {}
        if payload.get("nonce") is not None:
            return str(payload["nonce"])
        if payload.get("request_id") is not None:
            return str(payload["request_id"])
        if request.source == "telegram" and payload.get("update_id") is not None:
            return str(payload["update_id"])
        return ""

    def _remote_auth_error(self, request: RemoteCommandRequest) -> str:
        if request.source == "telegram":
            allowed = {
                chat_id.strip()
                for chat_id in self.settings.telegram_allowed_chat_ids.split(",")
                if chat_id.strip()
            }
            if not allowed:
                return "telegram remote control is not configured"
            if request.chat_id not in allowed:
                return "telegram chat is not authorized"
            return ""
        if request.source == "mobile":
            expected = self.settings.mobile_remote_token
            if not expected:
                return "mobile remote control is not configured"
            if request.bearer_token != expected:
                return "mobile bearer token is invalid"
            return ""
        return "remote source is not supported"

    def _execute_remote_command(
        self,
        source: str,
        command: str,
        args: list[str],
    ) -> dict:
        if command in {"help", ""}:
            return RemoteCommandResult(
                source=source,
                status="executed",
                command="help",
                message="Supported commands: status, approvals, approve <id>, reject <id> [reason], heartbeat, help",
                result={
                    "commands": ["status", "approvals", "approve", "reject", "heartbeat", "help"],
                },
            ).model_dump(mode="json")
        if command == "status":
            return RemoteCommandResult(
                source=source,
                status="executed",
                command=command,
                message="Kompany status snapshot",
                result=self.observability_snapshot(),
            ).model_dump(mode="json")
        if command == "approvals":
            approvals = self.list_approvals()
            return RemoteCommandResult(
                source=source,
                status="executed",
                command=command,
                message=f"{len(approvals)} pending approval(s)",
                result={"approvals": approvals},
            ).model_dump(mode="json")
        if command == "heartbeat":
            return RemoteCommandResult(
                source=source,
                status="executed",
                command=command,
                message="Heartbeat report",
                result=self.heartbeat_once(),
            ).model_dump(mode="json")
        if command == "approve" and args:
            approval = self.approve_request(args[0])
            return RemoteCommandResult(
                source=source,
                status="executed" if approval else "unknown_command",
                command=command,
                message="Approval updated" if approval else f"Approval '{args[0]}' not found",
                result=approval,
            ).model_dump(mode="json")
        if command == "reject" and args:
            reason = " ".join(args[1:]) if len(args) > 1 else "remote rejection"
            approval = self.reject_request(args[0], reason=reason)
            return RemoteCommandResult(
                source=source,
                status="executed" if approval else "unknown_command",
                command=command,
                message="Approval rejected" if approval else f"Approval '{args[0]}' not found",
                result=approval,
            ).model_dump(mode="json")
        return RemoteCommandResult(
            source=source,
            status="unknown_command",
            command=command,
            message="Unknown or incomplete remote command. Send 'help'.",
        ).model_dump(mode="json")

