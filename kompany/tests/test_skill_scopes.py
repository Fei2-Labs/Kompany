"""08-29 self-evolution R3: skill scopes + selective injection per workflow step."""

from __future__ import annotations

import pytest

from kompany.core.engine import KompanyEngine
from kompany.core.step_executor import ExecutorContext, default_step_executor, skills_spec
from kompany.core.workflow_runner import WorkflowRunner, WorkflowYAMLInvalid
from kompany.state.database import Database
from kompany.state.skills import SCOPES, SkillStore


def _store(tmp_path) -> SkillStore:
    return SkillStore(Database(tmp_path / "db"))


def test_scope_default_agent_and_migration_of_existing_rows(tmp_path):
    db = Database(tmp_path / "db")
    cols = {r[1] for r in db.execute("PRAGMA table_info(agent_skills)").fetchall()}
    assert "scope" in cols
    s = SkillStore(db)
    s.save("cmo", "launch-post", ["launch", "post"], "when launching", "1. write 2. post")
    assert s.get("cmo", "launch-post")["scope"] == "agent"
    with pytest.raises(ValueError):
        s.save("cmo", "x", [], "", "sop", scope="galaxy")


def test_company_scope_is_visible_to_other_roles_agent_scope_is_not(tmp_path):
    s = _store(tmp_path)
    s.save("cmo", "private-trick", ["pricing"], "", "keep it", scope="agent")
    s.save("cmo", "shared-playbook", ["pricing", "launch"], "", "share it", scope="company")
    names = lambda role, **kw: {r["name"] for r in s.list(role, **kw)}
    assert names("cmo") == {"private-trick", "shared-playbook"}
    assert names("cfo") == {"shared-playbook"}
    assert names("cfo", scopes=["agent"]) == set()
    assert names("cmo", scopes=["agent"]) == {"private-trick"}
    hits = s.retrieve("cfo", "pricing question")
    assert [h["name"] for h in hits] == ["shared-playbook"]
    text = s.retrieve_text("cfo", "pricing", scopes=["company"])
    assert "shared-playbook (shared by cmo)" in text and "private-trick" not in text
    assert "cmo" not in s.retrieve_text("cmo", "pricing")  # own skills carry no origin tag
    # narrowing back hides it again; unknown skill → None
    assert s.set_scope("cmo", "shared-playbook", "agent")["scope"] == "agent"
    assert names("cfo") == set()
    assert s.set_scope("cmo", "nope", "company") is None
    assert {r["name"] for r in s.list_all(scopes=["agent"])} == {"private-trick", "shared-playbook"}
    assert SCOPES == ("builtin", "company", "agent")


def test_index_lines_include_shared_skills(tmp_path):
    s = _store(tmp_path)
    s.save("cmo", "shared", ["alpha"], "", "x", scope="company")
    assert any(line.startswith("alpha:") for line in s.index_lines("cfo"))
    assert s.index_lines("cfo", scopes=["agent"]) == []


# ---------------------------------------------------------------------------
# step-level selective injection
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, text): self.text = text; self.cost_usd = 0.0


class _Agent:
    def __init__(self): self.prompts = []
    def call(self, prompt, **kw): self.prompts.append(prompt); return _Resp("ok")


class _Registry:
    def __init__(self): self.agent = _Agent()
    def get(self, role, company_state=None): return self.agent


def _wf(step_extra: dict):
    return WorkflowRunner({"workflow_id": "t", "display_name": "t", "steps": [{"id": "s1", "agent_role": "cfo", "prompt_template": "Plan {topic}", **step_extra}]})


def test_skills_spec_normalisation_and_validation():
    assert skills_spec({}) is None and skills_spec({"skills": False}) is None
    assert skills_spec({"skills": True}) == {"scopes": None, "limit": 3, "query": None}
    assert skills_spec({"skills": {"scopes": ["company"], "limit": 1, "query": "{topic}"}}) == {"scopes": ["company"], "limit": 1, "query": "{topic}"}
    _wf({"skills": {"scopes": ["company", "agent"], "limit": 2}})
    for bad in ({"skills": "yes"}, {"skills": {"scopes": ["galaxy"]}}, {"skills": {"limit": 0}}, {"skills": {"foo": 1}}):
        with pytest.raises(WorkflowYAMLInvalid):
            _wf(bad)


def test_step_injects_only_declared_scopes(tmp_path):
    s = _store(tmp_path)
    s.save("cmo", "shared-pricing", ["pricing"], "", "SHARED SOP", scope="company")
    s.save("cfo", "own-pricing", ["pricing"], "", "OWN SOP", scope="agent")
    reg = _Registry()
    # no skills key → prompt untouched
    runner = _wf({})
    ctx = ExecutorContext(registry=reg, runner=runner, initial_inputs={"topic": "pricing"}, skills=s)
    default_step_executor(runner.steps[0], {}, ctx)
    assert reg.agent.prompts[-1] == "Plan pricing"
    # company scope only → shared SOP in, own agent-scope SOP out
    runner = _wf({"skills": {"scopes": ["company"]}})
    ctx = ExecutorContext(registry=reg, runner=runner, initial_inputs={"topic": "pricing"}, skills=s)
    default_step_executor(runner.steps[0], {}, ctx)
    p = reg.agent.prompts[-1]
    assert "SHARED SOP" in p and "OWN SOP" not in p and p.endswith("Plan pricing")
    # true → everything visible to the role
    runner = _wf({"skills": True})
    ctx = ExecutorContext(registry=reg, runner=runner, initial_inputs={"topic": "pricing"}, skills=s)
    default_step_executor(runner.steps[0], {}, ctx)
    assert "OWN SOP" in reg.agent.prompts[-1] and "SHARED SOP" in reg.agent.prompts[-1]
    # no store → silently no injection
    runner = _wf({"skills": True})
    ctx = ExecutorContext(registry=reg, runner=runner, initial_inputs={"topic": "pricing"}, skills=None)
    default_step_executor(runner.steps[0], {}, ctx)
    assert reg.agent.prompts[-1] == "Plan pricing"
    # usage tracked on injected skills
    assert s.get("cmo", "shared-pricing")["used_count"] == 2


def test_reference_workflows_do_not_inject_by_default():
    from kompany.core import workflows_registry as reg
    for wid in ("idea-validation", "weekly-exec-review", "landing-page-launch"):
        assert all(skills_spec(step) is None for step in reg.get(wid).steps)


# ---------------------------------------------------------------------------
# four surfaces
# ---------------------------------------------------------------------------

def test_surfaces_parity(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("KOMPANY_INSTALLATION_ROLE_FILE", str(tmp_path / "norole"))
    e = KompanyEngine()
    e.skills.save("cmo", "launch-post", ["launch"], "", "sop", code="print(1)")
    from fastapi.testclient import TestClient
    from kompany.interfaces import api
    from kompany.interfaces.mcp_dispatch import dispatch_tool
    from kompany.interfaces.mcp_server import TOOLS
    from kompany.interfaces.sdk import Kompany
    monkeypatch.setattr(api, "_engine", e)
    c = TestClient(api.app)
    rows = c.get("/skills").json()
    assert rows[0]["name"] == "launch-post" and rows[0]["scope"] == "agent" and rows[0]["has_code"] and "code" not in rows[0]
    assert c.get("/skills", params={"scopes": "galaxy"}).status_code == 422
    assert c.post("/skills/cmo/launch-post/scope", json={"scope": "company"}).json()["scope"] == "company"
    assert c.post("/skills/cmo/nope/scope", json={"scope": "company"}).status_code == 404
    assert c.get("/skills", params={"agent_role": "cfo"}).json()[0]["name"] == "launch-post"
    assert dispatch_tool(e, "kompany_skills_list", {"agent_role": "cfo"}) == e.skills_list("cfo")
    assert dispatch_tool(e, "kompany_skill_set_scope", {"agent_role": "cmo", "name": "launch-post", "scope": "agent"})["scope"] == "agent"
    assert {t.name for t in TOOLS} >= {"kompany_skills_list", "kompany_skill_set_scope"}
    sdk = Kompany.__new__(Kompany); sdk._engine = e
    assert sdk.skills_list() == e.skills_list()
    assert any(ev["event_type"] == "skill.scope_changed" for ev in e.audit.recent(limit=20)) if hasattr(e.audit, "recent") else True
    from typer.testing import CliRunner
    from kompany.interfaces.cli import app
    res = CliRunner().invoke(app, ["skills", "list", "--json"])
    assert res.exit_code == 0 and "launch-post" in res.output
    res = CliRunner().invoke(app, ["skills", "scope", "cmo", "launch-post", "company"])
    assert res.exit_code == 0 and "company" in res.output
