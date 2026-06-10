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


def test_remember_returns_id_and_stores_knowledge_type():
    mem = _make_memory()
    mid = mem.remember(
        "cfo", "Q4 revenue dropped 8%",
        category="financial",
        knowledge_type="factual",
    )
    assert isinstance(mid, int) and mid > 0
    rows = mem.recall("cfo", knowledge_type="factual")
    assert len(rows) == 1
    assert rows[0]["knowledge_type"] == "factual"


def test_recall_filters_stale_by_default_and_can_include():
    mem = _make_memory()
    fresh_id = mem.remember("cto", "Use Postgres for analytics")
    stale_id = mem.remember(
        "cto", "Try GraphQL everywhere",
        valid_until="2000-01-01T00:00:00",
    )

    fresh = mem.recall("cto")
    all_mem = mem.recall("cto", include_stale=True)

    assert {m["id"] for m in fresh} == {fresh_id}
    assert {m["id"] for m in all_mem} == {fresh_id, stale_id}


def test_mark_stale_excludes_memory_from_default_recall():
    mem = _make_memory()
    mid = mem.remember("cmo", "Newsletter open rate is 32%")
    assert len(mem.recall("cmo")) == 1
    mem.mark_stale(mid)
    assert mem.recall("cmo") == []
    assert len(mem.recall("cmo", include_stale=True)) == 1


def test_recall_text_excludes_stale():
    mem = _make_memory()
    mem.remember("cmo", "Live insight")
    mem.remember(
        "cmo", "Outdated insight",
        valid_until="2000-01-01T00:00:00",
    )
    text = mem.recall_text("cmo")
    assert "Live insight" in text
    assert "Outdated insight" not in text


def test_memories_isolated_per_agent():
    mem = _make_memory()
    mem.remember("ceo", "CEO memory")
    mem.remember("cfo", "CFO memory")
    assert mem.count("ceo") == 1
    assert mem.count("cfo") == 1
    assert mem.recall("ceo")[0]["content"] == "CEO memory"


def test_recall_without_query_preserves_recency_order():
    mem = _make_memory()
    for i in range(3):
        mem.remember("ceo", f"Learning {i}")
    results = mem.recall("ceo")
    assert [r["content"] for r in results] == [
        "Learning 2", "Learning 1", "Learning 0",
    ]


def test_recall_returns_score():
    mem = _make_memory()
    mem.remember("ceo", "Fresh memory")
    result = mem.recall("ceo")[0]
    # Fresh row: recency bonus only (no query, no accesses, no confidence).
    assert result["score"] == 2.0


def test_recall_query_ranks_keyword_match_above_recent():
    mem = _make_memory()
    mem.remember("cmo", "Gumroad tags drive Discover traffic")
    mem.remember("cmo", "Slack emoji poll results")
    mem.remember("cmo", "Lunch order preferences")
    results = mem.recall("cmo", query="improve gumroad discover ranking")
    assert results[0]["content"] == "Gumroad tags drive Discover traffic"
    assert results[0]["score"] > results[1]["score"]


def test_recall_track_access_bumps_count():
    mem = _make_memory()
    mid = mem.remember("cto", "Deploy needs migration step")
    mem.recall("cto", track_access=True)
    mem.recall("cto", track_access=True)
    row = mem.db.execute(
        "SELECT access_count, last_accessed_at FROM agent_memories WHERE id = ?",
        (mid,),
    ).fetchone()
    assert row["access_count"] == 2
    assert row["last_accessed_at"] is not None


def test_recall_without_track_access_leaves_stats_untouched():
    mem = _make_memory()
    mid = mem.remember("cto", "Browse me")
    mem.recall("cto")
    row = mem.db.execute(
        "SELECT access_count FROM agent_memories WHERE id = ?", (mid,)
    ).fetchone()
    assert row["access_count"] == 0


def test_recall_frequently_accessed_outranks_newer():
    mem = _make_memory()
    old_id = mem.remember("ceo", "Battle-tested pattern")
    # Simulate heavy historical usage.
    mem.db.execute(
        "UPDATE agent_memories SET access_count = 20 WHERE id = ?", (old_id,)
    )
    mem.db.commit()
    mem.remember("ceo", "Brand new note")
    results = mem.recall("ceo")
    assert results[0]["content"] == "Battle-tested pattern"


def test_recall_confidence_boosts_distilled_patterns():
    mem = _make_memory()
    mem.remember("ceo", "Plain observation")
    mem.upsert_by_pattern_key(
        "ceo", "pricing-anchor", "Anchor pricing against the dearest tier",
        metadata={"confidence": 0.9},
    )
    results = mem.recall("ceo")
    assert results[0]["content"] == "Anchor pricing against the dearest tier"


def test_recall_text_passes_query():
    mem = _make_memory()
    mem.remember("cmo", "Gumroad tags drive Discover traffic")
    mem.remember("cmo", "Lunch order preferences")
    text = mem.recall_text("cmo", limit=1, query="gumroad discover")
    assert "Gumroad" in text
    assert "Lunch" not in text
