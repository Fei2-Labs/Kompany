"""Judgment seam — a replaceable source of typed, calibrated verdicts.

Kompany already decides things at several layers: pure-code hard gates
(``core/autonomy.py``, ``core/outward_policy.py``), heuristic gates
(``core/deai_gate.py``, ``core/fabrication_check.py``) and economy-tier
LLM judges (the de-AI LLM layer, ``core/debate.py``'s CEO decision). Those
layers stay exactly as they are. This module adds the *seam* a better
judgment source can be plugged into later, without any gate having to
learn who is answering.

Three deliberate properties:

* **Provider-agnostic.** Core names no vendor and imports no client. The
  question/answer vocabulary here (choice / boolean / score) is the
  ordinary shape of a calibrated judgment, not any one service's API.
* **Core is complete without a provider.** The shipped default,
  :class:`AbstainingJudgmentProvider`, answers every question with
  ``abstained=True``. A caller that sees an abstention MUST use whatever
  verdict it would have produced anyway. Nothing in Core degrades when no
  plugin is installed — that is the normal state of an AGPL self-hosted
  install.
* **External egress is opt-in.** A provider that reaches a third-party
  service declares ``is_external = True`` and is refused unless the
  founder turned on ``external_judgment_enabled``. Sending a company's
  directives, outward copy or financial context off the founder's own
  machine is their call to make, never a default.

Failure policy: a provider that raises, hangs or returns garbage yields
abstentions, so the existing deterministic verdict stands. Judgment is an
*upgrade* to a decision that already has an answer — never the only thing
standing between the engine and an action.

NOTE (2026-09-25): no Core gate consumed this seam at first landing. It
was landed ahead of any provider so the surface is public API before
the OSS announcement.

NOTE (2026-09-25, ADR-0011): the founder-tunable auto-approve check
(``core/engine_parts/surfaces.py._auto_approve_eligible``, via
``core/approval_judgment.py``) is now the seam's first consumer. It can
only turn an already-eligible auto-approve into a hold, never grant
eligibility a deterministic policy withheld — with no provider
installed it remains a no-op. See ADR-0011 before wiring a second one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

# Answer kinds. A provider must return the kind matching the question.
CHOICE = "choice"
BOOLEAN = "boolean"
SCORE = "score"

_KINDS = frozenset({CHOICE, BOOLEAN, SCORE})

# Plugin kind / entry-point group for judgment providers.
JUDGMENT_PLUGIN_KIND = "judgment"


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    """One narrow judgment to be answered against shared state.

    ``instructions`` carries the complete meaning of the question — the
    dict key a caller files it under is for the caller's own code and is
    not guaranteed to reach the provider.
    """

    instructions: str

    @property
    def kind(self) -> str:  # pragma: no cover — overridden by subclasses
        raise NotImplementedError


@dataclass(frozen=True)
class ChoiceQuestion(Question):
    """Pick exactly one of a defined set of options.

    ``options`` maps a stable option id (what code branches on) to a
    description of the situation that option represents.
    """

    options: Mapping[str, str] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return CHOICE


@dataclass(frozen=True)
class BooleanQuestion(Question):
    """Whether a condition holds. The answer is a probability of yes.

    A value near 0.5 means "roughly as likely as not" — it does NOT mean
    "medium intensity". Callers that need a degree want
    :class:`ScoreQuestion` instead.
    """

    when_true: str = ""
    when_false: str = ""

    @property
    def kind(self) -> str:
        return BOOLEAN


@dataclass(frozen=True)
class ScoreQuestion(Question):
    """Position along an ordered dimension.

    ``levels`` is ordered low → high; each level must describe a concrete
    situation that stands on its own. Comparable per-item score questions
    on one shared scale are how a caller builds a graded ranking.
    """

    levels: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        return SCORE


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Judgment:
    """One answer, with its uncertainty attached.

    ``value`` is the chosen option id (choice), the probability of yes
    (boolean), or the probability-weighted position on the level scale
    (score). ``probabilities`` is the full distribution when the provider
    supplies one — callers that only read ``value`` throw away the part
    that tells them whether to trust it.

    ``confidence`` summarises how concentrated the distribution is. It is
    NOT a claim that the answer is correct, and it is not permission to
    act: a high-confidence judgment still passes through every hard gate.

    ``abstained`` is the contract that keeps Core working without a
    provider. When True the caller MUST fall back to its own verdict and
    must not read ``value``.
    """

    kind: str
    value: Any = None
    probabilities: Mapping[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    abstained: bool = False
    detail: str = ""

    @property
    def usable(self) -> bool:
        """True when a caller may branch on ``value``."""
        return not self.abstained and self.kind in _KINDS


def abstain(kind: str, detail: str = "") -> Judgment:
    """Build an abstention — the answer Core is always able to produce."""
    return Judgment(kind=kind, abstained=True, detail=detail)


def abstain_all(
    questions: Mapping[str, Question], detail: str = ""
) -> dict[str, Judgment]:
    """Abstain on every question in ``questions``."""
    out: dict[str, Judgment] = {}
    for key, question in (questions or {}).items():
        kind = getattr(question, "kind", "")
        out[str(key)] = abstain(kind if kind in _KINDS else CHOICE, detail)
    return out


# ---------------------------------------------------------------------------
# The provider surface
# ---------------------------------------------------------------------------


class JudgmentProvider(ABC):
    """A source of typed judgments. Core ships one; plugins may ship more.

    Implementations MUST be side-effect free with respect to company
    state: a judgment observes, it never acts. They SHOULD answer every
    question in one round trip — the questions in a single call are
    independent and cannot see each other's answers.

    An implementation that cannot answer returns abstentions rather than
    raising; :func:`judge` also catches, so a provider that raises anyway
    degrades instead of taking a decision path down with it.
    """

    provider_id: str = ""
    """Dotted id, e.g. ``"myvendor.judgment"``."""

    display_name: str = ""

    is_external: bool = True
    """Does answering send state off this machine?

    Defaults to True so an implementation that forgets to declare itself
    is treated as egress and stays behind the founder's consent flag.
    Only set False for a provider that answers locally.
    """

    cost_hint_usd: float = 0.0
    """Rough per-call cost, for the founder's cost visibility. 0 = free."""

    @abstractmethod
    def judge(
        self,
        state: Mapping[str, Any],
        questions: Mapping[str, Question],
    ) -> Mapping[str, Judgment]:
        """Answer ``questions`` against ``state``.

        ``state`` holds facts — source text, identities, relationships,
        policies, current values — and never conclusions. The returned
        mapping SHOULD carry one entry per question key; missing keys are
        filled with abstentions by :func:`judge`.
        """


class AbstainingJudgmentProvider(JudgmentProvider):
    """Core's default: answers nothing, so every caller keeps its own verdict.

    This is not a placeholder to be replaced before shipping — it is the
    correct behaviour for an install with no judgment plugin, which is
    every install by default.
    """

    provider_id = "core.abstain"
    display_name = "No judgment provider"
    is_external = False

    def judge(
        self,
        state: Mapping[str, Any],
        questions: Mapping[str, Question],
    ) -> Mapping[str, Judgment]:
        return abstain_all(questions, detail="no judgment provider configured")


# ---------------------------------------------------------------------------
# Resolution + the safe call wrapper
# ---------------------------------------------------------------------------


def external_judgment_allowed(engine: Any = None) -> bool:
    """Has the founder opted in to off-machine judgment?

    Reads ``external_judgment_enabled`` from the engine's settings, and
    falls back to a bare settings object when no engine is supplied.
    Anything unreadable answers False — consent is never assumed.
    """
    settings = None
    if engine is not None:
        settings = getattr(engine, "settings", None)
    if settings is None:
        try:
            from kompany.config.settings import KompanySettings

            settings = KompanySettings()
        except Exception:  # noqa: BLE001 — unreadable settings = no consent
            return False
    return bool(getattr(settings, "external_judgment_enabled", False))


def resolve_judgment_provider(
    engine: Any = None,
    *,
    data_dir: Any = None,
) -> JudgmentProvider:
    """Return the active provider — never None, never raises.

    Order: a plugin-supplied provider that clears the consent gate, else
    :class:`AbstainingJudgmentProvider`. An external provider found while
    ``external_judgment_enabled`` is off is skipped, not error — the
    founder installed it but has not switched it on.
    """
    cached = getattr(engine, "judgment_provider", None) if engine else None
    if isinstance(cached, JudgmentProvider):
        return cached

    allow_external = external_judgment_allowed(engine)
    for candidate in _discovered_providers(engine, data_dir):
        if not isinstance(candidate, JudgmentProvider):
            continue
        if getattr(candidate, "is_external", True) and not allow_external:
            continue
        return candidate
    return AbstainingJudgmentProvider()


def _discovered_providers(engine: Any, data_dir: Any) -> list[Any]:
    """Plugin providers, or [] when discovery is unavailable or broken."""
    cached = getattr(engine, "judgment_providers", None) if engine else None
    if cached is not None:
        return list(cached)
    try:
        from kompany.plugins.loader import registered

        return list(registered(JUDGMENT_PLUGIN_KIND, data_dir))
    except Exception:  # noqa: BLE001 — a broken scan abstains, never crashes
        return []


def judge(
    questions: Mapping[str, Question],
    state: Mapping[str, Any] | None = None,
    *,
    provider: JudgmentProvider | None = None,
    engine: Any = None,
) -> dict[str, Judgment]:
    """Ask ``questions`` and always get one answer per key back.

    The fail-open contract in one place: a provider that raises, returns
    a non-mapping, or omits a key yields an abstention for the affected
    questions, so the caller's own deterministic verdict stands. Callers
    do not need their own try/except around this.
    """
    questions = dict(questions or {})
    if not questions:
        return {}
    active = provider or resolve_judgment_provider(engine)
    try:
        raw = active.judge(dict(state or {}), questions)
    except Exception as exc:  # noqa: BLE001 — degrade, never propagate
        return abstain_all(questions, detail=f"provider failed: {exc!r}")
    if not isinstance(raw, Mapping):
        return abstain_all(questions, detail="provider returned a non-mapping")

    out: dict[str, Judgment] = {}
    for key, question in questions.items():
        answer = raw.get(key)
        if isinstance(answer, Judgment) and answer.kind == question.kind:
            out[key] = answer
        elif isinstance(answer, Judgment):
            out[key] = abstain(
                question.kind,
                detail=f"provider answered {answer.kind!r}, expected "
                f"{question.kind!r}",
            )
        else:
            out[key] = abstain(
                question.kind, detail="provider omitted this key"
            )
    return out


__all__ = [
    "BOOLEAN",
    "CHOICE",
    "JUDGMENT_PLUGIN_KIND",
    "SCORE",
    "AbstainingJudgmentProvider",
    "BooleanQuestion",
    "ChoiceQuestion",
    "Judgment",
    "JudgmentProvider",
    "Question",
    "ScoreQuestion",
    "abstain",
    "abstain_all",
    "external_judgment_allowed",
    "judge",
    "resolve_judgment_provider",
]
