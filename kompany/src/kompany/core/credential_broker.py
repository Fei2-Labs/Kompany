"""Provider-neutral credential references and short-lived secret leases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from typing import Any, Callable, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CredentialBrokerError(RuntimeError):
    pass


class CredentialScopeError(CredentialBrokerError):
    pass


class CredentialApprovalError(CredentialBrokerError):
    pass


class CredentialLeaseError(CredentialBrokerError):
    pass


class CredentialAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    kind: Literal["unlock", "mfa", "reauth", "replace", "approval"]
    reason: str
    approval_id: str | None = None
    action_url: str | None = None
    resumable: bool = True


class CredentialActionRequired(CredentialBrokerError):
    def __init__(self, action: CredentialAction):
        super().__init__(action.reason)
        self.action = action


class SecretRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    company_id: str
    project_id: str
    connector: str
    allowed_actions: tuple[str, ...]
    allowed_agent_ids: tuple[str, ...]
    allowed_destinations: tuple[str, ...]
    max_ttl_seconds: int = Field(default=300, gt=0, le=3600)
    max_uses: int = Field(default=1, gt=0, le=10)
    requires_approval: bool = False


class LeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret_ref_id: str
    company_id: str
    project_id: str
    agent_id: str
    worker_id: str
    connector: str
    action: str
    destination: str
    ttl_seconds: int = Field(gt=0, le=3600)
    max_uses: int = Field(gt=0, le=10)
    approval_id: str | None = None
    idempotency_key: str | None = None


class SecretLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    secret_ref_id: str
    company_id: str
    project_id: str
    agent_id: str
    worker_id: str
    connector: str
    action: str
    destination: str
    expires_at: datetime
    max_uses: int
    uses_remaining: int
    status: str = "active"


class BrokerPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: SecretRef


class BrokerCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "unconfigured", "unavailable"]
    capabilities: tuple[str, ...] = ()
    supports_worker_delivery: bool = False


class CredentialBroker(Protocol):
    def health(self) -> BrokerCapabilities: ...

    def preflight(self, request: LeaseRequest) -> BrokerPreflight: ...

    def issue_lease(self, request: LeaseRequest) -> SecretLease: ...

    def get_lease(self, lease_id: str) -> SecretLease: ...

    def consume_lease(self, lease_id: str) -> SecretLease: ...

    def revoke_lease(self, lease_id: str) -> SecretLease: ...


class CredentialBrokerClient:
    """Core-side broker client; lease responses never contain plaintext."""

    def __init__(self, backend: CredentialBroker):
        self.backend = backend

    def issue_lease(self, request: LeaseRequest) -> SecretLease:
        lease = self.backend.issue_lease(request)
        expected = (
            request.secret_ref_id,
            request.company_id,
            request.project_id,
            request.agent_id,
            request.worker_id,
            request.connector,
            request.action,
            request.destination,
        )
        actual = (
            lease.secret_ref_id,
            lease.company_id,
            lease.project_id,
            lease.agent_id,
            lease.worker_id,
            lease.connector,
            lease.action,
            lease.destination,
        )
        max_expiry = datetime.now(UTC) + timedelta(
            seconds=request.ttl_seconds + 5
        )
        if (
            actual != expected
            or lease.status != "active"
            or lease.max_uses > request.max_uses
            or lease.uses_remaining > lease.max_uses
            or lease.uses_remaining <= 0
            or lease.expires_at <= datetime.now(UTC)
            or lease.expires_at > max_expiry
        ):
            raise CredentialScopeError(
                "credential broker lease exceeds the requested scope"
            )
        return lease

    def health(self) -> BrokerCapabilities:
        return self.backend.health()

    def preflight(self, request: LeaseRequest) -> BrokerPreflight:
        preflight = self.backend.preflight(request)
        ref = preflight.ref
        in_scope = (
            request.secret_ref_id == ref.id
            and request.company_id == ref.company_id
            and request.project_id == ref.project_id
            and request.connector == ref.connector
            and request.agent_id in ref.allowed_agent_ids
            and request.action in ref.allowed_actions
            and request.destination in ref.allowed_destinations
            and request.ttl_seconds <= ref.max_ttl_seconds
            and request.max_uses <= ref.max_uses
        )
        if not in_scope:
            raise CredentialScopeError(
                "secret lease request exceeds the reference scope"
            )
        return preflight

    def get_lease(self, lease_id: str) -> SecretLease:
        return self.backend.get_lease(lease_id)

    def consume_lease(self, lease_id: str) -> SecretLease:
        return self.backend.consume_lease(lease_id)

    def revoke_lease(self, lease_id: str) -> SecretLease:
        return self.backend.revoke_lease(lease_id)


class FakeCredentialBroker:
    """Deterministic in-memory broker for tests."""

    def __init__(
        self,
        secrets: dict[str, tuple[SecretRef, str]] | None = None,
    ):
        self._secrets = dict(secrets or {})
        self._leases: dict[str, SecretLease] = {}

    def issue_lease(self, request: LeaseRequest) -> SecretLease:
        ref = self._validated_ref(request)
        lease = SecretLease(
            secret_ref_id=ref.id,
            company_id=request.company_id,
            project_id=request.project_id,
            agent_id=request.agent_id,
            worker_id=request.worker_id,
            connector=request.connector,
            action=request.action,
            destination=request.destination,
            expires_at=(
                datetime.now(UTC)
                + timedelta(seconds=request.ttl_seconds)
            ),
            max_uses=request.max_uses,
            uses_remaining=request.max_uses,
        )
        self._leases[lease.id] = lease
        return lease

    def health(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            status="available",
            capabilities=(
                "scoped_leases",
                "revocation",
                "action_required",
            ),
            supports_worker_delivery=True,
        )

    def preflight(self, request: LeaseRequest) -> BrokerPreflight:
        entry = self._secrets.get(request.secret_ref_id)
        if entry is None:
            raise CredentialBrokerError(
                f"secret reference {request.secret_ref_id!r} not found"
            )
        ref, _secret = entry
        return BrokerPreflight(ref=ref)

    def _validated_ref(self, request: LeaseRequest) -> SecretRef:
        entry = self._secrets.get(request.secret_ref_id)
        if entry is None:
            raise CredentialBrokerError(
                f"secret reference {request.secret_ref_id!r} not found"
            )
        ref, _secret = entry
        expected = (
            ref.company_id,
            ref.project_id,
            ref.connector,
        )
        actual = (
            request.company_id,
            request.project_id,
            request.connector,
        )
        if actual != expected or request.action not in ref.allowed_actions:
            raise CredentialScopeError(
                "secret lease request exceeds the reference scope"
            )
        return ref

    def get_lease(self, lease_id: str) -> SecretLease:
        lease = self._leases.get(lease_id)
        if lease is None:
            raise CredentialLeaseError(f"secret lease {lease_id!r} not found")
        return lease.model_copy(deep=True)

    def consume_lease(self, lease_id: str) -> SecretLease:
        lease = self.get_lease(lease_id)
        if (
            lease.status != "active"
            or lease.expires_at <= datetime.now(UTC)
            or lease.uses_remaining <= 0
        ):
            raise CredentialLeaseError("secret lease is not usable")
        lease.uses_remaining -= 1
        if lease.uses_remaining == 0:
            lease.status = "exhausted"
        self._leases[lease_id] = lease
        return lease.model_copy(deep=True)

    def revoke_lease(self, lease_id: str) -> SecretLease:
        lease = self.get_lease(lease_id)
        lease.status = "revoked"
        lease.uses_remaining = 0
        self._leases[lease_id] = lease
        return lease.model_copy(deep=True)


class UnavailableCredentialBroker:
    def health(self) -> BrokerCapabilities:
        return BrokerCapabilities(status="unconfigured")

    def preflight(self, request: LeaseRequest) -> BrokerPreflight:
        raise CredentialBrokerError(
            "no external credential broker is configured"
        )

    def issue_lease(self, request: LeaseRequest) -> SecretLease:
        raise CredentialBrokerError(
            "no external credential broker is configured"
        )

    def get_lease(self, lease_id: str) -> SecretLease:
        raise CredentialBrokerError(
            "no external credential broker is configured"
        )

    consume_lease = get_lease
    revoke_lease = get_lease


BrokerTransport = Callable[
    [str, str, dict[str, Any] | None, dict[str, str]],
    dict[str, Any],
]
_PLAINTEXT_RESPONSE_KEYS = {
    "credential",
    "password",
    "plaintext",
    "raw_secret",
    "secret",
    "secret_value",
}


class HttpCredentialBroker:
    """Client for a customer-operated provider-neutral broker endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        auth_token: str = "",
        timeout_seconds: float = 10.0,
        transport: BrokerTransport | None = None,
    ):
        endpoint = endpoint.rstrip("/")
        parsed = urlparse(endpoint)
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and loopback
        ):
            raise ValueError(
                "credential broker endpoint must use HTTPS or loopback HTTP"
            )
        self.endpoint = endpoint
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._request

    def preflight(self, request: LeaseRequest) -> BrokerPreflight:
        payload = self._call(
            "POST",
            "/v1/credentials/preflight",
            request.model_dump(mode="json"),
        )
        self._raise_action(payload)
        return self._validate_response(BrokerPreflight, payload)

    def health(self) -> BrokerCapabilities:
        payload = self._call("GET", "/v1/health", None)
        return self._validate_response(BrokerCapabilities, payload)

    def issue_lease(self, request: LeaseRequest) -> SecretLease:
        payload = self._call(
            "POST",
            "/v1/credentials/leases",
            request.model_dump(mode="json"),
        )
        self._raise_action(payload)
        return self._validate_response(SecretLease, payload)

    def get_lease(self, lease_id: str) -> SecretLease:
        payload = self._call(
            "GET",
            f"/v1/credentials/leases/{lease_id}",
            None,
        )
        return self._validate_response(SecretLease, payload)

    def consume_lease(self, lease_id: str) -> SecretLease:
        payload = self._call(
            "POST",
            f"/v1/credentials/leases/{lease_id}/consume",
            {},
        )
        return self._validate_response(SecretLease, payload)

    def revoke_lease(self, lease_id: str) -> SecretLease:
        payload = self._call(
            "POST",
            f"/v1/credentials/leases/{lease_id}/revoke",
            {},
        )
        return self._validate_response(SecretLease, payload)

    def _call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        try:
            response = self._transport(method, path, payload, headers)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise CredentialBrokerError(
                f"credential broker request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(response, dict):
            raise CredentialBrokerError(
                "credential broker returned an invalid response"
            )
        self._reject_plaintext_fields(response)
        return response

    @staticmethod
    def _reject_plaintext_fields(payload: dict[str, Any]) -> None:
        pending: list[Any] = [payload]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if _PLAINTEXT_RESPONSE_KEYS.intersection(value):
                    raise CredentialBrokerError(
                        "credential broker response contained forbidden plaintext"
                    )
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)

    @staticmethod
    def _raise_action(payload: dict[str, Any]) -> None:
        if payload.get("status") == "action_required":
            try:
                action = CredentialAction.model_validate(
                    payload.get("action") or {}
                )
            except ValidationError:
                raise CredentialBrokerError(
                    "credential broker returned an invalid action"
                ) from None
            raise CredentialActionRequired(action)

    @staticmethod
    def _validate_response(
        model: type[BaseModel],
        payload: dict[str, Any],
    ) -> Any:
        if set(payload) - set(model.model_fields):
            raise CredentialBrokerError(
                "credential broker returned an invalid response"
            )
        try:
            return model.model_validate(payload)
        except ValidationError:
            raise CredentialBrokerError(
                "credential broker returned an invalid response"
            ) from None

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload).encode("utf-8")
            if payload is not None
            else None
        )
        request_headers = {
            **headers,
            "Content-Type": "application/json",
        }
        request = Request(
            f"{self.endpoint}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise
            if isinstance(parsed, dict):
                return parsed
            raise
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CredentialBrokerError(
                "credential broker returned invalid JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise CredentialBrokerError(
                "credential broker returned an invalid response"
            )
        return parsed


__all__ = [
    "CredentialBroker",
    "CredentialBrokerClient",
    "CredentialBrokerError",
    "CredentialApprovalError",
    "CredentialLeaseError",
    "CredentialAction",
    "CredentialActionRequired",
    "CredentialScopeError",
    "BrokerPreflight",
    "BrokerCapabilities",
    "FakeCredentialBroker",
    "HttpCredentialBroker",
    "LeaseRequest",
    "SecretLease",
    "SecretRef",
    "UnavailableCredentialBroker",
]
