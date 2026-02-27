"""Tests for agent memory system."""

import tempfile
from pathlib import Path

from kompany.state.database import Database
from kompany.state.memory import AgentMemory


def _make_memory():
    tmp = tempfile.mkdtemp()
    db = Database(Path(tmp))
    return AgentMemory(db)


def test_remember_and_recall():
    mem = _make_memory()
    mem.remember("ceo", "The founder prefers conservative estimates")
    results = mem.recall("ceo")
    assert len(results) == 1
    assert results[0]["content"] == "The founder prefers conservative estimates"


def test_recall_empty():
    mem = _make_memory()
    assert mem.recall("ceo") == []


def test_recall_with_category():
    mem = _make_memory()
    mem.remember("cfo", "Budget was tight last quarter", category="financial")
    mem.remember("cfo", "Revenue grew 10%", category="observation")
    financial = mem.recall("cfo", category="financial")
    assert len(financial) == 1
    assert financial[0]["category"] == "financial"


def test_recall_respects_limit():
    mem = _make_memory()
    for i in range(10):
        mem.remember("cto", f"Learning {i}")
    results = mem.recall("cto", limit=3)
    assert len(results) == 3


def test_recall_text_formatting():
    mem = _make_memory()
    mem.remember("cmo", "Social media converts best", category="insight")
    text = mem.recall_text("cmo")
    assert "Prior learnings:" in text
    assert "[insight] Social media converts best" in text


def test_recall_text_empty():
    mem = _make_memory()
    assert mem.recall_text("cmo") == ""


def test_count():
    mem = _make_memory()
    assert mem.count("ceo") == 0
    mem.remember("ceo", "First learning")
    mem.remember("ceo", "Second learning")
    assert mem.count("ceo") == 2


def test_memories_isolated_per_agent():
    mem = _make_memory()
    mem.remember("ceo", "CEO memory")
    mem.remember("cfo", "CFO memory")
    assert mem.count("ceo") == 1
    assert mem.count("cfo") == 1
    assert mem.recall("ceo")[0]["content"] == "CEO memory"
