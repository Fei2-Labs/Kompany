from __future__ import annotations

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kompany.interfaces.api import app
from kompany.interfaces.cli import app as cli_app
from kompany.interfaces.sdk import Kompany


class FakeCFO:
    def get_summary(self):
        return {
            "balance": 42.0,
            "total_income": 50.0,
            "total_expenses": -8.0,
            "total_ai_costs": -0.125,
        }


class FakeProjects:
    def list_active(self):
        return []


class FakeRegistry:
    def get(self, role):
        assert role == "cfo"
        return FakeCFO()


class FakeDebateRegistry:
    def get(self, role):
        return object()


class FakeDebatePart:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


class FakeDebateResult:
    question = "Should we launch?"
    rounds = [[FakeDebatePart({"agent_name": "CTO", "recommendation": "Yes"})]]
    synthesis = FakeDebatePart({"recommended_option": "Launch"})
    decision = FakeDebatePart({"decision": "Launch now"})


class FakeEngine:
    def __init__(self):
        self.settings = type(
            "Settings",
            (),
            {
                "company_name": "TestCo",
                "company_goal": "AI tools",
                "company_stage": "solo",
                "company_time_horizon": "12 months",
                "company_exclusions": "",
                "web_dashboard_token": "",
            },
        )()
        self.registry = FakeRegistry()
        self.projects = FakeProjects()
        self.init_calls = []
        self._approval = {
            "id": "app-1",
            "status": "pending",
            "action_type": "directive_execution",
            "summary": "Approve directive",
            "payload": {},
        }
        self._runtime = {"state": "running", "reason": None, "since": None}

    def initialize_company(self, name, capital, goal="", time_horizon="", exclusions=""):
        self.init_calls.append((name, capital, goal, time_horizon, exclusions))

    def get_company_state(self):
        return {"name": "TestCo"}

    def resume_project(self, project_id):
        return {
            "status": "resumed",
            "project_id": project_id,
            "latest_checkpoint": {
                "id": 1,
                "project_id": project_id,
                "task_id": "task-1",
                "step_index": 1,
                "state": {"last_completed_task": "task-1"},
            },
            "tasks_completed": 1,
            "tasks_failed": 0,
            "total_ai_cost": 0.0,
            "outputs": [],
            "fully_funded": False,
        }

    def prepare_decision_packet(self, text, target_amount=None):
        return {
            "id": "packet-1",
            "raw_input": text,
            "status": "awaiting_approval",
            "approval_id": "app-1",
            "revenue_proposal": {"owner": "cro", "summary": "proposal"},
            "financial_evaluation": {"owner": "cfo", "viable": True},
            "synthesis": {"owner": "cos", "recommendation": "approve"},
            "ceo_approval": {"owner": "ceo", "approved_direction": "prepare"},
            "execution_plan": {"owner": "coo", "steps": []},
        }

    def process_override(self, text):
        return {
            "status": "awaiting_approval",
            "approval_id": "app-1",
            "briefing": {
                "summary": f"Override requested: {text}",
                "risks": ["May invalidate the current plan or assumptions."],
                "required_confirmation": "Approve only after accepting these risks.",
                "will_execute_immediately": False,
            },
        }

    def list_approvals(self):
        return [self._approval] if self._approval["status"] == "pending" else []

    def approve_request(self, approval_id, approved_by="master", comment_body=None):
        if approval_id != self._approval["id"] or self._approval["status"] != "pending":
            return None
        self._approval = {**self._approval, "status": "approved", "resolved_by": approved_by}
        return self._approval

    def reject_request(self, approval_id, rejected_by="master", reason="", comment_body=None):
        if approval_id != self._approval["id"] or self._approval["status"] != "pending":
            return None
        self._approval = {**self._approval, "status": "rejected", "resolved_by": rejected_by, "resolution_reason": reason}
        return self._approval

    def observability_snapshot(self):
        return {
            "status": "ok",
            "company": {
                "name": "TestCo",
                "goal": "AI tools",
                "stage": "solo",
                "time_horizon": "12 months",
                "exclusions": "",
            },
            "runtime": self.get_runtime_state(),
            "finance": {
                "balance": 42.0,
                "total_income": 50.0,
                "total_expenses": -8.0,
                "total_ai_costs": 0.125,
            },
            "approvals": {"pending": 1, "items": self.list_approvals(), "blockers": []},
            "projects": {"active": 0, "items": [], "task_totals": {"pending": 0, "active": 0, "completed": 0, "failed": 0}},
            "agents": {"total": 16, "active": 1, "items": [{"role": "coo", "status": "dispatching", "current_task": "Run project", "updated_at": None}]},
            "tools": {"policies": 2, "allowed": 1, "denied": 1},
            "notifications": [],
            "office": {
                "theme": "virtual_company_floor",
                "rooms": [
                    {
                        "name": "operations_room",
                        "purpose": "Execution, delivery, and project coordination.",
                        "characters": [{"role": "coo", "room": "operations_room", "status": "dispatching", "current_task": "Run project", "updated_at": None}],
                    }
                ],
                "active_projects": [],
                "blockers": [],
            },
            "checked_at": "2026-05-16T09:45:00",
        }

    def get_runtime_state(self):
        return getattr(self, "_runtime", {"state": "running", "reason": None, "since": None})

    def heartbeat_once(self, dispatch=False, adapter="dry-run"):
        runtime = self.get_runtime_state()
        payload = {
            "status": "ok",
            "runtime": runtime,
            "pending_approvals": len(self.list_approvals()),
            "active_projects": len(self.projects.list_active()),
            "notifications": [
                {
                    "kind": "pending_approvals",
                    "severity": "action_required",
                    "summary": "1 approval request(s) awaiting user decision.",
                    "payload": {"approval_ids": ["app-1"]},
                }
            ],
            "checked_at": "2026-05-15T12:12:00",
        }
        if dispatch:
            payload["deliveries"] = self.dispatch_notifications(
                payload["notifications"],
                adapter=adapter,
            )
        return payload

    def dispatch_notifications(self, events, adapter="dry-run"):
        return [
            {
                "adapter": adapter,
                "status": "dry_run" if adapter == "dry-run" else "sent",
                "kind": event["kind"],
                "summary": event["summary"],
                "destination": "",
                "error": None,
                "provider_message_id": None,
                "payload": {"severity": event.get("severity", "info")},
            }
            for event in events
        ]

    def suspend(self, reason="manual"):
        self._runtime = {"state": "suspended", "reason": reason, "since": "2026-05-15T12:10:00"}
        return {**self._runtime, "status": "suspended"}

    def resume(self):
        self._runtime = {"state": "running", "reason": None, "since": "2026-05-15T12:11:00"}
        return {**self._runtime, "status": "resumed"}

    def create_backup(self, label="manual"):
        return {
            "id": "20260515T120000-" + label.replace(" ", "-"),
            "label": label,
            "kind": "manual",
            "path": "/tmp/backup.db",
            "size_bytes": 1024,
            "created_at": "2026-05-15T12:00:00",
        }

    def list_backups(self):
        return [
            {
                "id": "20260515T120000-manual",
                "label": "manual",
                "kind": "manual",
                "path": "/tmp/backup.db",
                "size_bytes": 1024,
                "created_at": "2026-05-15T12:00:00",
            }
        ]

    def restore_backup(self, backup_id):
        if backup_id != "20260515T120000-manual":
            raise FileNotFoundError(f"Backup '{backup_id}' not found")
        return {
            "id": backup_id,
            "label": "manual",
            "kind": "manual",
            "path": "/tmp/backup.db",
            "size_bytes": 1024,
            "created_at": "2026-05-15T12:00:00",
            "restored_at": "2026-05-15T12:05:00",
            "auto_pre_restore_id": "20260515T120500-pre-restore",
        }

    def run_retrospective(self, project_id):
        return {
            "project_id": project_id,
            "status": "recorded",
            "summary": "Build CRM",
            "tasks_completed": 2,
            "tasks_failed": 0,
            "reflections": [
                {"agent_role": "coo", "content": "ran 2 tasks"},
                {"agent_role": "researcher", "content": "ran 2 tasks"},
            ],
            "created_at": "2026-05-15T11:45:00",
        }

    def list_memories(self, agent_role, limit=20, include_stale=False, knowledge_type=None, category=None):
        if agent_role != "coo":
            return []
        return [
            {
                "id": 1,
                "agent_role": "coo",
                "category": category or "reflection",
                "knowledge_type": knowledge_type or "experiential",
                "content": "Project X completed",
                "context": "project:proj-1",
                "directive_id": None,
                "created_at": "2026-05-15T11:45:00",
                "valid_until": None,
            }
        ]

    def list_credentials(self):
        return [
            {
                "name": "telegram_bot_token",
                "configured": True,
                "updated_at": "2026-05-16T10:00:00Z",
            }
        ]

    def set_credential(self, name, value):
        return {
            "name": name,
            "configured": True,
            "updated_at": "2026-05-16T10:01:00Z",
        }

    def delete_credential(self, name):
        return {"name": name, "deleted": True}

    def rotate_credential_key(self, new_vault_key):
        return {"rotated": 1, "names": ["telegram_bot_token"]}

    def list_tool_policies(self, agent_role=None):
        policies = [
            {
                "agent_role": "researcher",
                "tool_name": "web_search",
                "allowed": True,
                "requires_approval": False,
                "reason": "Researcher may search public docs.",
                "updated_at": "2026-05-16T09:30:00",
            },
            {
                "agent_role": "subagent",
                "tool_name": "external_network",
                "allowed": False,
                "requires_approval": False,
                "reason": "Subagents cannot call external network tools directly.",
                "updated_at": "2026-05-16T09:30:00",
            },
        ]
        if agent_role:
            return [p for p in policies if p["agent_role"] == agent_role]
        return policies

    def set_tool_policy(self, agent_role, tool_name, allowed, reason="", requires_approval=False):
        return {
            "agent_role": agent_role,
            "tool_name": tool_name,
            "allowed": allowed,
            "requires_approval": requires_approval,
            "reason": reason,
            "updated_at": "2026-05-16T09:31:00",
        }

    def authorize_tool(self, agent_role, tool_name, purpose=""):
        allowed = agent_role == "researcher" and tool_name == "web_search"
        return {
            "agent_role": agent_role,
            "tool_name": tool_name,
            "allowed": allowed,
            "status": "allowed" if allowed else "denied",
            "reason": "Researcher may search public docs." if allowed else "No policy exists for this agent role and tool.",
            "result": None,
        }

    def use_tool(self, agent_role, tool_name, purpose="", arguments=None, approval_id=None):
        auth = self.authorize_tool(agent_role, tool_name, purpose=purpose)
        if not auth["allowed"]:
            return auth
        return {**auth, "status": "allowed", "approval_id": approval_id}

    def handle_remote_command(self, request):
        if not isinstance(request, dict):
            request = request.model_dump()
        command = request["text"].strip().split()[0].removeprefix("/") if request["text"].strip() else "help"
        if request["source"] == "mobile" and request.get("bearer_token") != "mobile-secret":
            return {
                "source": request["source"],
                "status": "denied",
                "command": command,
                "message": "mobile bearer token is invalid",
                "result": None,
                "replayed": False,
            }
        return {
            "source": request["source"],
            "status": "executed",
            "command": command,
            "message": "ok",
            "result": {"ok": True, "payload": request.get("payload", {})},
            "replayed": False,
        }

    def cleanup_remote_replays(self, ttl_seconds=None):
        return {
            "deleted": 2,
            "remaining": 1,
            "ttl_seconds": 3600 if ttl_seconds is None else ttl_seconds,
            "cutoff": "2026-05-16 09:00:00",
        }

    def release_delivery(self, approval_id):
        if approval_id != "app-delivery":
            raise ValueError(f"Approval '{approval_id}' is not a delivery_approval")
        return {
            "approval_id": approval_id,
            "project_id": "proj-1",
            "packet_id": "packet-1",
            "status": "delivered",
            "tasks_completed": 2,
            "tasks_failed": 0,
            "outputs": [],
            "reviews": [],
            "released_at": "2026-05-15T11:30:00",
            "released_by": "master",
            "notes": "",
        }

    def execute_decision_packet(self, approval_id):
        if approval_id != "app-approved":
            raise ValueError(f"Approval '{approval_id}' is not approved")
        return {
            "project_id": "proj-1",
            "approval_id": approval_id,
            "packet_id": "packet-1",
            "status": "awaiting_delivery_approval",
            "tasks_completed": 2,
            "tasks_failed": 0,
            "outputs": [],
            "reviews": [
                {"owner": "cro", "verdict": "approved", "notes": "ok"},
                {"owner": "cfo", "verdict": "approved", "notes": "ok"},
                {"owner": "cos", "verdict": "approved", "notes": "ok"},
                {"owner": "ceo", "verdict": "approved", "notes": "ok"},
            ],
            "delivery_approval_id": "app-delivery",
            "total_ai_cost": 0.0,
        }

    def reject_request(self, approval_id, rejected_by="master", reason=None, comment_body=None):
        if approval_id != self._approval["id"] or self._approval["status"] != "pending":
            return None
        self._approval = {
            **self._approval,
            "status": "rejected",
            "resolved_by": rejected_by,
            "resolution_reason": reason,
        }
        return self._approval


class FakeSDKEngine(FakeEngine):
    pass


runner = CliRunner()
client = TestClient(app)


def test_directive_llm_failure_returns_graceful_200(monkeypatch):
    """A directive whose LLM call blows up must NOT surface as a raw
    HTTP 500. The endpoint catches it and returns 200 + status=failed +
    a classified error_code so the timeline shows the real cause.
    Regression: founder saw "directive failed: HTTP 500" when swedeapi
    rejected gpt-5.5 with 'Param Incorrect / invalid_request_error'."""
    fake_engine = FakeEngine()

    def boom(_text):
        raise RuntimeError(
            "LLM 'gpt-5.5' unavailable after retry: BadRequestError: "
            '{"error":{"message":"Param Incorrect",'
            '"type":"invalid_request_error"}} upstream_error'
        )

    fake_engine.process_directive = boom  # type: ignore[assignment]
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/directive", json={"text": "abandon the call plan"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "provider_error"
    assert body["message"]


def test_classify_ping_error_buckets_invalid_request_as_provider_error():
    from kompany.interfaces.api import _classify_ping_error

    assert _classify_ping_error("BadRequestError: Param Incorrect") == "provider_error"
    assert _classify_ping_error("invalid_request_error upstream_error") == "provider_error"
    assert _classify_ping_error("AuthenticationError: 401") == "unauthorized"
    assert _classify_ping_error("RateLimitError: 429 quota") == "rate_limited"


def test_sdk_init_returns_full_payload(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.init(
        "Acme",
        capital=12.5,
        goal="AI tools",
        time_horizon="12 months",
        exclusions="crypto",
    )

    assert result == {
        "status": "initialized",
        "name": "Acme",
        "capital": 12.5,
        "goal": "AI tools",
        "time_horizon": "12 months",
        "exclusions": "crypto",
        "stage": "solo",
    }


def test_sdk_observability(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.observability()

    assert result["company"]["name"] == "TestCo"
    assert result["office"]["theme"] == "virtual_company_floor"
    assert result["agents"]["active"] == 1


    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.status()

    assert result["total_ai_costs"] == 0.125
    assert result["goal"] == "AI tools"
    assert result["time_horizon"] == "12 months"


def test_sdk_resume_project(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.resume_project("project-1")

    assert result["status"] == "resumed"
    assert result["project_id"] == "project-1"
    assert result["latest_checkpoint"]["state"]["last_completed_task"] == "task-1"


def test_sdk_decision_packet(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.prepare_decision_packet("Buy computer", target_amount=5000.0)

    assert result["status"] == "awaiting_approval"
    assert result["approval_id"] == "app-1"
    assert result["revenue_proposal"]["owner"] == "cro"


def test_sdk_override_returns_risk_briefing(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.override("Stop project")

    assert result["status"] == "awaiting_approval"
    assert result["approval_id"] == "app-1"
    assert result["briefing"]["will_execute_immediately"] is False


def test_sdk_approval_methods(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    assert sdk.approvals()[0]["id"] == "app-1"
    approved = sdk.approve("app-1")
    assert approved is not None
    assert approved["status"] == "approved"
    assert sdk.approvals() == []


def test_sdk_debate_matches_wire_shape(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    monkeypatch.setattr(
        "kompany.interfaces.sdk.DebateEngine",
        lambda registry, stage: type("FakeDebateEngine", (), {"run": lambda self, question, company_state: FakeDebateResult()})(),
    )
    sdk = Kompany()

    result = sdk.debate("Should we launch?")

    assert result == {
        "question": "Should we launch?",
        "rounds": [[{"agent_name": "CTO", "recommendation": "Yes"}]],
        "synthesis": {"recommended_option": "Launch"},
        "decision": {"decision": "Launch now"},
    }


def test_api_init_returns_full_payload(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/init", json={
        "name": "Acme",
        "capital": 5.0,
        "goal": "AI tools",
        "time_horizon": "12 months",
        "exclusions": "crypto",
    })

    assert response.status_code == 200
    assert response.json() == {
        "status": "initialized",
        "name": "Acme",
        "capital": 5.0,
        "goal": "AI tools",
        "time_horizon": "12 months",
        "exclusions": "crypto",
        "stage": "solo",
    }


def test_api_observability_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.get("/observability")

    assert response.status_code == 200
    assert response.json()["company"]["name"] == "TestCo"
    assert response.json()["office"]["theme"] == "virtual_company_floor"


def test_api_dashboard_returns_503_when_auth_unconfigured(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.get("/dashboard")

    assert response.status_code == 503
    assert response.json() == {"detail": "web dashboard auth is not configured"}


def test_api_dashboard_returns_login_page_for_missing_browser_auth(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.get("/dashboard")

    assert response.status_code == 401
    assert "text/html" in response.headers["content-type"]
    assert "Enter dashboard token" in response.text
    assert "dashboard-secret" not in response.text


def test_api_dashboard_returns_401_for_wrong_query_token(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.get("/dashboard?token=wrong-secret")

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid dashboard token"}
    assert "dashboard-secret" not in response.text
    assert "wrong-secret" not in response.text


def test_api_dashboard_login_rejects_wrong_token_without_echo(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/dashboard/login", data={"dashboard_token": "wrong-secret"})

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid dashboard token"}
    assert "dashboard-secret" not in response.text
    assert "wrong-secret" not in response.text
    assert "kompany_dashboard_session" not in response.cookies


def test_api_dashboard_login_sets_httponly_session_cookie(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post(
        "/dashboard/login",
        data={"dashboard_token": "dashboard-secret"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert "dashboard-secret" not in response.text
    assert "kompany_dashboard_session" in response.cookies
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Max-Age=43200" in set_cookie
    assert "dashboard-secret" not in set_cookie


def test_api_dashboard_html_endpoint_accepts_session_cookie(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    login = client.post(
        "/dashboard/login",
        data={"dashboard_token": "dashboard-secret"},
        follow_redirects=False,
    )
    client.cookies.set(
        "kompany_dashboard_session",
        login.cookies["kompany_dashboard_session"],
    )
    response = client.get("/dashboard")
    client.cookies.clear()

    assert response.status_code == 200
    assert "Kompany RPG Command Center" in response.text
    assert "Log out dashboard session" in response.text
    assert "dashboard-secret" not in response.text


def test_api_dashboard_rejects_expired_session_cookie(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    fake_engine.settings.dashboard_session_ttl_seconds = 1
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)
    monkeypatch.setattr("kompany.interfaces.api.time.time", lambda: 1_000_000)
    login = client.post(
        "/dashboard/login",
        data={"dashboard_token": "dashboard-secret"},
        follow_redirects=False,
    )
    client.cookies.set(
        "kompany_dashboard_session",
        login.cookies["kompany_dashboard_session"],
    )
    monkeypatch.setattr("kompany.interfaces.api.time.time", lambda: 1_000_002)

    response = client.get("/dashboard")
    client.cookies.clear()

    assert response.status_code == 401
    assert "Enter dashboard token" in response.text
    assert "dashboard-secret" not in response.text


def test_api_dashboard_rejects_malformed_session_cookie(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)
    client.cookies.set("kompany_dashboard_session", "not-a-session")

    response = client.get("/dashboard")
    client.cookies.clear()

    assert response.status_code == 401
    assert "Enter dashboard token" in response.text
    assert "dashboard-secret" not in response.text


def test_api_dashboard_logout_clears_session_cookie(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.get("/dashboard/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/login"
    set_cookie = response.headers["set-cookie"]
    assert "kompany_dashboard_session" in set_cookie
    assert "Max-Age=0" in set_cookie
    assert "dashboard-secret" not in set_cookie


def test_api_dashboard_html_endpoint_accepts_bearer_token(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.get(
        "/dashboard",
        headers={"Authorization": "Bearer dashboard-secret"},
    )

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Kompany RPG Command Center" in response.text
    assert "TestCo" in response.text
    assert "Runtime" in response.text
    assert "Balance" in response.text
    assert "operations_room" in response.text
    assert "coo" in response.text
    assert "View raw observability JSON" in response.text
    assert "dashboard-secret" not in response.text


def test_api_dashboard_html_endpoint_includes_live_refresh(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.get(
        "/dashboard",
        headers={"Authorization": "Bearer dashboard-secret"},
    )

    assert response.status_code == 200
    assert 'id="metrics"' in response.text
    assert 'class="hud"' in response.text
    assert 'class="hud-card"' in response.text
    assert 'data-metric="runtime"' in response.text
    assert 'id="blockers"' in response.text
    assert 'id="office"' in response.text
    assert 'id="refresh-status"' in response.text
    assert "fetch('/observability'" in response.text
    assert "setInterval(refreshDashboard, 5000)" in response.text
    assert "Live refresh stale" in response.text
    assert "Kompany RPG Command Center" in response.text
    assert "Quest blockers" in response.text
    assert "Rooms, agents, and active quests" in response.text
    assert "RPG action console" in response.text
    assert 'id="action-result"' in response.text
    assert 'data-dashboard-action="runtime-status"' in response.text
    assert 'data-dashboard-action="heartbeat"' in response.text
    assert 'data-dashboard-action="approvals"' in response.text
    assert 'data-dashboard-action="replay-cleanup"' in response.text
    assert 'data-dashboard-action="approve"' in response.text
    assert 'data-dashboard-action="reject"' in response.text
    assert 'data-dashboard-action="runtime-suspend"' in response.text
    assert 'data-dashboard-action="runtime-resume"' in response.text
    assert "fetch('/dashboard/action'" in response.text
    assert "credentials: 'same-origin'" in response.text
    assert 'class="agent-card"' in response.text
    assert 'class="agent-avatar"' in response.text
    assert 'class="agent-task"' in response.text
    assert "Run project" in response.text
    assert "renderAgentCard" in response.text
    assert "dashboard-secret" not in response.text


def test_api_dashboard_html_endpoint_accepts_query_token(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.get("/dashboard?token=dashboard-secret")

    assert response.status_code == 200
    assert "Kompany RPG Command Center" in response.text
    assert "dashboard-secret" not in response.text


def test_api_dashboard_action_requires_auth(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/dashboard/action", json={"action": "heartbeat"})

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid dashboard token"}
    assert "dashboard-secret" not in response.text


def test_api_dashboard_action_runs_bounded_action_with_session_cookie(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)
    login = client.post(
        "/dashboard/login",
        data={"dashboard_token": "dashboard-secret"},
        follow_redirects=False,
    )
    client.cookies.set(
        "kompany_dashboard_session",
        login.cookies["kompany_dashboard_session"],
    )

    response = client.post("/dashboard/action", json={"action": "heartbeat"})
    client.cookies.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["action"] == "heartbeat"
    assert payload["result"]["notifications"][0]["kind"] == "pending_approvals"
    assert "dashboard-secret" not in response.text


def test_api_dashboard_action_rejects_unsupported_action(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post(
        "/dashboard/action?token=dashboard-secret",
        json={"action": "approve-everything"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "unsupported dashboard action"}
    assert "dashboard-secret" not in response.text


def test_api_dashboard_action_approves_request(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post(
        "/dashboard/action?token=dashboard-secret",
        json={"action": "approve", "approval_id": "app-1"},
    )

    assert response.status_code == 200
    assert response.json()["action"] == "approve"
    assert response.json()["result"]["approval"]["status"] == "approved"


def test_api_dashboard_action_rejects_request(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post(
        "/dashboard/action?token=dashboard-secret",
        json={"action": "reject", "approval_id": "app-1", "reason": "nope"},
    )

    assert response.status_code == 200
    assert response.json()["action"] == "reject"
    assert response.json()["result"]["approval"]["status"] == "rejected"


def test_api_dashboard_action_suspends_and_resumes_runtime(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    suspend = client.post(
        "/dashboard/action?token=dashboard-secret",
        json={"action": "runtime-suspend", "reason": "maintenance"},
    )
    resume = client.post(
        "/dashboard/action?token=dashboard-secret",
        json={"action": "runtime-resume"},
    )

    assert suspend.status_code == 200
    assert suspend.json()["result"]["status"] == "suspended"
    assert resume.status_code == 200
    assert resume.json()["result"]["status"] == "resumed"


def test_api_dashboard_action_rejects_expired_session_cookie(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.settings.web_dashboard_token = "dashboard-secret"
    fake_engine.settings.dashboard_session_ttl_seconds = 1
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)
    monkeypatch.setattr("kompany.interfaces.api.time.time", lambda: 1_000_000)
    login = client.post(
        "/dashboard/login",
        data={"dashboard_token": "dashboard-secret"},
        follow_redirects=False,
    )
    client.cookies.set(
        "kompany_dashboard_session",
        login.cookies["kompany_dashboard_session"],
    )
    monkeypatch.setattr("kompany.interfaces.api.time.time", lambda: 1_000_002)

    response = client.post("/dashboard/action", json={"action": "heartbeat"})
    client.cookies.clear()

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid dashboard token"}
    assert "dashboard-secret" not in response.text


def test_api_status_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.get("/status")

    assert response.status_code == 200
    assert response.json()["total_ai_costs"] == 0.125
    assert response.json()["goal"] == "AI tools"
    assert response.json()["time_horizon"] == "12 months"


def test_api_resume_project_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/projects/project-1/resume")

    assert response.status_code == 200
    assert response.json()["status"] == "resumed"
    assert response.json()["latest_checkpoint"]["task_id"] == "task-1"


def test_api_decision_packet_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/decision-packet", json={"text": "Buy computer", "target_amount": 5000})

    assert response.status_code == 200
    assert response.json()["status"] == "awaiting_approval"
    assert response.json()["revenue_proposal"]["owner"] == "cro"


def test_api_override_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/override", json={"text": "Stop project"})

    assert response.status_code == 200
    assert response.json()["status"] == "awaiting_approval"
    assert response.json()["approval_id"] == "app-1"


def test_api_approval_endpoints(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    list_response = client.get("/approvals")
    approve_response = client.post("/approvals/app-1/approve")

    assert list_response.status_code == 200
    assert list_response.json()[0]["id"] == "app-1"
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"


def test_api_reject_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/approvals/app-1/reject", json={"reason": "too risky"})

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["resolution_reason"] == "too risky"


def test_api_debate_matches_mcp_shape(monkeypatch):
    fake_engine = FakeEngine()
    fake_engine.registry = FakeDebateRegistry()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)
    monkeypatch.setattr(
        "kompany.core.debate.DebateEngine",
        lambda registry, stage: type("FakeDebateEngine", (), {"run": lambda self, question, company_state: FakeDebateResult()})(),
    )

    response = client.post("/debate", json={"question": "Should we launch?"})

    assert response.status_code == 200
    assert response.json() == {
        "question": "Should we launch?",
        "rounds": [[{"agent_name": "CTO", "recommendation": "Yes"}]],
        "synthesis": {"recommended_option": "Launch"},
        "decision": {"decision": "Launch now"},
    }


def test_cli_init_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, [
        "init",
        "--name", "Acme",
        "--capital", "5",
        "--goal", "AI tools",
        "--time-horizon", "12 months",
        "--exclusions", "crypto",
        "--json",
    ])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "status": "initialized",
        "name": "Acme",
        "capital": 5.0,
        "goal": "AI tools",
        "time_horizon": "12 months",
        "exclusions": "crypto",
        "stage": "solo",
    }


def test_cli_resume_project_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["resume-project", "project-1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "resumed"
    assert payload["project_id"] == "project-1"


def test_cli_decision_packet_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, [
        "decision-packet",
        "Buy computer",
        "--target-amount",
        "5000",
        "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "awaiting_approval"
    assert payload["revenue_proposal"]["owner"] == "cro"


def test_cli_override_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["override", "Stop project", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "awaiting_approval"
    assert payload["approval_id"] == "app-1"


def test_cli_approvals_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["approvals", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)[0]["id"] == "app-1"


def test_cli_approve_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["approve", "app-1", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "approved"


def test_cli_reject_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["reject", "app-1", "--reason", "too risky", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "rejected"
    assert payload["resolution_reason"] == "too risky"


def test_sdk_execute_decision_packet(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.execute_decision_packet("app-approved")

    assert result["status"] == "awaiting_delivery_approval"
    assert result["delivery_approval_id"] == "app-delivery"
    assert len(result["reviews"]) == 4


def test_api_execute_decision_packet_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/decision-packet/execute", json={"approval_id": "app-approved"})

    assert response.status_code == 200
    assert response.json()["status"] == "awaiting_delivery_approval"
    assert response.json()["delivery_approval_id"] == "app-delivery"


def test_api_execute_decision_packet_rejects_unapproved(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/decision-packet/execute", json={"approval_id": "app-1"})

    assert response.status_code == 400


def test_sdk_runtime_status_suspend_resume(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    assert sdk.runtime_status()["state"] == "running"
    suspended = sdk.suspend(reason="quota_exhausted")
    assert suspended["state"] == "suspended"
    assert sdk.runtime_status()["state"] == "suspended"
    resumed = sdk.resume()
    assert resumed["state"] == "running"


def test_sdk_heartbeat(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.heartbeat()

    assert result["status"] == "ok"
    assert result["pending_approvals"] == 1
    assert result["notifications"][0]["kind"] == "pending_approvals"


def test_sdk_heartbeat_dispatches_notifications(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.heartbeat(dispatch=True, adapter="telegram")

    assert result["deliveries"][0]["adapter"] == "telegram"
    assert result["deliveries"][0]["status"] == "sent"


def test_sdk_dispatch_notifications(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.dispatch_notifications([
        {
            "kind": "pending_approvals",
            "severity": "action_required",
            "summary": "1 approval request waiting.",
            "payload": {"approval_ids": ["app-1"]},
        }
    ])

    assert result[0]["status"] == "dry_run"
    assert result[0]["adapter"] == "dry-run"


def test_api_runtime_endpoints(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    status = client.get("/runtime")
    suspend = client.post("/runtime/suspend", json={"reason": "quota"})
    status2 = client.get("/runtime")
    resume = client.post("/runtime/resume")

    assert status.status_code == 200
    assert status.json()["state"] == "running"
    assert suspend.json()["state"] == "suspended"
    assert status2.json()["state"] == "suspended"
    assert resume.json()["state"] == "running"


def test_api_heartbeat_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/heartbeat")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["pending_approvals"] == 1


def test_api_heartbeat_dispatch_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/heartbeat", json={"dispatch": True, "adapter": "telegram"})

    assert response.status_code == 200
    assert response.json()["deliveries"][0]["adapter"] == "telegram"


def test_api_dispatch_notifications_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/notifications/dispatch", json={
        "events": [
            {
                "kind": "pending_approvals",
                "severity": "action_required",
                "summary": "1 approval request waiting.",
                "payload": {"approval_ids": ["app-1"]},
            }
        ]
    })

    assert response.status_code == 200
    assert response.json()[0]["status"] == "dry_run"


def test_cli_runtime_commands(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    status = runner.invoke(cli_app, ["runtime", "--json"])
    suspend = runner.invoke(cli_app, ["suspend", "--reason", "quota", "--json"])
    resume = runner.invoke(cli_app, ["resume", "--json"])

    assert status.exit_code == 0
    assert json.loads(status.stdout)["state"] == "running"
    assert suspend.exit_code == 0
    assert json.loads(suspend.stdout)["state"] == "suspended"
    assert resume.exit_code == 0
    assert json.loads(resume.stdout)["state"] == "running"


def test_cli_heartbeat_json(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["heartbeat", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["notifications"][0]["kind"] == "pending_approvals"


def test_cli_heartbeat_dispatch_json(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, [
        "heartbeat",
        "--dispatch-notifications",
        "--adapter",
        "telegram",
        "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["deliveries"][0]["adapter"] == "telegram"


def test_cli_serve_once_json(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["heartbeat-loop", "--once", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["pending_approvals"] == 1


def test_cli_serve_once_dispatch_json(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, [
        "heartbeat-loop",
        "--once",
        "--dispatch-notifications",
        "--adapter",
        "telegram",
        "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["deliveries"][0]["adapter"] == "telegram"


def test_sdk_credential_methods(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    set_result = sdk.set_credential("telegram_bot_token", "secret-token")
    listed = sdk.list_credentials()
    rotated = sdk.rotate_credential_key("new-vault-key")
    deleted = sdk.delete_credential("telegram_bot_token")

    assert set_result["name"] == "telegram_bot_token"
    assert "secret-token" not in str(set_result)
    assert listed[0]["name"] == "telegram_bot_token"
    assert "secret-token" not in str(listed)
    assert rotated == {"rotated": 1, "names": ["telegram_bot_token"]}
    assert "new-vault-key" not in str(rotated)
    assert deleted == {"name": "telegram_bot_token", "deleted": True}


def test_sdk_backup_round_trip(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    created = sdk.create_backup(label="manual")
    listed = sdk.list_backups()
    restored = sdk.restore_backup(listed[0]["id"])

    assert created["label"] == "manual"
    assert listed[0]["id"] == "20260515T120000-manual"
    assert restored["auto_pre_restore_id"]


def test_api_credential_endpoints(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    set_response = client.post("/credentials", json={
        "name": "telegram_bot_token",
        "value": "secret-token",
    })
    list_response = client.get("/credentials")
    rotate_response = client.post("/credentials/rotate-key", json={
        "new_vault_key": "new-vault-key",
    })
    delete_response = client.delete("/credentials/telegram_bot_token")

    assert set_response.status_code == 200
    assert set_response.json()["name"] == "telegram_bot_token"
    assert "secret-token" not in set_response.text
    assert list_response.status_code == 200
    assert list_response.json()[0]["name"] == "telegram_bot_token"
    assert "secret-token" not in list_response.text
    assert rotate_response.status_code == 200
    assert rotate_response.json() == {"rotated": 1, "names": ["telegram_bot_token"]}
    assert "new-vault-key" not in rotate_response.text
    assert delete_response.json() == {"name": "telegram_bot_token", "deleted": True}


def test_api_backup_endpoints(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    create = client.post("/backups", json={"label": "manual"})
    listing = client.get("/backups")
    restore = client.post("/backups/20260515T120000-manual/restore")
    missing = client.post("/backups/bogus/restore")

    assert create.status_code == 200
    assert create.json()["label"] == "manual"
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == "20260515T120000-manual"
    assert restore.status_code == 200
    assert restore.json()["auto_pre_restore_id"]
    assert missing.status_code == 404


def test_cli_credential_commands_json(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    set_result = runner.invoke(cli_app, [
        "credential-set",
        "telegram_bot_token",
        "--value",
        "secret-token",
        "--json",
    ])
    list_result = runner.invoke(cli_app, ["credentials", "--json"])
    rotate_result = runner.invoke(cli_app, [
        "credential-rotate-key",
        "--new-vault-key",
        "new-vault-key",
        "--json",
    ])
    delete_result = runner.invoke(cli_app, [
        "credential-delete",
        "telegram_bot_token",
        "--json",
    ])

    assert set_result.exit_code == 0
    assert json.loads(set_result.stdout)["name"] == "telegram_bot_token"
    assert "secret-token" not in set_result.stdout
    assert list_result.exit_code == 0
    assert json.loads(list_result.stdout)[0]["name"] == "telegram_bot_token"
    assert "secret-token" not in list_result.stdout
    assert rotate_result.exit_code == 0
    assert json.loads(rotate_result.stdout) == {"rotated": 1, "names": ["telegram_bot_token"]}
    assert "new-vault-key" not in rotate_result.stdout
    assert delete_result.exit_code == 0
    assert json.loads(delete_result.stdout) == {"name": "telegram_bot_token", "deleted": True}


def test_cli_backup_commands_json(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    create = runner.invoke(cli_app, ["backup", "--label", "manual", "--json"])
    listing = runner.invoke(cli_app, ["backups", "--json"])
    restore = runner.invoke(cli_app, ["restore", "20260515T120000-manual", "--json"])

    assert create.exit_code == 0
    assert json.loads(create.stdout)["label"] == "manual"
    assert listing.exit_code == 0
    assert json.loads(listing.stdout)[0]["id"] == "20260515T120000-manual"
    assert restore.exit_code == 0
    assert json.loads(restore.stdout)["auto_pre_restore_id"]


def test_sdk_run_retrospective(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.run_retrospective("proj-1")

    assert result["status"] == "recorded"
    assert {r["agent_role"] for r in result["reflections"]} == {"coo", "researcher"}


def test_sdk_list_memories(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.list_memories("coo", category="reflection")

    assert len(result) == 1
    assert result[0]["context"] == "project:proj-1"


def test_api_retrospective_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/retrospective", json={"project_id": "proj-1"})

    assert response.status_code == 200
    assert response.json()["status"] == "recorded"


def test_api_memories_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.get("/memories/coo?category=reflection")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["agent_role"] == "coo"


def test_cli_retrospective_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["retrospective", "proj-1", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "recorded"


def test_cli_memories_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, [
        "memories", "coo", "--category", "reflection", "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["agent_role"] == "coo"


def test_sdk_remote_command(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.remote_command(
        "mobile",
        "status",
        bearer_token="mobile-secret",
        payload={"nonce": "sdk-nonce"},
    )

    assert result["status"] == "executed"
    assert result["command"] == "status"
    assert result["result"] == {"ok": True, "payload": {"nonce": "sdk-nonce"}}


def test_sdk_remote_replay_cleanup(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.cleanup_remote_replays(ttl_seconds=3600)

    assert result == {
        "deleted": 2,
        "remaining": 1,
        "ttl_seconds": 3600,
        "cutoff": "2026-05-16 09:00:00",
    }


def test_api_remote_command_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/remote/command", json={
        "source": "mobile",
        "text": "status",
        "bearer_token": "mobile-secret",
        "payload": {"request_id": "api-request-1"},
    })

    assert response.status_code == 200
    assert response.json()["status"] == "executed"
    assert response.json()["command"] == "status"
    assert response.json()["result"]["payload"] == {"request_id": "api-request-1"}


def test_api_remote_telegram_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/remote/telegram", json={
        "update_id": 123,
        "message": {"chat": {"id": 456}, "text": "/heartbeat"},
    })

    assert response.status_code == 200
    assert response.json()["source"] == "telegram"
    assert response.json()["command"] == "heartbeat"
    assert response.json()["result"]["payload"] == {"update_id": 123}


def test_api_remote_replay_cleanup_endpoint(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/remote/replays/cleanup", json={"ttl_seconds": 3600})

    assert response.status_code == 200
    assert response.json() == {
        "deleted": 2,
        "remaining": 1,
        "ttl_seconds": 3600,
        "cutoff": "2026-05-16 09:00:00",
    }


def test_cli_remote_command_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, [
        "remote-command",
        "status",
        "--bearer-token",
        "mobile-secret",
        "--nonce",
        "cli-nonce",
        "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "executed"
    assert payload["command"] == "status"
    assert payload["result"]["payload"] == {"nonce": "cli-nonce"}


def test_cli_remote_command_denies_bad_token(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, [
        "remote-command",
        "status",
        "--bearer-token",
        "wrong",
        "--json",
    ])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "denied"
    assert payload["message"] == "mobile bearer token is invalid"


def test_cli_remote_replay_cleanup_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, [
        "remote-replay-cleanup",
        "--ttl-seconds",
        "3600",
        "--json",
    ])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "deleted": 2,
        "remaining": 1,
        "ttl_seconds": 3600,
        "cutoff": "2026-05-16 09:00:00",
    }


def test_sdk_tool_authorization_methods(monkeypatch):
    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    policies = sdk.list_tool_policies(agent_role="researcher")
    policy = sdk.set_tool_policy(
        "writer",
        "publish_draft",
        True,
        reason="needs approval",
        requires_approval=True,
    )
    auth = sdk.authorize_tool("researcher", "web_search", purpose="research")
    use = sdk.use_tool(
        "researcher",
        "web_search",
        purpose="research",
        approval_id="app-tool",
    )

    assert policies[0]["agent_role"] == "researcher"
    assert policies[0]["requires_approval"] is False
    assert policy["allowed"] is True
    assert policy["requires_approval"] is True
    assert auth["status"] == "allowed"
    assert use["status"] == "allowed"
    assert use["approval_id"] == "app-tool"


    fake_engine = FakeSDKEngine()
    monkeypatch.setattr("kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: fake_engine)
    sdk = Kompany()

    result = sdk.release_delivery("app-delivery")

    assert result["status"] == "delivered"
    assert result["released_by"] == "master"


def test_api_tool_authorization_endpoints(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    listed = client.get("/tools/policies?agent_role=researcher")
    updated = client.post("/tools/policies", json={
        "agent_role": "writer",
        "tool_name": "publish_draft",
        "allowed": True,
        "requires_approval": True,
        "reason": "needs approval",
    })
    authorized = client.post("/tools/authorize", json={
        "agent_role": "researcher",
        "tool_name": "web_search",
        "purpose": "research",
    })
    used = client.post("/tools/use", json={
        "agent_role": "subagent",
        "tool_name": "external_network",
        "purpose": "fetch",
    })

    assert listed.status_code == 200
    assert listed.json()[0]["agent_role"] == "researcher"
    assert listed.json()[0]["requires_approval"] is False
    assert updated.json()["allowed"] is True
    assert updated.json()["requires_approval"] is True
    assert authorized.json()["status"] == "allowed"
    assert used.json()["status"] == "denied"


    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/delivery/release", json={"approval_id": "app-delivery"})

    assert response.status_code == 200
    assert response.json()["status"] == "delivered"


def test_api_release_delivery_rejects_unknown(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.api._engine", fake_engine)

    response = client.post("/delivery/release", json={"approval_id": "bogus"})

    assert response.status_code == 400


def test_cli_tool_authorization_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    listed = runner.invoke(cli_app, ["tool-policies", "--agent-role", "researcher", "--json"])
    updated = runner.invoke(cli_app, [
        "set-tool-policy",
        "writer",
        "publish_draft",
        "--allowed",
        "--requires-approval",
        "--reason",
        "needs approval",
        "--json",
    ])
    authorized = runner.invoke(cli_app, [
        "authorize-tool",
        "researcher",
        "web_search",
        "--purpose",
        "research",
        "--json",
    ])
    used = runner.invoke(cli_app, [
        "use-tool",
        "researcher",
        "web_search",
        "--purpose",
        "research",
        "--approval-id",
        "app-tool",
        "--json",
    ])

    assert listed.exit_code == 0
    assert json.loads(listed.stdout)[0]["agent_role"] == "researcher"
    assert json.loads(listed.stdout)[0]["requires_approval"] is False
    assert updated.exit_code == 0
    assert json.loads(updated.stdout)["allowed"] is True
    assert json.loads(updated.stdout)["requires_approval"] is True
    assert authorized.exit_code == 0
    assert json.loads(authorized.stdout)["status"] == "allowed"
    assert used.exit_code == 0
    assert json.loads(used.stdout)["status"] == "allowed"
    assert json.loads(used.stdout)["approval_id"] == "app-tool"


    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["release-delivery", "app-delivery", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "delivered"
    assert payload["released_by"] == "master"


def test_cli_execute_packet_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["execute-packet", "app-approved", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "awaiting_delivery_approval"
    assert payload["delivery_approval_id"] == "app-delivery"


def test_cli_observability_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["observability", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["company"]["name"] == "TestCo"
    assert payload["office"]["theme"] == "virtual_company_floor"


def test_cli_dashboard_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["dashboard", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["agents"]["active"] == 1


def test_cli_web_json_output():
    result = runner.invoke(cli_app, ["web", "--host", "0.0.0.0", "--port", "9000", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ready"
    assert payload["command"] == "uvicorn kompany.interfaces.api:app --host 0.0.0.0 --port 9000"
    assert payload["url"] == "http://0.0.0.0:9000/dashboard"
    assert payload["auth"] == "set WEB_DASHBOARD_TOKEN, open /dashboard, and enter it in the login form; API clients may use a Bearer token"
    assert "?token" not in payload["auth"]


def test_cli_status_json_output(monkeypatch):
    fake_engine = FakeEngine()
    monkeypatch.setattr("kompany.interfaces.cli._get_engine", lambda config=None: fake_engine)

    result = runner.invoke(cli_app, ["status", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "company": "TestCo",
        "goal": "AI tools",
        "time_horizon": "12 months",
        "exclusions": "",
        "stage": "solo",
        "balance": 42.0,
        "total_income": 50.0,
        "total_expenses": -8.0,
        "total_ai_costs": 0.125,
        "active_projects": 0,
    }
