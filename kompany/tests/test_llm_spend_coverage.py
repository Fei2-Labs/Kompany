"""Coverage discipline test for the STREAM layer.

Every LLM-spending code path must funnel through
:class:`kompany.llm.cost_tracker.CostTracker.record` (which is the sole
emitter of the ``llm.spend`` SSE event). Two complementary checks here:

1. **Static**: scan the kompany source tree for direct LLM call patterns
   and assert that every match either lives inside ``llm/`` (the
   client implementation itself) or is one of the small allow-listed
   helper / test files.
2. **Behavioural**: run a minimal end-to-end through ``LLMClient`` with
   a fake provider and assert exactly one ``llm.spend`` envelope is
   published per successful response.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kompany.llm.client import LLMClient, LLMResponse
from kompany.llm.cost_tracker import CostTracker
from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.models import LedgerCategory


# ---------------------------------------------------------------------
# Static coverage scan
# ---------------------------------------------------------------------


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "kompany"

# Files that legitimately reference the provider SDKs / raw client calls.
# Anything outside this list must use ``LLMClient``.
_ALLOWED_PROVIDER_FILES = {
    # The provider abstraction itself.
    "llm/client.py",
    "llm/providers.py",
    "llm/models.py",
    "llm/cost_tracker.py",
    "llm/cost_ledger.py",
    "llm/cost_preview.py",
}

_FORBIDDEN_PATTERNS = (
    re.compile(r"\banthropic\.Anthropic\("),
    re.compile(r"\bopenai\.OpenAI\("),
    re.compile(r"\bclient\.messages\.create\("),
    re.compile(r"\bclient\.chat\.completions\.create\("),
)


def _iter_python_files() -> list[Path]:
    return [p for p in _SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_direct_provider_sdk_use_outside_llm_layer():
    """Catch ad-hoc LLM calls that would bypass CostTracker."""
    offenders: list[tuple[str, str]] = []
    for path in _iter_python_files():
        rel = str(path.relative_to(_SRC_ROOT))
        if rel in _ALLOWED_PROVIDER_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in _FORBIDDEN_PATTERNS:
            if pattern.search(text):
                offenders.append((rel, pattern.pattern))
    assert not offenders, (
        "Files outside llm/ are calling provider SDKs directly. "
        "Every LLM call must go through LLMClient so CostTracker can "
        "record the ledger row and emit llm.spend SSE: " + str(offenders)
    )


# ---------------------------------------------------------------------
# Behavioural coverage
# ---------------------------------------------------------------------


class _FakeHub:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, dict(payload)))


class _FakeSettings:
    anthropic_api_key = "test"
    openai_api_key = "test"
    custom_base_url = ""

    def get_api_key_for_provider(self, _name: str) -> str:
        return "test"


def _ok_resp() -> LLMResponse:
    return LLMResponse(
        text="ok",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0,
        model="claude-sonnet-4-20250514",
    )


@pytest.fixture
def world(tmp_path):
    db = Database(tmp_path)
    ledger = Ledger(db)
    ledger.record(
        amount=10.0,
        description="seed",
        category=LedgerCategory.INCOME,
    )
    hub = _FakeHub()
    tracker = CostTracker(ledger=ledger, event_hub=hub)
    client = LLMClient(settings=_FakeSettings(), cost_tracker=tracker)
    client._call_anthropic = lambda *_a, **_kw: _ok_resp()  # type: ignore[assignment]
    return {"client": client, "hub": hub, "ledger": ledger}


def test_single_call_emits_one_llm_spend(world):
    world["client"].call(
        model="claude-sonnet-4-20250514",
        system="s",
        prompt="p",
        agent_name="ceo",
        action_type="target_feasibility",
    )
    spends = [p for t, p in world["hub"].events if t == "llm.spend"]
    assert len(spends) == 1
    assert spends[0]["action_type"] == "target_feasibility"


def test_three_calls_emit_three_llm_spend(world):
    for label in ("debate_round_1", "debate_synthesis", "debate_decision"):
        world["client"].call(
            model="claude-sonnet-4-20250514",
            system="s",
            prompt=f"{label}-prompt",
            agent_name="ceo",
            action_type=label,
        )
    spends = [p for t, p in world["hub"].events if t == "llm.spend"]
    assert [s["action_type"] for s in spends] == [
        "debate_round_1",
        "debate_synthesis",
        "debate_decision",
    ]


def test_call_records_cost_recorded_flag(world):
    resp = world["client"].call(
        model="claude-sonnet-4-20250514",
        system="s",
        prompt="p",
        agent_name="ceo",
    )
    assert resp._cost_recorded is True
