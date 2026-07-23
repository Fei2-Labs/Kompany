from __future__ import annotations

import pytest

from kompany.core.credential_broker import (
    CredentialActionRequired,
    CredentialAction,
    CredentialBrokerClient,
    CredentialBrokerError,
    CredentialApprovalError,
    CredentialScopeError,
    CredentialLeaseError,
    FakeCredentialBroker,
    HttpCredentialBroker,
    SecretRef,
    LeaseRequest,
)
from kompany.state.models import Project, ProjectStatus, Task, TaskStatus
from tests.test_engine_channel import _build_engine


def test_engine_issues_opaque_scoped_lease_without_returning_secret(
    tmp_path,
    monkeypatch,
):
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    secret_ref = SecretRef(
        id="secret://vinted/social-login",
        company_id="default",
        project_id="vinted",
        connector="browser",
        allowed_actions=("login",),
        allowed_agent_ids=("cmo",),
        allowed_destinations=("vinted.com",),
    )
    backend = FakeCredentialBroker({
        secret_ref.id: (secret_ref, "never-return-this-secret"),
    })
    engine.credential_broker = CredentialBrokerClient(backend)

    lease = engine.request_secret_lease(
        secret_ref.id,
        company_id="default",
        project_id="vinted",
        agent_id="cmo",
        worker_id="worker-1",
        connector="browser",
        action="login",
        destination="vinted.com",
        ttl_seconds=300,
        max_uses=1,
    )

    assert lease.secret_ref_id == secret_ref.id
    assert lease.project_id == "vinted"
    assert lease.worker_id == "worker-1"
    assert lease.connector == "browser"
    assert lease.action == "login"
    assert lease.destination == "vinted.com"
    assert lease.max_uses == 1
    assert "never-return-this-secret" not in lease.model_dump_json()


def test_engine_requires_matching_approval_for_one_time_worker_lease(
    tmp_path,
    monkeypatch,
):
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    secret_ref = SecretRef(
        id="secret://vinted/fallback-login",
        company_id="default",
        project_id="vinted",
        connector="isolated-worker",
        allowed_actions=("login",),
        allowed_agent_ids=("cmo",),
        allowed_destinations=("vinted.com",),
        requires_approval=True,
    )
    backend = FakeCredentialBroker({
        secret_ref.id: (secret_ref, "worker-only-secret"),
    })
    engine.credential_broker = CredentialBrokerClient(backend)
    request = {
        "company_id": "default",
        "project_id": "vinted",
        "agent_id": "cmo",
        "worker_id": "worker-1",
        "connector": "isolated-worker",
        "action": "login",
        "destination": "vinted.com",
        "ttl_seconds": 300,
        "max_uses": 1,
    }

    with pytest.raises(CredentialActionRequired) as blocked:
        engine.request_secret_lease(secret_ref.id, **request)

    action = blocked.value.action
    assert action.kind == "approval"
    assert action.resumable is True
    assert action.approval_id
    engine.approvals.approve(action.approval_id, approved_by="founder")

    lease = engine.request_secret_lease(
        secret_ref.id,
        approval_id=action.approval_id,
        **request,
    )

    assert lease.status == "active"
    with pytest.raises(CredentialApprovalError):
        engine.request_secret_lease(
            secret_ref.id,
            approval_id=action.approval_id,
            **request,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("company_id", "other-company"),
        ("project_id", "other-project"),
        ("agent_id", "ceo"),
        ("connector", "shell"),
        ("action", "export"),
        ("destination", "example.com"),
        ("ttl_seconds", 601),
        ("max_uses", 2),
    ],
)
def test_engine_rejects_lease_requests_outside_secret_ref_scope(
    tmp_path,
    monkeypatch,
    field,
    value,
):
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    secret_ref = SecretRef(
        id="secret://vinted/social-login",
        company_id="default",
        project_id="vinted",
        connector="browser",
        allowed_actions=("login",),
        allowed_agent_ids=("cmo",),
        allowed_destinations=("vinted.com",),
        max_ttl_seconds=600,
        max_uses=1,
    )
    engine.credential_broker = CredentialBrokerClient(
        FakeCredentialBroker({
            secret_ref.id: (secret_ref, "never-return-this-secret"),
        })
    )
    request = {
        "company_id": "default",
        "project_id": "vinted",
        "agent_id": "cmo",
        "worker_id": "worker-1",
        "connector": "browser",
        "action": "login",
        "destination": "vinted.com",
        "ttl_seconds": 300,
        "max_uses": 1,
    }
    request[field] = value

    with pytest.raises(CredentialScopeError):
        engine.request_secret_lease(secret_ref.id, **request)


def test_engine_persists_credential_blocker_and_resumes_same_task(
    tmp_path,
    monkeypatch,
):
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    project = Project(
        id="vinted",
        name="Vinted",
        type="operational",
        status=ProjectStatus.ACTIVE,
    )
    task = Task(
        id="task-vinted-login",
        project_id=project.id,
        title="Log in to Vinted",
        assigned_agent="cmo",
        status=TaskStatus.ACTIVE,
    )
    engine.projects.create(project)
    engine.projects.create_task(task)
    secret_ref = SecretRef(
        id="secret://vinted/social-login",
        company_id="default",
        project_id=project.id,
        connector="browser",
        allowed_actions=("login",),
        allowed_agent_ids=("cmo",),
        allowed_destinations=("vinted.com",),
    )
    engine.credential_broker = CredentialBrokerClient(
        FakeCredentialBroker({
            secret_ref.id: (secret_ref, "never-return-this-secret"),
        })
    )
    lease_request = LeaseRequest(
        secret_ref_id=secret_ref.id,
        company_id="default",
        project_id=project.id,
        agent_id="cmo",
        worker_id="worker-1",
        connector="browser",
        action="login",
        destination="vinted.com",
        ttl_seconds=300,
        max_uses=1,
    )
    action = CredentialAction(
        id="action-1",
        kind="reauth",
        reason="Browser session expired",
        action_url="https://broker.example/actions/action-1",
    )

    engine.block_task_for_credential(task.id, action, lease_request)

    blocked = engine.projects.get_task(task.id)
    assert blocked is not None
    assert blocked.status == TaskStatus.BLOCKED
    assert blocked.result["credential_action"]["id"] == action.id
    assert "never-return-this-secret" not in str(blocked.result)

    resumed = engine.resume_credential_task(task.id, action.id)

    assert resumed.id == task.id
    assert resumed.status == TaskStatus.PENDING


def test_resume_replaces_blocker_when_broker_requires_next_action(
    tmp_path,
    monkeypatch,
):
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    project = Project(
        id="vinted",
        name="Vinted",
        type="operational",
        status=ProjectStatus.ACTIVE,
    )
    task = Task(
        id="task-vinted-login",
        project_id=project.id,
        title="Log in to Vinted",
        assigned_agent="cmo",
        status=TaskStatus.ACTIVE,
    )
    engine.projects.create(project)
    engine.projects.create_task(task)
    secret_ref = SecretRef(
        id="secret://vinted/social-login",
        company_id="default",
        project_id=project.id,
        connector="browser",
        allowed_actions=("login",),
        allowed_agent_ids=("cmo",),
        allowed_destinations=("vinted.com",),
    )
    backend = FakeCredentialBroker({
        secret_ref.id: (secret_ref, "never-return-this-secret"),
    })
    engine.credential_broker = CredentialBrokerClient(backend)
    request = LeaseRequest(
        secret_ref_id=secret_ref.id,
        company_id="default",
        project_id=project.id,
        agent_id="cmo",
        worker_id="worker-1",
        connector="browser",
        action="login",
        destination="vinted.com",
        ttl_seconds=300,
        max_uses=1,
    )
    unlock = CredentialAction(
        id="action-unlock",
        kind="unlock",
        reason="Unlock required",
    )
    engine.block_task_for_credential(task.id, unlock, request)
    mfa = CredentialAction(
        id="action-mfa",
        kind="mfa",
        reason="MFA required",
    )

    def require_mfa(_request):
        raise CredentialActionRequired(mfa)

    backend.preflight = require_mfa

    with pytest.raises(CredentialActionRequired):
        engine.resume_credential_task(task.id, unlock.id)

    blocked = engine.projects.get_task(task.id)
    assert blocked is not None
    assert blocked.status == TaskStatus.BLOCKED
    assert blocked.result["credential_action"]["id"] == mfa.id


def test_http_broker_maps_provider_action_to_structured_core_blocker():
    calls = []

    def transport(method, path, payload, headers):
        calls.append((method, path, payload, headers))
        return {
            "status": "action_required",
            "action": {
                "id": "action-unlock",
                "kind": "unlock",
                "reason": "Credential store is locked",
                "action_url": "https://broker.example/actions/action-unlock",
            },
        }

    backend = HttpCredentialBroker(
        "https://broker.example",
        auth_token="broker-token",
        transport=transport,
    )
    request = LeaseRequest(
        secret_ref_id="secret://vinted/social-login",
        company_id="default",
        project_id="vinted",
        agent_id="cmo",
        worker_id="worker-1",
        connector="browser",
        action="login",
        destination="vinted.com",
        ttl_seconds=300,
        max_uses=1,
    )

    with pytest.raises(CredentialActionRequired) as blocked:
        backend.preflight(request)

    assert blocked.value.action.kind == "unlock"
    assert blocked.value.action.resumable is True
    assert calls[0][0:2] == ("POST", "/v1/credentials/preflight")
    assert calls[0][2] == request.model_dump(mode="json")
    assert calls[0][3]["Authorization"] == "Bearer broker-token"


def test_engine_configures_external_broker_from_environment(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "KOMPANY_CREDENTIAL_BROKER_ENDPOINT",
        "https://broker.example",
    )
    monkeypatch.setenv(
        "KOMPANY_CREDENTIAL_BROKER_TOKEN",
        "broker-token",
    )
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("KOMPANY_VAULT_KEY", "test-vault-key")

    from kompany.core.engine import KompanyEngine

    engine = KompanyEngine()

    assert isinstance(engine.credential_broker.backend, HttpCredentialBroker)
    assert engine.credential_broker.backend.endpoint == "https://broker.example"


def test_engine_enforces_lease_use_limit_and_revocation(
    tmp_path,
    monkeypatch,
):
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    secret_ref = SecretRef(
        id="secret://vinted/social-login",
        company_id="default",
        project_id="vinted",
        connector="browser",
        allowed_actions=("login",),
        allowed_agent_ids=("cmo",),
        allowed_destinations=("vinted.com",),
    )
    backend = FakeCredentialBroker({
        secret_ref.id: (secret_ref, "never-return-this-secret"),
    })
    engine.credential_broker = CredentialBrokerClient(backend)
    request = {
        "company_id": "default",
        "project_id": "vinted",
        "agent_id": "cmo",
        "worker_id": "worker-1",
        "connector": "browser",
        "action": "login",
        "destination": "vinted.com",
        "ttl_seconds": 300,
        "max_uses": 1,
    }
    used_lease = engine.request_secret_lease(secret_ref.id, **request)

    exhausted = engine.consume_secret_lease(
        used_lease.id,
        worker_id="worker-1",
    )

    assert exhausted.uses_remaining == 0
    with pytest.raises(CredentialLeaseError):
        engine.consume_secret_lease(
            used_lease.id,
            worker_id="worker-1",
        )

    revoked_lease = engine.request_secret_lease(secret_ref.id, **request)
    engine.revoke_secret_lease(revoked_lease.id)

    with pytest.raises(CredentialLeaseError):
        engine.validate_secret_lease(
            revoked_lease.id,
            worker_id="worker-1",
        )


def test_http_broker_rejects_plaintext_without_echoing_it():
    leaked_value = "must-not-appear-in-errors"

    def transport(method, path, payload, headers):
        return {
            "id": "lease-1",
            "secret_ref_id": "secret://vinted/social-login",
            "company_id": "default",
            "project_id": "vinted",
            "agent_id": "cmo",
            "worker_id": "worker-1",
            "connector": "browser",
            "action": "login",
            "destination": "vinted.com",
            "expires_at": "2026-07-23T16:00:00Z",
            "max_uses": 1,
            "uses_remaining": 1,
            "status": "active",
            "secret": leaked_value,
        }

    backend = HttpCredentialBroker(
        "https://broker.example",
        transport=transport,
    )
    request = LeaseRequest(
        secret_ref_id="secret://vinted/social-login",
        company_id="default",
        project_id="vinted",
        agent_id="cmo",
        worker_id="worker-1",
        connector="browser",
        action="login",
        destination="vinted.com",
        ttl_seconds=300,
        max_uses=1,
    )

    with pytest.raises(CredentialBrokerError) as rejected:
        backend.issue_lease(request)

    assert leaked_value not in str(rejected.value)


def test_core_rejects_broker_lease_that_expands_requested_scope():
    secret_ref = SecretRef(
        id="secret://vinted/social-login",
        company_id="default",
        project_id="vinted",
        connector="browser",
        allowed_actions=("login",),
        allowed_agent_ids=("cmo",),
        allowed_destinations=("vinted.com",),
    )
    backend = FakeCredentialBroker({
        secret_ref.id: (secret_ref, "never-return-this-secret"),
    })
    original_issue = backend.issue_lease

    def expanded_issue(request):
        lease = original_issue(request)
        return lease.model_copy(update={"worker_id": "other-worker"})

    backend.issue_lease = expanded_issue
    client = CredentialBrokerClient(backend)
    request = LeaseRequest(
        secret_ref_id=secret_ref.id,
        company_id="default",
        project_id="vinted",
        agent_id="cmo",
        worker_id="worker-1",
        connector="browser",
        action="login",
        destination="vinted.com",
        ttl_seconds=300,
        max_uses=1,
    )

    with pytest.raises(CredentialScopeError):
        client.issue_lease(request)


def test_http_broker_reports_declared_capabilities():
    def transport(method, path, payload, headers):
        assert (method, path, payload) == ("GET", "/v1/health", None)
        return {
            "status": "available",
            "capabilities": ["scoped_leases", "action_required"],
            "supports_worker_delivery": True,
        }

    backend = HttpCredentialBroker(
        "https://broker.example",
        transport=transport,
    )

    health = backend.health()

    assert health.status == "available"
    assert health.supports_worker_delivery is True
    assert health.capabilities == ("scoped_leases", "action_required")


def test_ambiguous_issuance_failure_does_not_release_approval(
    tmp_path,
    monkeypatch,
):
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    secret_ref = SecretRef(
        id="secret://vinted/fallback-login",
        company_id="default",
        project_id="vinted",
        connector="isolated-worker",
        allowed_actions=("login",),
        allowed_agent_ids=("cmo",),
        allowed_destinations=("vinted.com",),
        requires_approval=True,
    )
    backend = FakeCredentialBroker({
        secret_ref.id: (secret_ref, "worker-only-secret"),
    })
    engine.credential_broker = CredentialBrokerClient(backend)
    request = {
        "company_id": "default",
        "project_id": "vinted",
        "agent_id": "cmo",
        "worker_id": "worker-1",
        "connector": "isolated-worker",
        "action": "login",
        "destination": "vinted.com",
        "ttl_seconds": 300,
        "max_uses": 1,
    }
    with pytest.raises(CredentialActionRequired) as blocked:
        engine.request_secret_lease(secret_ref.id, **request)
    approval_id = blocked.value.action.approval_id
    assert approval_id
    engine.approvals.approve(approval_id, approved_by="founder")

    def ambiguous_failure(_request):
        raise CredentialBrokerError("broker timeout")

    backend.issue_lease = ambiguous_failure
    with pytest.raises(CredentialBrokerError):
        engine.request_secret_lease(
            secret_ref.id,
            approval_id=approval_id,
            **request,
        )

    row = engine.db.execute(
        """SELECT status FROM credential_approval_consumptions
           WHERE approval_id = ?""",
        (approval_id,),
    ).fetchone()
    assert row["status"] == "indeterminate"
    with pytest.raises(CredentialApprovalError):
        engine.request_secret_lease(
            secret_ref.id,
            approval_id=approval_id,
            **request,
        )


def test_engine_reports_unavailable_when_broker_health_check_fails(
    tmp_path,
    monkeypatch,
):
    engine = _build_engine(tmp_path, monkeypatch, initialize=True)
    backend = FakeCredentialBroker()

    def unavailable():
        raise CredentialBrokerError("connection failed")

    backend.health = unavailable
    engine.credential_broker = CredentialBrokerClient(backend)

    assert engine.credential_broker_status() == {
        "status": "unavailable",
        "capabilities": [],
        "supports_worker_delivery": False,
    }
