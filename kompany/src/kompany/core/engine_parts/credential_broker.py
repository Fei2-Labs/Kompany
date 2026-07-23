"""Engine boundary for provider-neutral credential leases."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import sqlite3
from uuid import uuid4

from kompany.core.credential_broker import (
    CredentialAction,
    CredentialActionRequired,
    CredentialApprovalError,
    CredentialBrokerError,
    CredentialLeaseError,
    LeaseRequest,
    SecretLease,
)
from kompany.state.models import ApprovalRequest, ApprovalStatus
from kompany.state.models import Task, TaskStatus
from kompany.core.event_hub import get_event_hub


class CredentialBrokerMixin:
    def credential_broker_status(self) -> dict:
        try:
            health = self.credential_broker.health()
        except CredentialBrokerError:
            from kompany.core.credential_broker import BrokerCapabilities

            health = BrokerCapabilities(status="unavailable")
        return health.model_dump(mode="json")

    def request_secret_lease(
        self,
        secret_ref_id: str,
        *,
        company_id: str,
        project_id: str,
        agent_id: str,
        worker_id: str,
        connector: str,
        action: str,
        destination: str,
        ttl_seconds: int,
        max_uses: int,
        approval_id: str | None = None,
        task_id: str | None = None,
    ) -> SecretLease:
        request = LeaseRequest(
            secret_ref_id=secret_ref_id,
            company_id=company_id,
            project_id=project_id,
            agent_id=agent_id,
            worker_id=worker_id,
            connector=connector,
            action=action,
            destination=destination,
            ttl_seconds=ttl_seconds,
            max_uses=max_uses,
            approval_id=approval_id,
        )
        claimed_approval_id: str | None = None
        reservation_owner: str | None = None
        try:
            preflight = self.credential_broker.preflight(request)
            if preflight.ref.requires_approval:
                claimed_approval_id = self._require_credential_approval(
                    request
                )
                reservation_owner = self._claim_credential_approval(
                    claimed_approval_id,
                    request,
                )
                request = request.model_copy(
                    update={
                        "idempotency_key": (
                            f"credential-approval:{claimed_approval_id}"
                        )
                    }
                )
            lease = self.credential_broker.issue_lease(request)
            if claimed_approval_id and reservation_owner:
                self._consume_credential_approval(
                    claimed_approval_id,
                    reservation_owner,
                    lease.id,
                )
        except CredentialActionRequired as exc:
            if claimed_approval_id and reservation_owner:
                self._mark_credential_approval_indeterminate(
                    claimed_approval_id,
                    reservation_owner,
                )
            if task_id:
                blocked_request = request
                if exc.action.approval_id:
                    blocked_request = request.model_copy(
                        update={"approval_id": exc.action.approval_id}
                    )
                self.block_task_for_credential(
                    task_id,
                    exc.action,
                    blocked_request,
                )
            raise
        except Exception:
            if claimed_approval_id and reservation_owner:
                self._mark_credential_approval_indeterminate(
                    claimed_approval_id,
                    reservation_owner,
                )
            raise
        self.audit.record(
            "credential_lease.issued",
            "Issued scoped credential lease",
            detail={
                "lease_id": lease.id,
                "secret_ref_id": lease.secret_ref_id,
                "worker_id": lease.worker_id,
                "connector": lease.connector,
                "action": lease.action,
                "destination": lease.destination,
                "expires_at": lease.expires_at.isoformat(),
                "max_uses": lease.max_uses,
            },
            agent_role=agent_id,
            project_id=project_id,
        )
        return lease

    def block_task_for_credential(
        self,
        task_id: str,
        action: CredentialAction,
        request: LeaseRequest,
    ) -> Task:
        task = self.projects.get_task(task_id)
        if task is None:
            raise ValueError(f"task {task_id!r} not found")
        if task.project_id != request.project_id:
            raise CredentialApprovalError(
                "credential blocker project does not match the task"
            )
        result = {
            "credential_action": action.model_dump(mode="json"),
            "credential_lease_request": request.model_dump(mode="json"),
        }
        self.projects.update_task_status(task_id, TaskStatus.BLOCKED, result)
        self.audit.record(
            "credential.action_required",
            action.reason,
            detail={
                "task_id": task_id,
                "delegation_id": task.delegation_id,
                "action": action.model_dump(mode="json"),
                "secret_ref_id": request.secret_ref_id,
            },
            agent_role=request.agent_id,
            project_id=request.project_id,
        )
        get_event_hub().publish(
            "credential.action_required",
            {
                "delegation_id": task.delegation_id,
                "task_id": task_id,
                "action": action.model_dump(mode="json"),
            },
        )
        blocked = self.projects.get_task(task_id)
        if blocked is None:
            raise RuntimeError("credential blocker persistence failed")
        return blocked

    def resume_credential_task(self, task_id: str, action_id: str) -> Task:
        task = self.projects.get_task(task_id)
        if task is None:
            raise ValueError(f"task {task_id!r} not found")
        result = task.result or {}
        action = result.get("credential_action") or {}
        raw_request = result.get("credential_lease_request")
        if (
            task.status != TaskStatus.BLOCKED
            or action.get("id") != action_id
            or not raw_request
        ):
            raise CredentialApprovalError(
                "credential action does not match the blocked task"
            )
        request = LeaseRequest.model_validate(raw_request)
        try:
            preflight = self.credential_broker.preflight(request)
            if preflight.ref.requires_approval:
                self._require_credential_approval(request)
        except CredentialActionRequired as exc:
            blocked_request = request
            if exc.action.approval_id:
                blocked_request = request.model_copy(
                    update={"approval_id": exc.action.approval_id}
                )
            self.block_task_for_credential(
                task_id,
                exc.action,
                blocked_request,
            )
            raise
        self.projects.update_task_status_raw(
            task_id,
            TaskStatus.PENDING.value,
            {
                **result,
                "credential_resumed": True,
            },
        )
        self.audit.record(
            "credential.task_resumed",
            "Resumed task after credential preflight",
            detail={"task_id": task_id, "action_id": action_id},
            agent_role=request.agent_id,
            project_id=request.project_id,
        )
        resumed = self.projects.get_task(task_id)
        if resumed is None:
            raise RuntimeError("credential task resume persistence failed")
        return resumed

    def list_credential_action_events(self) -> list[dict]:
        rows = self.db.execute(
            """SELECT id FROM tasks
               WHERE status = 'blocked' AND result IS NOT NULL"""
        ).fetchall()
        events = []
        for row in rows:
            task = self.projects.get_task(row["id"])
            if task is None or not task.delegation_id:
                continue
            action = (task.result or {}).get("credential_action")
            if action:
                events.append({
                    "delegation_id": task.delegation_id,
                    "task_id": task.id,
                    "action": action,
                })
        return events

    def get_credential_action_for_delegation(
        self,
        delegation_id: str,
    ) -> dict | None:
        for event in self.list_credential_action_events():
            if event["delegation_id"] == delegation_id:
                return event
        return None

    def validate_secret_lease(
        self,
        lease_id: str,
        *,
        worker_id: str,
    ) -> SecretLease:
        lease = self.credential_broker.get_lease(lease_id)
        if (
            lease.status != "active"
            or lease.expires_at <= datetime.now(UTC)
            or lease.uses_remaining <= 0
            or lease.worker_id != worker_id
        ):
            raise CredentialLeaseError("secret lease is not usable")
        return lease

    def consume_secret_lease(
        self,
        lease_id: str,
        *,
        worker_id: str,
    ) -> SecretLease:
        self.validate_secret_lease(lease_id, worker_id=worker_id)
        lease = self.credential_broker.consume_lease(lease_id)
        self.audit.record(
            "credential_lease.consumed",
            "Consumed one credential lease use",
            detail={
                "lease_id": lease.id,
                "uses_remaining": lease.uses_remaining,
            },
            agent_role=lease.agent_id,
            project_id=lease.project_id,
        )
        return lease

    def revoke_secret_lease(self, lease_id: str) -> SecretLease:
        lease = self.credential_broker.revoke_lease(lease_id)
        self.audit.record(
            "credential_lease.revoked",
            "Revoked credential lease",
            detail={"lease_id": lease.id},
            agent_role=lease.agent_id,
            project_id=lease.project_id,
        )
        return lease

    def _require_credential_approval(self, request: LeaseRequest) -> str:
        payload = request.model_dump(
            exclude={"approval_id", "idempotency_key"}
        )
        approval = (
            self.approvals.get(request.approval_id)
            if request.approval_id
            else None
        )
        if approval is None and request.approval_id:
            raise CredentialApprovalError(
                "credential lease approval does not exist"
            )
        if approval is None:
            approval = self.approvals.create(
                ApprovalRequest(
                    action_type="credential_lease",
                    summary=(
                        f"Allow {request.agent_id} to use "
                        f"{request.connector} for {request.action}"
                    ),
                    payload=payload,
                    project_id=request.project_id,
                    requested_by=request.agent_id,
                    severity="high",
                )
            )
        if approval.status != ApprovalStatus.APPROVED:
            raise CredentialActionRequired(
                CredentialAction(
                    kind="approval",
                    reason="A founder must approve this credential lease",
                    approval_id=approval.id,
                )
            )
        if (
            approval.action_type != "credential_lease"
            or approval.project_id != request.project_id
            or approval.payload != payload
            or not approval.resolved_by
        ):
            raise CredentialApprovalError(
                "credential lease approval does not match the request scope"
            )
        return approval.id

    def _claim_credential_approval(
        self,
        approval_id: str,
        request: LeaseRequest,
    ) -> str:
        fingerprint = hashlib.sha256(
            request.model_dump_json(
                exclude={"approval_id", "idempotency_key"},
            ).encode("utf-8")
        ).hexdigest()
        reservation_owner = uuid4().hex
        try:
            self.db.execute(
                """INSERT INTO credential_approval_consumptions
                   (approval_id, request_fingerprint, reservation_owner,
                    status)
                   VALUES (?, ?, ?, 'reserved')""",
                (approval_id, fingerprint, reservation_owner),
            )
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            raise CredentialApprovalError(
                "credential lease approval has already been used"
            ) from exc
        return reservation_owner

    def _consume_credential_approval(
        self,
        approval_id: str,
        reservation_owner: str,
        lease_id: str,
    ) -> None:
        cursor = self.db.execute(
            """UPDATE credential_approval_consumptions
               SET status = 'consumed', lease_id = ?,
                   consumed_at = datetime('now')
               WHERE approval_id = ? AND reservation_owner = ?
                 AND status = 'reserved'""",
            (lease_id, approval_id, reservation_owner),
        )
        self.db.commit()
        if cursor.rowcount != 1:
            raise CredentialApprovalError(
                "credential lease approval reservation was lost"
            )

    def _mark_credential_approval_indeterminate(
        self,
        approval_id: str,
        reservation_owner: str,
    ) -> None:
        self.db.execute(
            """UPDATE credential_approval_consumptions
               SET status = 'indeterminate'
               WHERE approval_id = ? AND reservation_owner = ?
                 AND status = 'reserved'""",
            (approval_id, reservation_owner),
        )
        self.db.commit()


__all__ = ["CredentialBrokerMixin"]
