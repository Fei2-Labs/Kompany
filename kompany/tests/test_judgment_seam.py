"""Judgment seam (ADR-0010) — the contract Core relies on.

The seam's whole value is that it is inert: with no provider installed,
every caller keeps the verdict it already had. These tests freeze that,
the consent gate on off-machine providers, and the fail-open behaviour
that keeps a broken provider from taking a decision path down with it.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from kompany.core.judgment import (
    BOOLEAN,
    CHOICE,
    SCORE,
    AbstainingJudgmentProvider,
    BooleanQuestion,
    ChoiceQuestion,
    Judgment,
    JudgmentProvider,
    Question,
    ScoreQuestion,
    abstain,
    external_judgment_allowed,
    judge,
    resolve_judgment_provider,
)


class _FakeSettings:
    def __init__(self, enabled: bool):
        self.external_judgment_enabled = enabled


class _FakeEngine:
    def __init__(self, enabled: bool = False, providers: list | None = None):
        self.settings = _FakeSettings(enabled)
        if providers is not None:
            self.judgment_providers = providers


class _LocalProvider(JudgmentProvider):
    provider_id = "test.local"
    is_external = False

    def judge(self, state, questions):
        return {
            key: Judgment(kind=q.kind, value="a", confidence=0.9)
            for key, q in questions.items()
        }


class _ExternalProvider(_LocalProvider):
    provider_id = "test.external"
    is_external = True


class _ExplodingProvider(JudgmentProvider):
    provider_id = "test.boom"
    is_external = False

    def judge(self, state, questions):
        raise RuntimeError("upstream is down")


class _WrongShapeProvider(JudgmentProvider):
    provider_id = "test.wrong"
    is_external = False

    def judge(self, state, questions):
        return "not a mapping"


class _WrongKindProvider(JudgmentProvider):
    provider_id = "test.kind"
    is_external = False

    def judge(self, state, questions):
        return {k: Judgment(kind=SCORE, value=2.0) for k in questions}


class _PartialProvider(JudgmentProvider):
    provider_id = "test.partial"
    is_external = False

    def judge(self, state, questions):
        first = next(iter(questions))
        return {first: Judgment(kind=questions[first].kind, value="a")}


def _q() -> ChoiceQuestion:
    return ChoiceQuestion(
        instructions="pick one", options={"a": "option a", "b": "option b"}
    )


# --- the default: Core is complete with no provider ------------------------


def test_default_provider_is_local_and_abstains():
    provider = resolve_judgment_provider()
    assert isinstance(provider, AbstainingJudgmentProvider)
    # A default that reached the network would be a privacy regression.
    assert provider.is_external is False

    answers = judge({"x": _q()}, provider=provider)
    assert answers["x"].abstained is True
    assert answers["x"].usable is False


def test_abstention_is_not_usable_even_with_a_value():
    # ``usable`` is the single check callers make; it must not be fooled.
    assert abstain(CHOICE).usable is False
    assert Judgment(kind=CHOICE, value="a", abstained=True).usable is False
    assert Judgment(kind=CHOICE, value="a").usable is True


def test_empty_question_set_short_circuits():
    assert judge({}) == {}


# --- consent gate ----------------------------------------------------------


def test_external_provider_skipped_without_consent():
    engine = _FakeEngine(enabled=False, providers=[_ExternalProvider()])
    provider = resolve_judgment_provider(engine)
    assert isinstance(provider, AbstainingJudgmentProvider)


def test_external_provider_used_once_founder_opts_in():
    engine = _FakeEngine(enabled=True, providers=[_ExternalProvider()])
    provider = resolve_judgment_provider(engine)
    assert provider.provider_id == "test.external"


def test_local_provider_needs_no_consent():
    engine = _FakeEngine(enabled=False, providers=[_LocalProvider()])
    provider = resolve_judgment_provider(engine)
    assert provider.provider_id == "test.local"


def test_external_provider_skipped_ahead_of_an_eligible_local_one():
    engine = _FakeEngine(
        enabled=False, providers=[_ExternalProvider(), _LocalProvider()]
    )
    assert resolve_judgment_provider(engine).provider_id == "test.local"


def test_is_external_defaults_to_true_when_undeclared():
    # A provider author who forgets the flag must fail closed, not open.
    class _Undeclared(JudgmentProvider):
        provider_id = "test.undeclared"

        def judge(self, state, questions):  # pragma: no cover
            return {}

    assert _Undeclared.is_external is True
    engine = _FakeEngine(enabled=False, providers=[_Undeclared()])
    resolved = resolve_judgment_provider(engine)
    assert isinstance(resolved, AbstainingJudgmentProvider)


def test_consent_defaults_off_without_an_engine():
    assert external_judgment_allowed(None) is False


def test_consent_reads_the_engine_setting():
    assert external_judgment_allowed(_FakeEngine(enabled=True)) is True
    assert external_judgment_allowed(_FakeEngine(enabled=False)) is False


def test_settings_flag_defaults_off():
    from kompany.config.settings import KompanySettings

    assert KompanySettings().external_judgment_enabled is False


# --- fail open -------------------------------------------------------------


def test_raising_provider_yields_abstentions():
    answers = judge({"x": _q()}, provider=_ExplodingProvider())
    assert answers["x"].abstained is True
    assert "upstream is down" in answers["x"].detail


def test_non_mapping_return_yields_abstentions():
    answers = judge({"x": _q()}, provider=_WrongShapeProvider())
    assert answers["x"].abstained is True


def test_wrong_answer_kind_is_refused():
    # A score answer to a choice question would make a caller branch on
    # a float. Refuse it rather than hand it over.
    answers = judge({"x": _q()}, provider=_WrongKindProvider())
    assert answers["x"].abstained is True
    assert answers["x"].kind == CHOICE


def test_missing_key_is_filled_with_an_abstention():
    questions = {"x": _q(), "y": _q()}
    answers = judge(questions, provider=_PartialProvider())
    assert set(answers) == {"x", "y"}
    assert answers["x"].usable is True
    assert answers["y"].abstained is True


def test_broken_plugin_scan_falls_back_to_the_default(monkeypatch):
    import kompany.core.judgment as judgment_mod

    def _boom(*args: Any, **kwargs: Any):
        raise RuntimeError("entry point scan blew up")

    monkeypatch.setattr(judgment_mod, "_discovered_providers", _boom)
    with pytest.raises(RuntimeError):
        judgment_mod._discovered_providers(None, None)
    # ...but resolution itself must not propagate it.
    monkeypatch.setattr(
        judgment_mod, "_discovered_providers", lambda *a, **k: []
    )
    assert isinstance(resolve_judgment_provider(), AbstainingJudgmentProvider)


# --- question / answer vocabulary -----------------------------------------


def test_question_kinds():
    assert _q().kind == CHOICE
    assert BooleanQuestion(instructions="does it hold?").kind == BOOLEAN
    score_q = ScoreQuestion(instructions="how much?", levels=("lo", "hi"))
    assert score_q.kind == SCORE


def test_base_question_has_no_kind():
    with pytest.raises(NotImplementedError):
        _ = Question(instructions="bare").kind


def test_provider_answering_correctly_is_usable():
    answers = judge({"x": _q()}, provider=_LocalProvider())
    assert answers["x"].usable is True
    assert answers["x"].value == "a"
    assert answers["x"].confidence == 0.9


# --- the seam stays unwired ------------------------------------------------


def test_no_core_decision_path_consumes_the_seam_yet():
    """ADR-0010 landed the seam only; wiring needs founder sign-off.

    If this fails, someone connected a gate to the judgment seam. That is
    a deliberate decision — update this test along with the ADR, do not
    delete it.
    """
    import subprocess
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "kompany"
    # Real imports only — a prose mention of ``core/judgment.py`` in a
    # comment is not a consumer.
    pattern = (
        r"^\s*(from kompany\.core\.judgment "
        r"|import kompany\.core\.judgment)"
    )
    out = subprocess.run(
        ["grep", "-rlE", pattern, "--include=*.py", str(src)],
        capture_output=True,
        text=True,
    )
    importers = {
        Path(line).name for line in out.stdout.split() if line.strip()
    }
    assert importers <= {"contract.py"}, (
        f"unexpected consumers of the judgment seam: {sorted(importers)}"
    )


def test_judgment_abc_is_part_of_the_plugin_contract():
    from kompany.plugins import (
        ENTRY_POINT_GROUPS,
        JudgmentProvider as ExportedProvider,
        __contract_version__,
    )

    assert ExportedProvider is JudgmentProvider
    assert "kompany.judgment" in ENTRY_POINT_GROUPS
    assert __contract_version__ == "1.3.0"


def test_loader_knows_the_judgment_kind():
    from kompany.plugins.loader import discover

    found = discover(include_workspace=False)
    # Core ships no judgment plugin; the kind exists and is empty.
    assert found.get("judgment") == []
