"""Tier classifier matrix (06-12-self-update-pipeline PRD D2)."""

from __future__ import annotations

import pytest

from kompany.core.self_update.tiers import (
    T3_FILES,
    T3_PROMPT_NOTE,
    classify_paths,
)


class TestT3Detection:
    @pytest.mark.parametrize("path", list(T3_FILES))
    def test_every_exact_t3_file_blocks(self, path):
        tier, hits = classify_paths([path])
        assert tier == "t3"
        assert hits == [path]

    def test_self_update_package_blocks(self):
        tier, hits = classify_paths(
            ["kompany/src/kompany/core/self_update/tiers.py"]
        )
        assert tier == "t3"
        assert hits

    def test_github_workflows_block(self):
        tier, _ = classify_paths([".github/workflows/ci.yml"])
        assert tier == "t3"

    def test_t3_wins_over_everything_else(self):
        tier, hits = classify_paths(
            ["docs/notes.md", "kompany/src/kompany/core/ticker.py", "CONSTITUTION.md"]
        )
        assert tier == "t3"
        assert hits == ["CONSTITUTION.md"]

    def test_dot_slash_and_backslash_normalized(self):
        tier, _ = classify_paths(["./CONSTITUTION.md"])
        assert tier == "t3"
        tier, _ = classify_paths([".github\\workflows\\ci.yml"])
        assert tier == "t3"

    def test_constitution_lookalike_in_docs_is_not_t3(self):
        # Only the repo-root CONSTITUTION.md is protected... but suffix
        # matching is intentionally conservative: docs/CONSTITUTION.md
        # ALSO blocks (endswith "/CONSTITUTION.md").
        tier, _ = classify_paths(["docs/CONSTITUTION.md"])
        assert tier == "t3"
        # A different filename never matches.
        tier, _ = classify_paths(["docs/MY_CONSTITUTION_NOTES.md"])
        assert tier == "t1"


class TestT2T1:
    @pytest.mark.parametrize(
        "path",
        [
            "kompany/src/kompany/core/ticker.py",
            "kompany/tests/test_ticker.py",
            "tauri/src/main.rs",
            "scripts/build.sh",
            "kompany/pyproject.toml",
        ],
    )
    def test_code_paths_are_t2(self, path):
        tier, hits = classify_paths([path])
        assert tier == "t2"
        assert hits == []

    @pytest.mark.parametrize(
        "paths",
        [
            ["docs/guide.md"],
            ["README.md"],
            ["docs/a.md", "docs/sub/b.md", "USAGE_GUIDE.md"],
        ],
    )
    def test_all_docs_are_t1(self, paths):
        assert classify_paths(paths) == ("t1", [])

    def test_mixed_docs_and_code_is_t2(self):
        tier, _ = classify_paths(["docs/guide.md", "kompany/src/kompany/x.py"])
        assert tier == "t2"

    def test_unrecognized_non_docs_is_conservative_t2(self):
        tier, _ = classify_paths(["mystery/config.toml"])
        assert tier == "t2"

    def test_empty_list_is_t1(self):
        assert classify_paths([]) == ("t1", [])
        assert classify_paths(["", "  "]) == ("t1", [])


def test_prompt_note_lists_forbidden_paths():
    assert T3_PROMPT_NOTE
    assert "CONSTITUTION.md" in T3_PROMPT_NOTE
    assert "self_update" in T3_PROMPT_NOTE
    assert ".github/workflows" in T3_PROMPT_NOTE
