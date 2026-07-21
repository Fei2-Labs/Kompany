"""Skills / memory-compounding layer (06-16-agentic-chat-engine P5).

Covers:
- SkillStore save/get + dedupe (re-save refines, never fans out a dup),
- L1 trigger-word index build (<=30 lines, no embeddings),
- trigger-word retrieval (matching + non-matching),
- crystallization: runs on success, respects the flag + the tool-use cap,
  dedupes against an existing skill (mocked LLM distill call — NO network),
- retrieved skills injected into the chat system prompt,
- recall/remember/save_skill tools + their SideEffect classification,
- history compression triggers past threshold + preserves the checkpoint,
- end-to-end acceptance: a repeated task crystallizes a skill that is
  retrieved on a later similar request (mocked LLM throughout).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from kompany.core.agent_tools.base import SideEffect, ToolContext
from kompany.core.agent_tools.chat_registry import build_chat_registry
from kompany.core.agent_tools.memory_tools import memory_tools
from kompany.core.agent_tools.registry import ToolRegistry
from kompany.core.engine_parts.skill_crystallize import (
    CrystallizedSkill,
    SkillCrystallizationMixin,
)
from kompany.core.harness.native_runner import NativeRunner
from kompany.core.harness.protocol import HarnessCaps
from kompany.state.database import Database
from kompany.state.memory import AgentMemory
from kompany.state.skills import INDEX_MAX_LINES, SkillStore, tokenize


# --------------------------------------------------------------------- #
# fixtures / fakes
# --------------------------------------------------------------------- #

def _make_store() -> SkillStore:
    db = Database(Path(tempfile.mkdtemp()))
    return SkillStore(db)


class _Audit:
    def __init__(self):
        self.records = []

    def record(self, event_type, action, detail=None, **kw):
        self.records.append((event_type, action, detail))


class _Settings:
    skill_crystallization_enabled = True
    skill_crystallize_min_tools = 2
    skill_retrieve_limit = 3

    def get_model_for_tier(self, tier):
        return "fake-model"


class _StructuredLLM:
    """Stub whose ``call_structured`` returns a scripted CrystallizedSkill."""

    def __init__(self, skill: CrystallizedSkill):
        self._skill = skill
        self.calls = 0

    def call_structured(self, *a, output_schema=None, **kw):
        self.calls += 1

        class _Resp:
            parsed = self._skill
            cost_usd = 0.002

        return _Resp()


class _FakeEngine(SkillCrystallizationMixin):
    """Just enough engine to drive the crystallization mixin."""

    def __init__(self, skill: CrystallizedSkill):
        db = Database(Path(tempfile.mkdtemp()))
        self.skills = SkillStore(db)
        self.memory = AgentMemory(db)
        self.settings = _Settings()
        self.audit = _Audit()
        self.llm = _StructuredLLM(skill)


# --------------------------------------------------------------------- #
# 1. SkillStore: save / get / dedupe
# --------------------------------------------------------------------- #

def test_skill_save_and_get():
    store = _make_store()
    res = store.save(
        "ceo", "competitor_research",
        trigger_words=["competitor", "research", "market"],
        when_to_use="When asked to research competitors.",
        sop="1. web_search the competitor\n2. web_fetch their site",
        code="print('hi')",
    )
    assert res["action"] == "inserted"
    got = store.get("ceo", "competitor_research")
    assert got is not None
    assert got["trigger_words"] == ["competitor", "research", "market"]
    assert "web_search" in got["sop"]
    assert got["code"] == "print('hi')"
    assert got["created_count"] == 1


def test_skill_save_dedupes_by_name():
    """Re-saving the same skill REFINES it, never fans out a duplicate row."""
    store = _make_store()
    store.save("ceo", "competitor_research", ["competitor"], "", "v1 sop")
    res = store.save("ceo", "competitor_research", ["competitor", "rival"],
                     "", "v2 sop refined")
    assert res["action"] == "updated"
    assert store.count("ceo") == 1
    got = store.get("ceo", "competitor_research")
    assert got["sop"] == "v2 sop refined"
    assert got["created_count"] == 2  # re-derived once


# --------------------------------------------------------------------- #
# 2. L1 trigger-word index
# --------------------------------------------------------------------- #

def test_index_builds_keyword_map_capped():
    store = _make_store()
    store.save("ceo", "skill_a", ["alpha", "beta"], "", "x")
    store.save("ceo", "skill_b", ["beta", "gamma"], "", "y")
    lines = store.index_lines("ceo")
    joined = "\n".join(lines)
    assert "alpha:" in joined
    # 'beta' routes to BOTH skills (shared trigger word).
    beta_line = [ln for ln in lines if ln.startswith("beta:")][0]
    assert "skill_a" in beta_line and "skill_b" in beta_line
    assert len(lines) <= INDEX_MAX_LINES


def test_index_respects_30_line_cap():
    store = _make_store()
    # 40 skills, each a unique trigger word → would be 40 lines uncapped.
    for i in range(40):
        store.save("ceo", f"skill_{i}", [f"kw{i:02d}"], "", "z")
    assert len(store.index_lines("ceo")) == INDEX_MAX_LINES


# --------------------------------------------------------------------- #
# 3. retrieval (matching + non-matching), no embeddings
# --------------------------------------------------------------------- #

def test_retrieve_matches_by_trigger_words():
    store = _make_store()
    store.save("ceo", "competitor_research",
               ["competitor", "research", "market"], "", "do research")
    store.save("ceo", "email_draft", ["email", "draft"], "", "write email")
    hits = store.retrieve("ceo", "please research our main competitor")
    assert [h["name"] for h in hits] == ["competitor_research"]


def test_retrieve_returns_empty_on_no_match():
    store = _make_store()
    store.save("ceo", "competitor_research", ["competitor"], "", "x")
    assert store.retrieve("ceo", "what's the weather today") == []
    assert store.retrieve("ceo", "") == []


def test_retrieve_text_track_use_bumps_count():
    store = _make_store()
    store.save("ceo", "competitor_research",
               ["competitor", "research"], "Use when researching rivals.",
               "1. search\n2. fetch")
    block = store.retrieve_text("ceo", "research the competitor")
    assert "SKILL: competitor_research" in block
    assert "Use when researching rivals." in block
    # track_use=True inside retrieve_text → used_count bumped.
    assert store.get("ceo", "competitor_research")["used_count"] == 1


def test_tokenize_drops_short_tokens():
    assert tokenize("Research the AI market") == ["research", "the", "market"]


# --------------------------------------------------------------------- #
# 4. crystallization: success / flag / cap / dedupe
# --------------------------------------------------------------------- #

def _skill() -> CrystallizedSkill:
    return CrystallizedSkill(
        name="competitor_research",
        trigger_words=["competitor", "research", "market"],
        when_to_use="When asked to research a competitor.",
        sop="1. web_search\n2. web_fetch the top result",
    )


def test_crystallize_saves_on_success():
    eng = _FakeEngine(_skill())
    res = eng.maybe_crystallize_skill(
        request="research our competitor Acme",
        final_text="Acme does X and Y.",
        tool_names=["web_search", "web_fetch"],
        succeeded=True,
    )
    assert res["status"] == "saved"
    assert eng.llm.calls == 1
    assert eng.skills.get("ceo", "competitor_research") is not None
    assert any(r[0] == "skill.crystallized" for r in eng.audit.records)


def test_crystallize_skipped_when_disabled():
    eng = _FakeEngine(_skill())
    eng.settings.skill_crystallization_enabled = False
    res = eng.maybe_crystallize_skill(
        request="research X", final_text="done",
        tool_names=["web_search", "web_fetch"], succeeded=True,
    )
    assert res["status"] == "disabled"
    assert eng.llm.calls == 0  # no LLM spend when off


def test_crystallize_skipped_on_failure():
    eng = _FakeEngine(_skill())
    res = eng.maybe_crystallize_skill(
        request="research X", final_text="",
        tool_names=["web_search", "web_fetch"], succeeded=False,
    )
    assert res["status"] == "skipped_failed"
    assert eng.llm.calls == 0


def test_crystallize_skipped_when_below_tool_floor():
    """A trivial one-tool chat is not worth a skill."""
    eng = _FakeEngine(_skill())
    res = eng.maybe_crystallize_skill(
        request="research X", final_text="done",
        tool_names=["web_search"], succeeded=True,  # 1 distinct < min 2
    )
    assert res["status"] == "skipped_trivial"
    assert eng.llm.calls == 0


def test_crystallize_dedupes_against_existing_skill():
    eng = _FakeEngine(_skill())
    eng.maybe_crystallize_skill(
        request="research competitor A", final_text="a",
        tool_names=["web_search", "web_fetch"], succeeded=True,
    )
    res = eng.maybe_crystallize_skill(
        request="research competitor B", final_text="b",
        tool_names=["web_search", "web_fetch"], succeeded=True,
    )
    assert res["action"] == "updated"
    assert eng.skills.count("ceo") == 1  # refined, not duplicated


# --------------------------------------------------------------------- #
# 5. recall / remember / save_skill tools + classification
# --------------------------------------------------------------------- #

def test_memory_tools_classification():
    tools = {t.name: t for t in memory_tools()}
    assert tools["recall"].side_effect == SideEffect.READ
    assert tools["remember"].side_effect == SideEffect.WRITE_LOCAL
    assert tools["save_skill"].side_effect == SideEffect.WRITE_LOCAL


def test_recall_and_remember_tools_roundtrip():
    eng = _FakeEngine(_skill())
    eng.skills.save("ceo", "competitor_research",
                    ["competitor", "research"], "", "search then fetch")
    ctx = ToolContext(engine=eng)
    tools = {t.name: t for t in memory_tools()}

    recall_out = tools["recall"].run({"query": "research the competitor"}, ctx)
    assert "competitor_research" in recall_out

    remember_out = tools["remember"].run(
        {"note": "Founder prefers concise updates."}, ctx
    )
    assert "saved note" in remember_out
    assert eng.memory.recall("ceo", query="concise")


def test_save_skill_tool_writes_store():
    eng = _FakeEngine(_skill())
    ctx = ToolContext(engine=eng)
    tools = {t.name: t for t in memory_tools()}
    out = tools["save_skill"].run(
        {"name": "weekly_report", "trigger_words": ["weekly", "report"],
         "sop": "1. gather metrics\n2. summarize"}, ctx
    )
    assert "weekly_report" in out
    assert eng.skills.get("ceo", "weekly_report") is not None


# --------------------------------------------------------------------- #
# 6. retrieval injection into the chat system prompt
# --------------------------------------------------------------------- #

class _PromptEngine:
    """Minimal engine exposing the system-prompt composition path."""

    def __init__(self):
        db = Database(Path(tempfile.mkdtemp()))
        self.skills = SkillStore(db)
        self.settings = _Settings()

    # methods the real mixin uses, stubbed:
    def _compose_answer_context(self):
        return ("Company context here.", None)

    # reuse the real mixin methods bound to this instance:
    from kompany.core.engine_parts.agentic_chat import AgenticChatMixin
    _agentic_chat_system_prompt = AgenticChatMixin._agentic_chat_system_prompt
    _retrieve_skills_block = AgenticChatMixin._retrieve_skills_block


class _CEO:
    def system_prompt(self):
        return "You are the CEO."

    def with_founder_context(self, p):
        return p + " [founder]"


def test_retrieved_skill_injected_into_system_prompt():
    eng = _PromptEngine()
    eng.skills.save("ceo", "competitor_research",
                    ["competitor", "research"], "When researching rivals.",
                    "1. web_search\n2. web_fetch")
    prompt = eng._agentic_chat_system_prompt(
        _CEO(), request="research the competitor Acme"
    )
    assert "Relevant past skills" in prompt
    assert "competitor_research" in prompt


def test_no_skill_block_when_no_match():
    eng = _PromptEngine()
    eng.skills.save("ceo", "competitor_research", ["competitor"], "", "x")
    prompt = eng._agentic_chat_system_prompt(
        _CEO(), request="what time is the standup"
    )
    assert "Relevant past skills" not in prompt


# --------------------------------------------------------------------- #
# 7. history compression
# --------------------------------------------------------------------- #

class _CompressSettings:
    agentic_history_compress_after_turns = 3


def _runner_with_threshold():
    return NativeRunner(
        model="m", llm_client=object(), settings=_CompressSettings(),
    )


def test_history_compression_triggers_past_threshold_and_keeps_checkpoint():
    runner = _runner_with_threshold()
    seed = {"role": "user", "content": "Task:\nresearch X"}
    messages = [seed]
    # Build a long synthetic transcript (5 turns > threshold 3).
    history = []
    for i in range(1, 6):
        history.append({
            "turn": i,
            "thought": f"thinking step {i}",
            "tool": {"names": [f"tool_{i}"]},
            "observation": f"result {i}",
        })
        messages.append({"role": "assistant", "content": f"a{i}"})
        messages.append({"role": "user", "content": f"obs{i}"})

    rebuilt = runner._maybe_compress_history(messages, history)

    # Compressed: far fewer messages than the raw 11.
    assert len(rebuilt) < len(messages)
    # Task seed preserved verbatim (the original ask survives compaction).
    assert rebuilt[0] is seed
    # A rolling summary of the older turns is present.
    summary = rebuilt[1]["content"]
    assert "Summary of earlier turns" in summary
    assert "tool_1" in summary  # an old turn folded into the summary
    # The latest checkpoint (most recent turns) is preserved verbatim,
    # while the OLD turns are gone from the verbatim recap (folded to summary).
    recap = "\n".join(m["content"] for m in rebuilt if isinstance(m["content"], str))
    assert "thinking step 5" in recap  # newest turn kept intact
    assert "result 5" in recap
    assert "[turn 1]" not in recap  # oldest turn no longer verbatim


def test_history_compression_noop_below_threshold():
    runner = _runner_with_threshold()
    seed = {"role": "user", "content": "Task:\nresearch X"}
    messages = [seed, {"role": "assistant", "content": "a1"}]
    history = [{"turn": 1, "thought": "t", "tool": None, "observation": "o"}]
    assert runner._maybe_compress_history(messages, history) is messages


def test_history_compression_disabled_when_zero():
    class _Zero:
        agentic_history_compress_after_turns = 0

    runner = NativeRunner(model="m", llm_client=object(), settings=_Zero())
    messages = [{"role": "user", "content": "Task:\nx"}]
    history = [{"turn": i, "thought": "t", "tool": None, "observation": "o"}
               for i in range(1, 20)]
    assert runner._maybe_compress_history(messages, history) is messages


# --------------------------------------------------------------------- #
# 8. ACCEPTANCE: repeated task crystallizes → retrieved on later request
# --------------------------------------------------------------------- #

def test_acceptance_crystallize_then_retrieve_end_to_end():
    """A successful session crystallizes a skill; a later similar request
    retrieves it (the PRD acceptance criterion), all with a mocked LLM."""
    eng = _FakeEngine(_skill())

    # 1. First task succeeds with real tool use → crystallize.
    crystallized = eng.maybe_crystallize_skill(
        request="research our competitor Acme and summarize",
        final_text="Acme sells X.",
        tool_names=["web_search", "web_fetch"],
        succeeded=True,
    )
    assert crystallized["status"] == "saved"

    # 2. A LATER, similar request retrieves the stored skill by trigger words
    #    — the compounding payoff (no new LLM call needed to recall).
    hits = eng.skills.retrieve(
        "ceo", "can you research the competitor landscape"
    )
    assert [h["name"] for h in hits] == ["competitor_research"]

    # 3. And it lands in the injected prompt block for the next session.
    block = eng.skills.retrieve_text("ceo", "research the competitor")
    assert "competitor_research" in block
    assert "web_search" in block


# --------------------------------------------------------------------- #
# 9. chat registry includes the memory tools
# --------------------------------------------------------------------- #

def test_chat_registry_includes_memory_tools():
    class _MinEngine:
        settings = None
        audit = None

    reg = build_chat_registry(_MinEngine())
    assert isinstance(reg, ToolRegistry)
    names = reg.names()
    assert "recall" in names and "remember" in names and "save_skill" in names
    # recall is inline (READ); remember/save_skill inline (WRITE_LOCAL).
    assert reg.is_inline("recall")
    assert reg.is_inline("remember")
    assert reg.is_inline("save_skill")
