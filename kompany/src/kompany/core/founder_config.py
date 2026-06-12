"""Founder profile + rules — typed models, engine ops, and enforcement.

Issues #6 (founder_rules) and #7 (founder_profile), grilled 2026-05-27.

Two ``company_config`` JSON rows:

- ``founder_profile`` — presentation: how the team addresses and talks
  to the founder (address, comms style, language, …). Injected into
  EVERY agent system prompt via ``founder_context_block``.
- ``founder_rules`` — enforcement: hybrid hard + soft.

  * **Hard rules** (structured ``{kind, match, action}``) are
    deterministic and enforced at TWO points:
      1. proposal time — excluded capabilities are appended to the
         CEO decomposition/proposal prompts as a NEVER clause AND a
         deterministic post-filter drops matching TaskSpecs/directives
         before any tokens are spent debating them;
      2. execution time — ``check_tool_rules`` gates every tool call
         (excluded / over per-action budget cap / forbidden paid
         category → refused with a clear error).
  * **Soft preferences** (free text) ride along in the same prompt
    block as the profile. Best-effort, never enforced.

Hard-rule kinds:

- ``exclude_capability`` — ``match`` is a capability keyword (e.g.
  ``phone_call``); any task/tool whose text matches is dropped/refused.
- ``budget_cap`` — ``match`` is a per-action USD cap (e.g. ``"10"``);
  any tool action whose estimated/real cost exceeds it is refused.
- ``forbid_paid_category`` — ``match`` is a category keyword; PAID
  tools (SPEND side effect or any cost) matching it are refused.

Constitution invariant (honest assessment): the founder's comms style
and soft preferences shape PHRASING only — they must never soften,
inflate, or otherwise alter the substance of an honest assessment.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROFILE_KEY = "founder_profile"
RULES_KEY = "founder_rules"

PROFILE_AUDIT_EVENT = "settings.founder_profile_changed"
RULES_AUDIT_EVENT = "settings.founder_rules_changed"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FounderProfile(BaseModel):
    """Founder identity/comms prefs — flat, NOT a runnable agent soul."""

    model_config = ConfigDict(extra="forbid")

    address: str | None = None  # how to address the founder ("Clare", "boss")
    pronouns: str | None = None
    comms_style: str | None = None  # "terse, direct, no fluff"
    language: str | None = None  # "zh" / "en"
    working_hours: str | None = None
    timezone: str | None = None
    risk_tolerance: str | None = None


HardRuleKind = Literal["exclude_capability", "budget_cap", "forbid_paid_category"]


class HardRule(BaseModel):
    """One deterministic founder constraint."""

    model_config = ConfigDict(extra="forbid")

    kind: HardRuleKind
    match: str = Field(min_length=1, max_length=200)
    action: str = "skip"


class FounderRules(BaseModel):
    """Hard (enforced) + soft (best-effort prompt text) founder rules."""

    model_config = ConfigDict(extra="forbid")

    hard: list[HardRule] = Field(default_factory=list)
    soft: str = ""


# ---------------------------------------------------------------------------
# company_config JSON storage
# ---------------------------------------------------------------------------


def _read_config_json(engine: Any, key: str) -> dict | None:
    try:
        row = engine.db.execute(
            "SELECT value FROM company_config WHERE key = ?", (key,)
        ).fetchone()
    except Exception:  # noqa: BLE001 — pre-init absence is fine
        return None
    if not row or not row["value"]:
        return None
    try:
        data = json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_config_json(engine: Any, key: str, data: dict | None) -> None:
    if data is None:
        engine.db.execute("DELETE FROM company_config WHERE key = ?", (key,))
    else:
        engine.db.execute(
            """INSERT INTO company_config (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value, updated_at = excluded.updated_at""",
            (key, json.dumps(data, ensure_ascii=False)),
        )
    engine.db.commit()


def _validation_message(exc: ValidationError, what: str) -> str:
    errors = exc.errors()
    if errors:
        loc = ".".join(str(p) for p in errors[0].get("loc", ()))
        msg = str(errors[0].get("msg", f"invalid {what}"))
        return f"{loc}: {msg}" if loc else msg
    return f"invalid {what}"


def _mirror_to_settings(engine: Any, attr: str, value: dict | None) -> None:
    """Keep ``engine.settings.founder_*`` in sync so agents (which only
    see settings) pick the change up without a reboot. Best-effort —
    duck-typed test engines may not carry the field."""
    try:
        setattr(engine.settings, attr, value)
    except (ValueError, TypeError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# Engine ops — profile
# ---------------------------------------------------------------------------


def get_founder_profile(engine: Any) -> dict | None:
    """Stored founder profile dict, or ``None`` when unset."""
    return _read_config_json(engine, PROFILE_KEY)


def set_founder_profile(engine: Any, payload: dict | None) -> dict:
    """Merge-set (or clear with ``None``) the founder profile.

    A partial payload merges over the stored profile so the founder can
    update one field from any surface without re-sending the rest.
    Validation errors surface as ``ValueError`` (same message on every
    interface). Returns ``{"profile": dict|None}``.
    """
    if payload is None:
        profile_dict: dict | None = None
    else:
        merged = dict(get_founder_profile(engine) or {})
        merged.update(payload)
        try:
            profile = FounderProfile.model_validate(merged)
        except ValidationError as exc:
            raise ValueError(_validation_message(exc, "founder profile")) from exc
        profile_dict = profile.model_dump(exclude_none=True)
    _write_config_json(engine, PROFILE_KEY, profile_dict)
    _mirror_to_settings(engine, "founder_profile", profile_dict)
    detail = (
        {"cleared": True}
        if profile_dict is None
        else {"fields": sorted(profile_dict.keys())}
    )
    try:
        engine.audit.record(
            PROFILE_AUDIT_EVENT,
            "Founder profile cleared" if profile_dict is None else "Founder profile updated",
            detail=detail,
        )
    except Exception:  # noqa: BLE001 — audit miss must not block settings
        pass
    return {"profile": profile_dict}


# ---------------------------------------------------------------------------
# Engine ops — rules
# ---------------------------------------------------------------------------


def get_founder_rules(engine: Any) -> dict | None:
    """Stored founder rules dict (``{hard, soft}``), or ``None``."""
    return _read_config_json(engine, RULES_KEY)


def set_founder_rules(engine: Any, payload: dict | None) -> dict:
    """Merge-set (or clear with ``None``) the founder rules.

    Top-level merge: a payload carrying only ``soft`` keeps the stored
    ``hard`` list and vice versa. Returns ``{"rules": dict|None}``.
    """
    if payload is None:
        rules_dict: dict | None = None
    else:
        merged = dict(get_founder_rules(engine) or {})
        merged.update(payload)
        try:
            rules = FounderRules.model_validate(merged)
        except ValidationError as exc:
            raise ValueError(_validation_message(exc, "founder rules")) from exc
        rules_dict = rules.model_dump()
    _write_config_json(engine, RULES_KEY, rules_dict)
    _mirror_to_settings(engine, "founder_rules", rules_dict)
    detail = (
        {"cleared": True}
        if rules_dict is None
        else {
            "hard_count": len(rules_dict.get("hard", [])),
            "has_soft": bool(rules_dict.get("soft")),
        }
    )
    try:
        engine.audit.record(
            RULES_AUDIT_EVENT,
            "Founder rules cleared" if rules_dict is None else "Founder rules updated",
            detail=detail,
        )
    except Exception:  # noqa: BLE001
        pass
    return {"rules": rules_dict}


# ---------------------------------------------------------------------------
# Prompt injection (profile + soft rules) — cheap, no LLM
# ---------------------------------------------------------------------------

_PROFILE_LINES: list[tuple[str, str]] = [
    ("address", "Address the founder as: {v}"),
    ("pronouns", "Founder pronouns: {v}"),
    ("comms_style", "Communication style the founder prefers: {v}"),
    ("language", "Respond to the founder in this language: {v}"),
    ("working_hours", "Founder working hours: {v}"),
    ("timezone", "Founder timezone: {v}"),
    ("risk_tolerance", "Founder risk tolerance: {v}"),
]


def founder_context_block(
    profile: dict | None, rules: dict | None
) -> str:
    """Build the founder-context block injected into agent prompts.

    Pure string formatting — never an LLM call. Constitution invariant:
    comms style / soft preferences shape PHRASING only; they must never
    alter the substance of an honest assessment, and the block says so
    explicitly so agents don't soften bad news to please the founder.
    """
    lines: list[str] = []
    for key, template in _PROFILE_LINES:
        value = (profile or {}).get(key)
        if value:
            lines.append(template.format(v=value))
    soft = ((rules or {}).get("soft") or "").strip()
    if soft:
        lines.append(f"Founder preferences (best-effort): {soft}")
    if not lines:
        return ""
    lines.append(
        "Style shapes phrasing ONLY — never soften or alter the "
        "substance of an honest assessment."
    )
    return "Founder context:\n" + "\n".join(f"- {line}" for line in lines)


# ---------------------------------------------------------------------------
# Hard-rule enforcement — proposal time
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return " ".join(str(text).lower().replace("_", " ").replace("-", " ").split())


def excluded_capabilities(rules: dict | None) -> list[str]:
    """``match`` values of every ``exclude_capability`` hard rule."""
    out: list[str] = []
    for rule in (rules or {}).get("hard", []) or []:
        if rule.get("kind") == "exclude_capability" and rule.get("match"):
            out.append(str(rule["match"]))
    return out


def matches_excluded(text: str, match: str) -> bool:
    """Case-insensitive, ``_``/``-``-insensitive substring match."""
    return _normalize(match) in _normalize(text)


def never_propose_clause(rules: dict | None) -> str:
    """Prompt clause for CEO proposal/decomposition prompts; '' if none."""
    excluded = excluded_capabilities(rules)
    if not excluded:
        return ""
    return (
        "\nFounder hard rules — NEVER propose tasks requiring: "
        + ", ".join(excluded)
        + ". Do not discuss or plan around them; they are excluded.\n"
    )


def filter_task_specs(specs: list, rules: dict | None) -> tuple[list, list]:
    """Deterministic post-filter: drop specs matching an excluded
    capability. Works on anything carrying ``title``/``prompt``
    attributes (TaskSpec) — returns ``(kept, dropped)``."""
    excluded = excluded_capabilities(rules)
    if not excluded:
        return list(specs), []
    kept, dropped = [], []
    for spec in specs:
        text = f"{getattr(spec, 'title', '')} {getattr(spec, 'prompt', '')}"
        if any(matches_excluded(text, m) for m in excluded):
            dropped.append(spec)
        else:
            kept.append(spec)
    return kept, dropped


def filter_directive_dicts(
    directives: list[dict], rules: dict | None
) -> tuple[list[dict], list[dict]]:
    """Same deterministic filter for proposed-directive dicts
    (title/rationale/week_plan text)."""
    excluded = excluded_capabilities(rules)
    if not excluded:
        return list(directives), []
    kept, dropped = [], []
    for d in directives:
        text = " ".join(
            [
                str(d.get("title", "")),
                str(d.get("rationale", "")),
                " ".join(str(x) for x in (d.get("week_plan") or [])),
            ]
        )
        if any(matches_excluded(text, m) for m in excluded):
            dropped.append(d)
        else:
            kept.append(d)
    return kept, dropped


# ---------------------------------------------------------------------------
# Hard-rule enforcement — execution time
# ---------------------------------------------------------------------------


def check_tool_rules(
    rules: dict | None,
    *,
    tool_name: str,
    side_effect: str = "read",
    estimated_cost_usd: float = 0.0,
    description: str = "",
) -> str | None:
    """Gate one tool action against the founder's hard rules.

    Returns a founder-readable refusal reason, or ``None`` when the
    action passes. Consulted by ``execute_tool`` (inline path) and
    ``engine.propose_action`` (approval path) so a blocked action never
    even reaches the inbox.
    """
    hard = (rules or {}).get("hard", []) or []
    if not hard:
        return None
    text = f"{tool_name} {description}"
    cost = float(estimated_cost_usd or 0.0)
    paid = side_effect == "spend" or cost > 0
    for rule in hard:
        kind = rule.get("kind")
        match = str(rule.get("match", ""))
        if kind == "exclude_capability" and match and matches_excluded(text, match):
            return (
                f"blocked by founder rule: capability '{match}' is excluded "
                f"(tool {tool_name})"
            )
        if kind == "budget_cap":
            try:
                cap = float(match)
            except (TypeError, ValueError):
                continue
            if cost > cap:
                return (
                    f"blocked by founder rule: per-action budget cap "
                    f"${cap:.2f} exceeded (estimated ${cost:.2f} for {tool_name})"
                )
        if kind == "forbid_paid_category" and match and paid:
            if matches_excluded(text, match):
                return (
                    f"blocked by founder rule: paid category '{match}' is "
                    f"forbidden (tool {tool_name})"
                )
    return None


__all__ = [
    "FounderProfile",
    "FounderRules",
    "HardRule",
    "PROFILE_AUDIT_EVENT",
    "PROFILE_KEY",
    "RULES_AUDIT_EVENT",
    "RULES_KEY",
    "check_tool_rules",
    "excluded_capabilities",
    "filter_directive_dicts",
    "filter_task_specs",
    "founder_context_block",
    "get_founder_profile",
    "get_founder_rules",
    "matches_excluded",
    "never_propose_clause",
    "set_founder_profile",
    "set_founder_rules",
]
