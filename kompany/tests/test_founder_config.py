"""Founder profile + rules (#6/#7) — models, ops, injection, enforcement.

Covers: typed-model validation; company_config JSON roundtrip with merge
+ clear; the founder-context prompt block reaching a real BaseAgent
subclass system prompt (and respecting language / soft rules); the
hard-rule NEVER clause + deterministic post-filter at decomposition and
first-directive proposal time; the execution-time gate refusals
(exclude / per-action budget cap / forbidden paid category); and the
REST / MCP / SDK / CLI parity contract.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kompany.agents.base import BaseAgent
from kompany.config.settings import KompanySettings
from kompany.core import founder_config
from kompany.core.autonomy import AutonomyGate
from kompany.core.founder_config import FounderProfile, FounderRules
from kompany.core.runner import ProjectRunner, TaskSpec
from kompany.interfaces.api import app as api_app
from kompany.interfaces.cli import app as cli_app
from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.models import Project, ProjectType

PROFILE = {"address": "Clare", "comms_style": "terse, direct", "language": "zh"}
RULES = {
    "hard": [{"kind": "exclude_capability", "match": "phone_call", "action": "skip"}],
    "soft": "prefer async over meetings",
}


class OpsEngine:
    """Duck-typed engine carrying exactly what founder_config needs.

    Mirrors ``KompanyEngine``'s thin wrappers so the same instance can
    stand in behind REST/MCP/SDK/CLI in the parity tests below.
    """

    def __init__(self, tmp_path):
        self.db = Database(tmp_path)
        self.audit = AuditLog(self.db)
        self.settings = KompanySettings(data_dir=tmp_path)

    def get_founder_profile(self):
        return founder_config.get_founder_profile(self)

    def set_founder_profile(self, payload):
        return founder_config.set_founder_profile(self, payload)

    def get_founder_rules(self):
        return founder_config.get_founder_rules(self)

    def set_founder_rules(self, payload):
        return founder_config.set_founder_rules(self, payload)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_profile_model_rejects_unknown_fields():
    with pytest.raises(Exception):
        FounderProfile.model_validate({"address": "Clare", "salary": "1M"})


def test_rules_model_validates_hard_rule_kinds():
    rules = FounderRules.model_validate(RULES)
    assert rules.hard[0].kind == "exclude_capability"
    with pytest.raises(Exception):
        FounderRules.model_validate(
            {"hard": [{"kind": "exclude_everything", "match": "x"}]}
        )
    with pytest.raises(Exception):
        FounderRules.model_validate({"hard": [{"kind": "budget_cap", "match": ""}]})


# ---------------------------------------------------------------------------
# Engine ops — roundtrip, merge, clear, audit
# ---------------------------------------------------------------------------


def test_profile_roundtrip_merge_and_clear(tmp_path):
    eng = OpsEngine(tmp_path)
    assert eng.get_founder_profile() is None
    result = eng.set_founder_profile(PROFILE)
    assert result == {"profile": PROFILE}
    assert eng.get_founder_profile() == PROFILE
    # Mirrored to settings so agents pick it up without a reboot.
    assert eng.settings.founder_profile == PROFILE

    # Partial payload merges over the stored profile.
    eng.set_founder_profile({"language": "en"})
    stored = eng.get_founder_profile()
    assert stored["language"] == "en"
    assert stored["address"] == "Clare"

    # None clears — row deleted, settings mirror cleared.
    assert eng.set_founder_profile(None) == {"profile": None}
    assert eng.get_founder_profile() is None
    assert eng.settings.founder_profile is None


def test_profile_validation_error_surfaces_and_nothing_persists(tmp_path):
    eng = OpsEngine(tmp_path)
    with pytest.raises(ValueError, match="salary"):
        eng.set_founder_profile({"salary": "1M"})
    assert eng.get_founder_profile() is None


def test_rules_roundtrip_merge_and_clear(tmp_path):
    eng = OpsEngine(tmp_path)
    assert eng.get_founder_rules() is None
    result = eng.set_founder_rules(RULES)
    assert result["rules"]["soft"] == RULES["soft"]
    assert result["rules"]["hard"][0]["match"] == "phone_call"
    assert eng.settings.founder_rules == result["rules"]

    # Top-level merge: soft-only payload keeps the hard list.
    eng.set_founder_rules({"soft": "weekly summary on Fridays"})
    stored = eng.get_founder_rules()
    assert stored["soft"] == "weekly summary on Fridays"
    assert len(stored["hard"]) == 1

    assert eng.set_founder_rules(None) == {"rules": None}
    assert eng.get_founder_rules() is None
    assert eng.settings.founder_rules is None


def test_rules_audit_carries_counts_not_content(tmp_path):
    eng = OpsEngine(tmp_path)
    eng.set_founder_rules(RULES)
    rows = [
        e for e in eng.audit.recent(limit=10)
        if e["event_type"] == founder_config.RULES_AUDIT_EVENT
    ]
    assert len(rows) == 1
    assert json.loads(rows[0]["detail"]) == {"hard_count": 1, "has_soft": True}


# ---------------------------------------------------------------------------
# Prompt injection — profile + soft rules into a real agent system prompt
# ---------------------------------------------------------------------------


class _FakeResp:
    cost_usd = 0.0
    parsed = None


class _CapturingLLM:
    def __init__(self):
        self.last_system = None

    def call(self, *, model, system, prompt, **kwargs):
        self.last_system = system
        return _FakeResp()

    def call_structured(self, *, model, system, prompt, output_schema, **kwargs):
        self.last_system = system
        return _FakeResp()


class _ProbeAgent(BaseAgent):
    role = "probe"
    display_name = "Probe"

    def system_prompt(self) -> str:
        return "You are the probe agent."


def test_founder_context_injected_into_agent_system_prompt(tmp_path):
    settings = KompanySettings(
        data_dir=tmp_path, founder_profile=PROFILE, founder_rules=RULES
    )
    llm = _CapturingLLM()
    agent = _ProbeAgent(llm, settings)
    agent.call("hello")
    system = llm.last_system
    assert "You are the probe agent." in system
    assert "Founder context:" in system
    assert "Address the founder as: Clare" in system
    assert "terse, direct" in system
    # Language respected.
    assert "Respond to the founder in this language: zh" in system
    # Soft rules injected, best-effort.
    assert "prefer async over meetings" in system
    # Constitution invariant: phrasing only, never assessments.
    assert "never soften or alter the substance of an honest assessment" in system


def test_no_profile_means_unchanged_prompt(tmp_path):
    settings = KompanySettings(data_dir=tmp_path)
    llm = _CapturingLLM()
    _ProbeAgent(llm, settings).call_structured("hi", output_schema=None)
    assert llm.last_system == "You are the probe agent."


def test_context_block_empty_when_unset():
    assert founder_config.founder_context_block(None, None) == ""
    assert founder_config.founder_context_block({}, {"hard": []}) == ""


# ---------------------------------------------------------------------------
# Proposal-time enforcement — NEVER clause + deterministic post-filter
# ---------------------------------------------------------------------------


def test_never_propose_clause():
    assert founder_config.never_propose_clause(None) == ""
    clause = founder_config.never_propose_clause(RULES)
    assert "NEVER propose tasks requiring: phone_call" in clause


def test_filter_task_specs_matches_case_and_separator_insensitively():
    specs = [
        TaskSpec(title="Cold Phone-Call blitz", assigned_agent="cro", prompt="dial"),
        TaskSpec(title="Write landing page", assigned_agent="cmo", prompt="copy"),
        TaskSpec(title="Outreach", assigned_agent="cro", prompt="Make a PHONE CALL list"),
    ]
    kept, dropped = founder_config.filter_task_specs(specs, RULES)
    assert [s.title for s in kept] == ["Write landing page"]
    assert len(dropped) == 2


class _FakeCEO:
    """Fake CEO returning a phone-call task — must be filtered out."""

    def __init__(self):
        self.last_prompt = None

    def call_structured(self, *, prompt, output_schema, **kwargs):
        self.last_prompt = prompt

        class _Parsed:
            tasks = [
                TaskSpec(title="Phone call 20 prospects", assigned_agent="cro", prompt="call"),
                TaskSpec(title="Draft outreach email", assigned_agent="cmo", prompt="write"),
            ]

        class _Resp:
            parsed = _Parsed()

        return _Resp()


class _FakeRegistry:
    def __init__(self, ceo):
        self._ceo = ceo

    def get(self, role, company_state=None):
        return self._ceo


class _FakeRunnerEngine:
    def __init__(self, ceo, rules):
        self.registry = _FakeRegistry(ceo)
        self._rules = rules
        self.audit_events = []

    def get_company_state(self):
        return {}

    def get_founder_rules(self):
        return self._rules

    @property
    def audit(self):
        eng = self

        class _A:
            def record(self, *args, **kwargs):
                eng.audit_events.append(args[0] if args else kwargs)

        return _A()


def _revenue_project():
    return Project(
        name="Sales push",
        type=ProjectType.OPERATIONAL,
        plan={"paths": [{"name": "outbound", "description": "sell"}]},
    )


def test_decompose_prompt_contains_never_clause_and_filter_drops_phone_task():
    ceo = _FakeCEO()
    runner = ProjectRunner(_FakeRunnerEngine(ceo, RULES))
    specs = runner._decompose(_revenue_project())
    assert "NEVER propose tasks requiring: phone_call" in ceo.last_prompt
    # Deterministic post-filter backstops the LLM regardless.
    assert [s.title for s in specs] == ["Draft outreach email"]


def test_decompose_without_rules_keeps_everything():
    ceo = _FakeCEO()
    runner = ProjectRunner(_FakeRunnerEngine(ceo, None))
    specs = runner._decompose(_revenue_project())
    assert len(specs) == 2
    assert "NEVER propose" not in ceo.last_prompt


def test_decompose_week_plan_branch_is_filtered_too():
    runner = ProjectRunner(_FakeRunnerEngine(_FakeCEO(), RULES))
    project = Project(
        name="First move",
        type=ProjectType.OPERATIONAL,
        plan={
            "week_plan": ["Mon: phone call blitz", "Tue: ship landing page"],
            "proposer_role": "ceo",
        },
    )
    specs = runner._decompose(project)
    assert [s.title for s in specs] == ["Tue: ship landing page"]


def test_filter_directive_dicts_drops_matching_proposals():
    directives = [
        {"title": "Phone-call 50 leads", "rationale": "fast"},
        {"title": "Ship waitlist", "rationale": "cheap", "week_plan": ["Mon: copy"]},
        {"title": "Outreach", "rationale": "x", "week_plan": ["Wed: phone call round"]},
    ]
    kept, dropped = founder_config.filter_directive_dicts(directives, RULES)
    assert [d["title"] for d in kept] == ["Ship waitlist"]
    assert len(dropped) == 2


# ---------------------------------------------------------------------------
# Execution-time enforcement — AutonomyGate.check_rules
# ---------------------------------------------------------------------------

GATE_RULES = {
    "hard": [
        {"kind": "exclude_capability", "match": "phone", "action": "skip"},
        {"kind": "budget_cap", "match": "10", "action": "block"},
        {"kind": "forbid_paid_category", "match": "ads", "action": "block"},
    ],
    "soft": "",
}


def test_gate_refuses_excluded_tool():
    reason = AutonomyGate().check_rules(
        GATE_RULES, tool_name="phone.call", side_effect="external_action"
    )
    assert reason is not None
    assert "capability 'phone' is excluded" in reason


def test_gate_refuses_over_budget_action():
    gate = AutonomyGate()
    reason = gate.check_rules(
        GATE_RULES, tool_name="email.send", side_effect="spend",
        estimated_cost_usd=25.0,
    )
    assert "budget cap $10.00 exceeded" in reason
    assert "$25.00" in reason
    # Under the cap passes.
    assert gate.check_rules(
        GATE_RULES, tool_name="email.send", side_effect="spend",
        estimated_cost_usd=5.0,
    ) is None


def test_gate_refuses_forbidden_paid_category():
    gate = AutonomyGate()
    reason = gate.check_rules(
        GATE_RULES, tool_name="meta.ads_buy", side_effect="spend",
        estimated_cost_usd=3.0,
    )
    assert "paid category 'ads' is forbidden" in reason
    # Same category match on a FREE read tool is NOT a paid violation.
    assert gate.check_rules(
        GATE_RULES, tool_name="meta.ads_report", side_effect="read",
        estimated_cost_usd=0.0,
    ) is None


def test_gate_passes_clean_tool_and_no_rules():
    gate = AutonomyGate()
    assert gate.check_rules(GATE_RULES, tool_name="email.send") is None
    assert gate.check_rules(None, tool_name="phone.call") is None


# ---------------------------------------------------------------------------
# Parity — REST / MCP / SDK / CLI emit the same shapes
# ---------------------------------------------------------------------------

client = TestClient(api_app)
runner_cli = CliRunner()


def test_rest_profile_and_rules_roundtrip(tmp_path, monkeypatch):
    eng = OpsEngine(tmp_path)
    monkeypatch.setattr("kompany.interfaces.api._engine", eng)

    assert client.get("/founder/profile").json() is None
    res = client.put("/founder/profile", json=PROFILE)
    assert res.status_code == 200
    assert res.json() == {"profile": PROFILE}
    assert client.get("/founder/profile").json() == eng.get_founder_profile()

    res = client.put("/founder/rules", json=RULES)
    assert res.status_code == 200
    assert client.get("/founder/rules").json() == eng.get_founder_rules()

    # clear=true removes; empty body never silently clears (merge no-op).
    assert client.put("/founder/profile", json={"clear": True}).json() == {
        "profile": None
    }
    assert client.put("/founder/rules", json={"clear": True}).json() == {"rules": None}
    assert client.get("/founder/profile").json() is None

    # Validation error → 400 with the shared message.
    res = client.put("/founder/rules", json={"hard": [{"kind": "nope", "match": "x"}]})
    assert res.status_code == 400


def test_mcp_dispatch_profile_and_rules(tmp_path):
    from kompany.interfaces.mcp_dispatch import dispatcher

    eng = OpsEngine(tmp_path)
    assert dispatcher.dispatch_tool(eng, "kompany_founder_profile_show", {}) is None
    result = dispatcher.dispatch_tool(
        eng, "kompany_founder_profile_set", dict(PROFILE)
    )
    assert result == {"profile": PROFILE}
    assert (
        dispatcher.dispatch_tool(eng, "kompany_founder_profile_show", {})
        == eng.get_founder_profile()
    )
    # Bare set never silently clears.
    assert "error" in dispatcher.dispatch_tool(eng, "kompany_founder_profile_set", {})
    assert eng.get_founder_profile() is not None
    assert dispatcher.dispatch_tool(
        eng, "kompany_founder_profile_set", {"clear": True}
    ) == {"profile": None}

    result = dispatcher.dispatch_tool(eng, "kompany_founder_rules_set", dict(RULES))
    assert result["rules"]["hard"][0]["kind"] == "exclude_capability"
    assert (
        dispatcher.dispatch_tool(eng, "kompany_founder_rules_show", {})
        == eng.get_founder_rules()
    )
    bad = dispatcher.dispatch_tool(
        eng, "kompany_founder_rules_set", {"hard": [{"kind": "nope", "match": "x"}]}
    )
    assert "error" in bad
    assert dispatcher.dispatch_tool(
        eng, "kompany_founder_rules_set", {"clear": True}
    ) == {"rules": None}


def test_mcp_tools_registered():
    mcp = __import__("importlib").import_module("kompany.interfaces.mcp_server")
    names = [tool.name for tool in mcp.TOOLS]
    for tool_name in (
        "kompany_founder_profile_show",
        "kompany_founder_profile_set",
        "kompany_founder_rules_show",
        "kompany_founder_rules_set",
    ):
        assert tool_name in names


def test_sdk_parity(tmp_path, monkeypatch):
    from kompany.interfaces.sdk import Kompany

    eng = OpsEngine(tmp_path)
    monkeypatch.setattr(
        "kompany.interfaces.sdk.KompanyEngine", lambda config_path=None: eng
    )
    k = Kompany()
    assert k.founder_profile() is None
    assert k.set_founder_profile(PROFILE) == {"profile": PROFILE}
    assert k.founder_profile() == eng.get_founder_profile()
    assert k.set_founder_rules(RULES)["rules"] == eng.get_founder_rules()
    assert k.founder_rules() == eng.get_founder_rules()
    assert k.set_founder_profile(None) == {"profile": None}
    assert k.set_founder_rules(None) == {"rules": None}


def test_cli_founder_profile_and_rules(tmp_path, monkeypatch):
    eng = OpsEngine(tmp_path)
    monkeypatch.setattr(
        "kompany.interfaces.cli_founder._get_engine", lambda config=None: eng
    )

    result = runner_cli.invoke(
        cli_app, ["founder", "profile", "set", "--json", json.dumps(PROFILE)]
    )
    assert result.exit_code == 0
    result = runner_cli.invoke(cli_app, ["founder", "profile", "show", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == PROFILE

    result = runner_cli.invoke(
        cli_app, ["founder", "rules", "set", "--json", json.dumps(RULES)]
    )
    assert result.exit_code == 0
    result = runner_cli.invoke(cli_app, ["founder", "rules", "show", "--json"])
    assert json.loads(result.output)["hard"][0]["match"] == "phone_call"

    # Invalid JSON / invalid rule → exit 1; nothing silently cleared.
    result = runner_cli.invoke(cli_app, ["founder", "rules", "set", "--json", "{bad"])
    assert result.exit_code == 1
    result = runner_cli.invoke(cli_app, ["founder", "profile", "set"])
    assert result.exit_code == 1
    assert eng.get_founder_rules() is not None

    result = runner_cli.invoke(cli_app, ["founder", "rules", "set", "--clear"])
    assert result.exit_code == 0
    assert eng.get_founder_rules() is None


# ---------------------------------------------------------------------------
# Real engine — boot mirror + thin wrappers
# ---------------------------------------------------------------------------


def test_real_engine_roundtrip_and_boot_mirror(tmp_path, monkeypatch):
    from kompany.core.engine import KompanyEngine

    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    engine = KompanyEngine()
    engine.set_founder_profile(PROFILE)
    engine.set_founder_rules(RULES)
    assert engine.settings.founder_profile == PROFILE

    # A fresh boot mirrors the company_config rows into settings so
    # agents carry the founder context from the first prompt.
    reborn = KompanyEngine()
    assert reborn.get_founder_profile() == PROFILE
    assert reborn.settings.founder_profile == PROFILE
    assert reborn.settings.founder_rules == reborn.get_founder_rules()


def test_real_pipeline_refuses_excluded_and_forbidden_paid_tools(monkeypatch):
    """End-to-end: execute_tool + propose_action consult the hard rules."""
    from pydantic import BaseModel as _BM

    from kompany.core.engine import KompanyEngine
    from kompany.plugins.contract import (
        AutonomyTier,
        CostEstimate,
        Integration,
        SideEffect,
        Tool,
    )

    class _In(_BM):
        text: str = "x"

    class _PhoneTool(Tool):
        name = "phone.call"
        description = "place a phone call"
        input_schema = _In
        side_effect = SideEffect.READ
        autonomy_tier = AutonomyTier.AUTO

        def estimate_cost(self, inputs):
            return CostEstimate()

        def execute(self, inputs, ctx):  # pragma: no cover — must not run
            raise AssertionError("excluded tool executed")

    class _AdsTool(Tool):
        name = "meta.ads_buy"
        description = "buy ads"
        input_schema = _In
        side_effect = SideEffect.SPEND
        autonomy_tier = AutonomyTier.APPROVAL

        def estimate_cost(self, inputs):
            return CostEstimate(external_usd=3.0)

        def execute(self, inputs, ctx):  # pragma: no cover
            raise AssertionError("forbidden paid tool executed")

    class _Integ(Integration):
        integration_id = "test_founder_rules"
        display_name = "Test"
        required_credentials = ()

        def tools(self):
            return [_PhoneTool(), _AdsTool()]

    monkeypatch.setattr(
        "kompany.plugins.loader.discover",
        lambda: {"integration": [_Integ()]},
    )
    engine = KompanyEngine()
    engine.set_founder_rules(GATE_RULES)

    out = engine.execute_tool("phone.call", {"text": "hi"})
    assert out["ok"] is False
    assert out.get("refused") is True
    assert "excluded" in out["detail"]

    with pytest.raises(ValueError, match="forbidden"):
        engine.propose_action("meta.ads_buy", {"text": "go"}, "buy ads")
    # The refusal never created an approval card.
    assert not [
        a for a in engine.approvals.list_pending()
        if a.action_type == "tool_action"
    ]
