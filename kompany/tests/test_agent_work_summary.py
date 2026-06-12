"""agent_work_summary op (06-12-panel-truthfulness #22).

Shape test for the engine op (real SQL against a real Database) plus
the four-interface parity check (SDK == REST == MCP; CLI renders the
same engine call) — mirrors ``test_self_update_effects`` parity pattern.
"""

from __future__ import annotations

from kompany.core.engine import KompanyEngine
from kompany.state.database import Database


def _seed_tasks(db: Database) -> None:
    db.execute(
        "INSERT INTO projects (id, name, type, status) "
        "VALUES ('p1', 'Sprint 0', 'product', 'active')"
    )
    rows = [
        ("t1", "cto", "delivered"),
        ("t2", "cto", "completed"),
        ("t3", "CTO", "delivered"),  # mixed case folds into one role
        ("t4", "cmo", "failed"),
        ("t5", "cmo", "pending"),
    ]
    for tid, agent, status in rows:
        db.execute(
            "INSERT INTO tasks (id, project_id, title, status, assigned_agent) "
            "VALUES (?, 'p1', ?, ?, ?)",
            (tid, f"task {tid}", status, agent),
        )
    db.commit()


class _OpsEngine:
    """Minimal engine carrying only what the op + transports need."""

    def __init__(self, db: Database):
        self.db = db

    # Bind the REAL engine logic so all surfaces exercise the same SQL.
    def agent_work_summary(self):
        return KompanyEngine.agent_work_summary(self)

    async def start(self):  # api lifespan hook
        pass

    async def stop(self):
        pass


def test_agent_work_summary_shape(tmp_path):
    db = Database(tmp_path / "db")
    _seed_tasks(db)
    engine = _OpsEngine(db)

    out = engine.agent_work_summary()

    assert set(out) == {"cto", "cmo"}
    cto = out["cto"]
    assert cto["delivered"] == 2
    assert cto["completed"] == 1
    assert cto["failed"] == 0
    assert cto["total"] == 3
    assert cto["last_active"]  # MAX(updated_at) — schema default fills it
    cmo = out["cmo"]
    assert cmo == {
        "delivered": 0,
        "completed": 0,
        "failed": 1,
        "total": 2,
        "last_active": cmo["last_active"],
    }


def test_agent_work_summary_empty_db(tmp_path):
    db = Database(tmp_path / "db")
    assert _OpsEngine(db).agent_work_summary() == {}


def test_agent_work_summary_parity_sdk_rest_mcp(tmp_path, monkeypatch):
    """SDK / REST / MCP return the SAME dict (engine op is the single
    logic home; CLI renders the same engine call)."""
    import asyncio
    import json as _json

    from fastapi.testclient import TestClient

    import kompany.interfaces.api as api_mod
    from kompany.interfaces import mcp_proxy, mcp_server

    db = Database(tmp_path / "db")
    _seed_tasks(db)
    engine = _OpsEngine(db)

    # SDK path == engine op by construction; assert via engine directly.
    sdk_out = engine.agent_work_summary()

    monkeypatch.setattr(api_mod, "_engine", engine)
    with TestClient(api_mod.app) as client:
        rest_out = client.get("/agents/work-summary").json()

    monkeypatch.setattr(mcp_proxy, "discover_sidecar", lambda data_dir=None: None)
    monkeypatch.setattr(mcp_server, "_engine", engine)
    out = asyncio.run(mcp_server.call_tool("kompany_agent_work_summary", {}))
    mcp_out = _json.loads(out[0].text)

    assert sdk_out == rest_out == mcp_out
    assert rest_out["cto"]["delivered"] == 2
