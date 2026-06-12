"""Issues #13 (full-autonomy reframe) + #10 (abandon a plan).

#13 — founder_action strings name CONNECTIONS/APPROVALS, never founder
labor; an outreach task with email connected files a ``tool_action``
draft card (propose) instead of blocking.

#10 — ``engine.abandon_project`` full effect matrix + the SDK/REST/MCP
parity check (test_agent_work_summary precedent).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kompany.core import task_outcome
from kompany.core.engine import KompanyEngine
from kompany.core.task_outcome import classify_outcome, resolve_outcome
from kompany.state.approvals import ApprovalRequests
from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.models import (
    ApprovalRequest,
    LedgerCategory,
    Project,
    ProjectStatus,
    ProjectType,
    Task,
    TaskStatus,
)
from kompany.state.projects import Projects

# ---------------------------------------------------------------------------
# #13 — classify_outcome doctrine
# ---------------------------------------------------------------------------

BLOCKED_OUTPUT = (
    "I cannot truthfully confirm messages were sent because I do not "
    "have access to your email."
)


def test_blocked_send_names_connection_not_labor():
    outcome, action = classify_outcome(
        "Wed: Send outreach to 40 contacts", BLOCKED_OUTPUT
    )
    assert outcome == "blocked"
    assert "connect" in action.lower()
    assert "yourself" not in action.lower()


@pytest.mark.parametrize(
    "title",
    [
        "Send outreach emails",
        "Publish the landing page on Gumroad",
        "Book onboarding calls",
        "Define a $99 paid offer",
    ],
)
def test_no_founder_action_asks_for_labor(title):
    """Every branch of the classifier follows the three-jobs doctrine."""
    for output in (BLOCKED_OUTPUT, "Here is the finished asset."):
        _, action = classify_outcome(title, output)
        low = action.lower()
        assert "yourself" not in low
        assert "paste the" not in low
        assert "run the calls" not in low


def test_email_connected_returns_propose_email():
    outcome, action = classify_outcome(
        "Send outreach to prospects",
        "Draft ready. To: jane@example.com — Hi Jane ...",
        email_connected=True,
    )
    assert outcome == "propose_email"
    assert "approve" in action.lower()


def test_email_connected_without_recipient_stays_blocked_with_connect_framing():
    """No extractable recipient → no card can be drafted → conservative
    fallback to the normal classification."""
    outcome, action = classify_outcome(
        "Send outreach to prospects", BLOCKED_OUTPUT, email_connected=True
    )
    assert outcome == "blocked"
    assert "connect" in action.lower()


# ---------------------------------------------------------------------------
# #13 — resolve_outcome hook (propose instead of block)
# ---------------------------------------------------------------------------


class _ProposeEngine:
    def __init__(self):
        self.proposed: list[dict] = []

    def propose_action(self, tool_name, inputs, summary, **kw):
        self.proposed.append({"tool": tool_name, "inputs": inputs, **kw})
        return {"id": "ap1"}


def _task(title="Send outreach to leads"):
    return SimpleNamespace(id="t1", title=title, assigned_agent="cmo")


def _project():
    return SimpleNamespace(id="p1", triggers_directive_id=None)


def test_resolve_outcome_files_card_when_email_connected(monkeypatch):
    monkeypatch.setattr(task_outcome, "_email_send_connected", lambda e: True)
    engine = _ProposeEngine()
    output = "Draft for jane@example.com:\n\nHi Jane, ..."

    outcome, action = resolve_outcome(engine, _task(), _project(), output)

    assert outcome == "delivered"
    assert "approve" in action.lower()
    assert len(engine.proposed) == 1
    card = engine.proposed[0]
    assert card["tool"] == "email.send"
    assert card["inputs"]["to"] == "jane@example.com"
    assert card["inputs"]["body"] == output
    assert card["project_id"] == "p1"
    assert card["task_id"] == "t1"


def test_resolve_outcome_blocks_with_connect_ask_when_not_connected(monkeypatch):
    monkeypatch.setattr(task_outcome, "_email_send_connected", lambda e: False)
    engine = _ProposeEngine()

    outcome, action = resolve_outcome(engine, _task(), _project(), BLOCKED_OUTPUT)

    assert outcome == "blocked"
    assert "connect" in action.lower()
    assert engine.proposed == []


def test_resolve_outcome_falls_back_when_propose_fails(monkeypatch):
    monkeypatch.setattr(task_outcome, "_email_send_connected", lambda e: True)

    class Boom:
        def propose_action(self, *a, **k):
            raise RuntimeError("vault down")

    outcome, action = resolve_outcome(
        Boom(), _task(), _project(), "to jane@example.com — draft body"
    )
    assert outcome == "delivered"  # asset exists; never crash the run
    assert "yourself" not in action.lower()


def test_email_send_connected_against_real_registry(monkeypatch):
    """Builtin email integrations + a stocked credential vault → True."""

    class Creds:
        def get(self, key):
            return "x"  # every credential present

    engine = SimpleNamespace(credentials=Creds())
    assert task_outcome._email_send_connected(engine) is True

    class Empty:
        def get(self, key):
            return None

    engine = SimpleNamespace(credentials=Empty())
    assert task_outcome._email_send_connected(engine) is False


# ---------------------------------------------------------------------------
# #10 — abandon_project effect matrix
# ---------------------------------------------------------------------------


class _OpsEngine:
    """Minimal engine binding the REAL abandon_project logic (parity
    with the _OpsEngine pattern in test_agent_work_summary)."""

    def __init__(self, db: Database):
        self.db = db
        self.projects = Projects(db)
        self.ledger = Ledger(db)
        self.approvals = ApprovalRequests(db)
        self.audit = AuditLog(db)

    def abandon_project(self, project_id: str, reason: str = ""):
        return KompanyEngine.abandon_project(self, project_id, reason=reason)

    def unallocated_treasury(self) -> float:
        return KompanyEngine.unallocated_treasury(self)

    async def start(self):  # api lifespan hook
        pass

    async def stop(self):
        pass


def _seed(engine: _OpsEngine, pid: str = "p1") -> None:
    engine.ledger.record(200.0, "seed capital", LedgerCategory.INCOME)
    engine.projects.create(Project(
        id=pid, name="Plan", type=ProjectType.REVENUE,
        status=ProjectStatus.ACTIVE, funded_amount=100.0,
    ))
    engine.ledger.record(
        -30.0, "ai work", LedgerCategory.AI_COST, project_id=pid
    )
    for tid, status in (
        (f"{pid}-t1", TaskStatus.PENDING),
        (f"{pid}-t2", TaskStatus.ACTIVE),
        (f"{pid}-t3", TaskStatus.COMPLETED),
    ):
        engine.projects.create_task(Task(
            id=tid, project_id=pid, title=tid, status=status,
            assigned_agent="cmo",
        ))
    engine.approvals.create(ApprovalRequest(
        id=f"{pid}-a1", action_type="envelope_topup",
        summary="top up", project_id=pid,
    ))


def test_abandon_full_effect_matrix(tmp_path):
    engine = _OpsEngine(Database(tmp_path / "db"))
    _seed(engine)
    free_before = engine.unallocated_treasury()  # 170 - 70 reserved = 100

    out = engine.abandon_project("p1", reason="pivot")

    assert out["cancelled"] is True
    assert out["status"] == "cancelled"
    assert out["tasks_stopped"] == 2  # pending + active, not completed
    assert out["approvals_withdrawn"] == 1
    assert out["envelope_released"] == pytest.approx(70.0)  # 100 funded − 30 spent

    statuses = {t.id: t.status for t in engine.projects.list_tasks("p1")}
    assert statuses["p1-t1"] == TaskStatus.CANCELLED
    assert statuses["p1-t2"] == TaskStatus.CANCELLED
    assert statuses["p1-t3"] == TaskStatus.COMPLETED

    card = engine.approvals.get("p1-a1")
    assert card.status.value == "cancelled"

    # Envelope earmark is released: unallocated treasury grows by exactly
    # the unspent amount, with NO ledger row (balance untouched).
    assert engine.unallocated_treasury() == pytest.approx(free_before + 70.0)
    assert engine.ledger.get_balance() == pytest.approx(170.0)

    events = [e["event_type"] for e in engine.audit.recent(limit=10)]
    assert "project.cancelled" in events


def test_abandon_is_idempotent_no_double_release(tmp_path):
    engine = _OpsEngine(Database(tmp_path / "db"))
    _seed(engine)
    engine.abandon_project("p1")
    free_after_first = engine.unallocated_treasury()

    again = engine.abandon_project("p1", reason="again")

    assert again["cancelled"] is False
    assert again["note"] == "already terminal"
    assert again["envelope_released"] == 0.0
    assert again["tasks_stopped"] == 0
    assert engine.unallocated_treasury() == pytest.approx(free_after_first)


def test_abandon_unknown_project_raises(tmp_path):
    engine = _OpsEngine(Database(tmp_path / "db"))
    with pytest.raises(ValueError):
        engine.abandon_project("nope")


def test_abandon_works_on_draft_projects(tmp_path):
    """Drafts strip dismiss (#2 leftover) uses the same op."""
    engine = _OpsEngine(Database(tmp_path / "db"))
    engine.projects.create(Project(
        id="d1", name="Draft", type=ProjectType.REVENUE,
        status=ProjectStatus.DRAFT,
    ))
    out = engine.abandon_project("d1", reason="dismissed draft")
    assert out["cancelled"] is True
    assert out["previous_status"] == "draft"


# ---------------------------------------------------------------------------
# #10 — four-interface parity (SDK == REST == MCP)
# ---------------------------------------------------------------------------


def test_abandon_parity_sdk_rest_mcp(tmp_path, monkeypatch):
    import asyncio
    import json as _json

    from fastapi.testclient import TestClient

    import kompany.interfaces.api as api_mod
    from kompany.interfaces import mcp_proxy, mcp_server

    engine = _OpsEngine(Database(tmp_path / "db"))
    for pid in ("p_sdk", "p_rest", "p_mcp"):
        _seed(engine, pid)

    # SDK path == engine op by construction.
    sdk_out = engine.abandon_project("p_sdk", reason="r")

    monkeypatch.setattr(api_mod, "_engine", engine)
    with TestClient(api_mod.app) as client:
        rest_out = client.post(
            "/projects/p_rest/abandon", json={"reason": "r"}
        ).json()

    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)
    monkeypatch.setattr(mcp_server, "_engine", engine)
    out = asyncio.run(mcp_server.call_tool(
        "kompany_project_abandon", {"project_id": "p_mcp", "reason": "r"}
    ))
    mcp_out = _json.loads(out[0].text)

    def norm(d):
        return {k: v for k, v in d.items() if k != "id"}

    assert norm(sdk_out) == norm(rest_out) == norm(mcp_out)
    assert rest_out["cancelled"] is True
    assert rest_out["envelope_released"] == pytest.approx(70.0)
