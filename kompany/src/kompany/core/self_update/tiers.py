"""Tier classifier for self-update diffs (PRD D2).

Path-based and conservative: tier is enforced POST-session on the
actual ``git diff --name-only`` output (instruction screening can't be
trusted), plus a prompt-level note (:data:`T3_PROMPT_NOTE`) injected
into every self-update session. Any T3 hit aborts the proposal — the
brakes can't modify the brakes.
"""

from __future__ import annotations

# T3 — never modifiable through this pipeline, even as a proposal.
# Exact repo-relative file paths.
T3_FILES: tuple[str, ...] = (
    "CONSTITUTION.md",
    "kompany/src/kompany/state/ledger.py",
    "kompany/src/kompany/llm/cost_tracker.py",
    "kompany/src/kompany/llm/models.py",
    "kompany/src/kompany/state/approvals.py",
    "kompany/src/kompany/core/autonomy.py",
    "kompany/src/kompany/core/approval_effects.py",
)

# T3 — anything under these directory prefixes.
T3_PREFIXES: tuple[str, ...] = (
    "kompany/src/kompany/core/self_update/",
    ".github/workflows/",
)

# T2 — code: PR-only via this pipeline.
_T2_PREFIXES: tuple[str, ...] = (
    "kompany/src/",
    "kompany/tests/",
    "tauri/",
)

T3_PROMPT_NOTE = (
    "PROTECTED PATHS (NEVER modify, create, or delete — the proposal is "
    "aborted automatically if the diff touches any of them): "
    "CONSTITUTION.md; kompany/src/kompany/state/ledger.py; "
    "kompany/src/kompany/llm/cost_tracker.py; "
    "kompany/src/kompany/llm/models.py; "
    "kompany/src/kompany/state/approvals.py; "
    "kompany/src/kompany/core/autonomy.py; "
    "kompany/src/kompany/core/approval_effects.py; anything under "
    "kompany/src/kompany/core/self_update/ or .github/workflows/. "
    "These are the company's safety brakes — they can only be changed by "
    "the founder directly, never through a self-update session."
)


def _normalize(path: str) -> str:
    p = path.strip().replace("\\", "/").lstrip("/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _is_t3(path: str) -> bool:
    for pat in T3_FILES:
        if path == pat or path.endswith("/" + pat):
            return True
    for pref in T3_PREFIXES:
        if path.startswith(pref) or ("/" + pref) in ("/" + path):
            return True
    return False


def _is_code(path: str) -> bool:
    if any(path.startswith(pref) for pref in _T2_PREFIXES):
        return True
    name = path.rsplit("/", 1)[-1]
    if path.endswith(".sh"):
        return True
    return name.startswith("pyproject")


def _is_docs(path: str) -> bool:
    return path.startswith("docs/") or path.endswith(".md")


def classify_paths(paths: list[str]) -> tuple[str, list[str]]:
    """Classify a diff's repo-relative paths. Returns ``(tier, t3_hits)``.

    - any T3 hit → ``"t3"`` (hits listed for the health event / autopsy)
    - else any code path (``kompany/src``, ``kompany/tests``, ``tauri``,
      shell scripts, pyproject) → ``"t2"``
    - else all-docs (``docs/**``, ``*.md``) → ``"t1"``
    - anything unrecognized is treated as code (``"t2"``, conservative)
    """
    normalized = [_normalize(p) for p in paths if p and p.strip()]
    t3_hits = [p for p in normalized if _is_t3(p)]
    if t3_hits:
        return "t3", t3_hits
    if any(_is_code(p) for p in normalized):
        return "t2", []
    if normalized and all(_is_docs(p) for p in normalized):
        return "t1", []
    if not normalized:
        return "t1", []
    # Mixed / unrecognized non-docs paths: conservative — treat as code.
    return "t2", []


__all__ = ["T3_FILES", "T3_PREFIXES", "T3_PROMPT_NOTE", "classify_paths"]
