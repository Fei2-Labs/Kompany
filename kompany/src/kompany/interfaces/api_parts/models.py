"""Shared request models split out of api.py (ADR-0003). Verbatim move."""

from __future__ import annotations

from typing import Any  # noqa: F401

from pydantic import BaseModel, ConfigDict, Field  # noqa: F401

class DirectiveRequest(BaseModel):
    text: str
    # CEO-channel session to continue (06-03-ceo-channel). Optional: omitting
    # it opens a fresh session, preserving the legacy one-shot /directive
    # behaviour. A clarify reply passes the session_id from the prior result.
    session_id: str | None = None


class OverrideRequest(BaseModel):
    text: str


class DecisionPacketRequest(BaseModel):
    text: str
    target_amount: float | None = None


class InitRequest(BaseModel):
    name: str
    capital: float = 0.0
    goal: str = ""
    time_horizon: str = ""
    exclusions: str = ""


class DebateRequest(BaseModel):
    question: str


class RejectApprovalRequest(BaseModel):
    reason: str = ""
    comment: str = ""


class ApproveApprovalRequest(BaseModel):
    comment: str = ""


class ReviseApprovalRequest(BaseModel):
    counter: str
    comment: str = ""


class SnoozeApprovalRequest(BaseModel):
    minutes: int
    comment: str = ""


class CancelApprovalRequest(BaseModel):
    reason: str = ""
    comment: str = ""


class CommentApprovalRequest(BaseModel):
    body: str
    by_type: str = "user"
    by_id: str | None = None


class MemoryIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_role: str = Field(..., min_length=1, max_length=80)
    content: str = Field(..., min_length=1, max_length=12000)
    category: str = Field(default="observation", min_length=1, max_length=80)
    context: str | None = Field(default=None, max_length=160)
    project_id: str | None = Field(default=None, max_length=80)
    knowledge_type: str = Field(default="experiential", min_length=1, max_length=80)


class HeartbeatRequest(BaseModel):
    dispatch: bool = False
    adapter: str = "dry-run"


class DispatchNotificationsRequest(BaseModel):
    events: list[dict[str, Any]]
    adapter: str = "dry-run"


class ToolPolicyRequest(BaseModel):
    agent_role: str
    tool_name: str
    allowed: bool
    requires_approval: bool = False
    reason: str = ""


class ToolAuthorizationRequest(BaseModel):
    agent_role: str
    tool_name: str
    purpose: str = ""
    arguments: dict[str, Any] = {}
    approval_id: str | None = None


class RemoteCommandAPIRequest(BaseModel):
    source: str = "mobile"
    text: str
    chat_id: str = ""
    bearer_token: str = ""
    payload: dict[str, Any] = {}


class RemoteReplayCleanupRequest(BaseModel):
    ttl_seconds: int | None = None


class DashboardActionRequest(BaseModel):
    action: str
    approval_id: str | None = None
    reason: str = ""


class CredentialRequest(BaseModel):
    name: str
    value: str


class CredentialKeyRotationRequest(BaseModel):
    new_vault_key: str
