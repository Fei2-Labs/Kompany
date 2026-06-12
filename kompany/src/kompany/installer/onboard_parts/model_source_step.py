"""Step 2.5 — model source selection (06-11-harness-execution-leg PR5b).

Split out of ``onboard.py`` (ADR-0003). Tests monkeypatch
``kompany.installer.onboard._detected_subscription_kinds`` /
``._ping_claude_code``, so call sites here resolve those names through
the ``onboard`` module at call time.
"""

from __future__ import annotations

import os
from typing import Any

from rich.console import Console
from rich.prompt import Prompt

from kompany.installer.onboard_parts.common import OnboardResult, _emit_step


def _onboard():
    """Late import of the facade module so monkeypatches apply."""
    from kompany.installer import onboard

    return onboard


# Founder-facing labels for the detected zero-key subscription options.
_SOURCE_CHOICE_LABELS: dict[str, str] = {
    "claude_subscription": "claude-subscription",
    "openai_subscription": "openai-subscription",
}


def _detected_subscription_kinds() -> dict[str, dict[str, Any]]:
    """source_kind → CLI info for detected zero-key subscription CLIs."""
    from kompany.core.model_source_ops import detect_agent_clis

    found: dict[str, dict[str, Any]] = {}
    for name, info in detect_agent_clis().items():
        kind = info.get("source_kind")
        if info.get("found") and kind in _SOURCE_CHOICE_LABELS:
            found[kind] = {**info, "cli": name}
    return found


def _choose_model_source(
    console: Console, *, yes: bool, provider_flag: str | None
) -> tuple[str, float | None]:
    """Pick a model source kind; returns ``(kind, monthly_fee_usd)``.

    Headless mode and an explicit ``--provider`` flag always take the
    API-key path (``custom_api``). Interactively, detected agent CLIs
    are offered as zero-key subscription options — defaulting to the
    API-key path so nothing changes unless the founder opts in.
    """
    if yes or provider_flag:
        return "custom_api", None
    try:
        detected = _onboard()._detected_subscription_kinds()
    except Exception:  # noqa: BLE001 — detection is best-effort
        detected = {}
    if not detected:
        return "custom_api", None
    badges = ", ".join(
        f"{info['cli']}{' ' + info['version'] if info.get('version') else ''}"
        for info in detected.values()
    )
    console.print(f"      [green]✓[/green] agent CLIs detected: {badges}")
    choices = [_SOURCE_CHOICE_LABELS[k] for k in detected] + ["api-key"]
    picked = Prompt.ask("      Model source", choices=choices, default="api-key")
    kind = next(
        (k for k, label in _SOURCE_CHOICE_LABELS.items() if label == picked),
        "custom_api",
    )
    if kind == "custom_api":
        return kind, None
    fee_text = Prompt.ask("      Monthly subscription fee (USD)", default="20")
    try:
        fee = float(fee_text)
    except (TypeError, ValueError):
        fee = 20.0
    return kind, fee


def _setup_claude_subscription_provider(
    console: Console, *, engine: Any, result: OnboardResult
) -> bool:
    """Zero-key provider setup when the claude subscription was picked.

    Probes the local ``claude`` CLI (subscription auth — no API key) and
    routes the single-shot model tiers through the ``claude-code``
    provider, persisting the pick the same way the custom-provider path
    does. Returns ``False`` when the probe fails so the caller can fall
    back to the API-key flow.
    """
    _emit_step(console, 2, "LLM provider")
    test_mode = os.environ.get("KOMPANY_TEST_MODE", "") == "1"
    detail = None if test_mode else _onboard()._ping_claude_code()
    if detail is not None:
        console.print(
            f"      [yellow]claude CLI probe failed: {detail} — "
            "falling back to API-key setup.[/yellow]"
        )
        result.notes.append(f"claude subscription probe failed: {detail}")
        return False
    console.print(
        "      [green]✓[/green] claude CLI reachable "
        "(subscription auth — no API key needed)"
    )
    result.provider = "claude_code"
    result.api_key_storage = None
    result.ping_status = "skipped_test_mode" if test_mode else "ok"
    model = "claude-code:sonnet"
    try:
        engine.db.execute(
            """INSERT INTO company_config (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value, updated_at = datetime('now')""",
            ("custom_model_picked", model),
        )
    except Exception as exc:  # noqa: BLE001 — tier persist is best-effort
        result.notes.append(f"model tier persist failed ({exc})")
    for attr in ("model_apex", "model_primary", "model_economy"):
        try:
            setattr(engine.settings, attr, model)
        except (ValueError, TypeError):
            pass
    return True


def _setup_codex_subscription_provider(
    console: Console, *, engine: Any, result: OnboardResult
) -> bool:
    """Zero-key provider setup when the OpenAI subscription was picked.

    Mirrors :func:`_setup_claude_subscription_provider` for the ``codex``
    CLI (issue #18): single-shot tiers route through the ``codex:*``
    provider, so the C-suite's L2 calls ride the ChatGPT subscription
    too — no API key prompt. The probe is a cheap PATH + ``--version``
    check (a real ``codex exec`` round-trip would spend plan quota at
    onboarding); auth problems surface on the first call with a clear
    provider error. Returns ``False`` to fall back to the API-key flow.
    """
    import shutil
    import subprocess

    _emit_step(console, 2, "LLM provider")
    test_mode = os.environ.get("KOMPANY_TEST_MODE", "") == "1"
    detail: str | None = None
    if not test_mode:
        if shutil.which("codex") is None:
            detail = "codex CLI not found on PATH"
        else:
            try:
                proc = subprocess.run(
                    ["codex", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proc.returncode != 0:
                    detail = f"codex --version exited {proc.returncode}"
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {exc}"
    if detail is not None:
        console.print(
            f"      [yellow]codex CLI probe failed: {detail} — "
            "falling back to API-key setup.[/yellow]"
        )
        result.notes.append(f"openai subscription probe failed: {detail}")
        return False
    console.print(
        "      [green]✓[/green] codex CLI found "
        "(ChatGPT subscription auth — no API key needed)"
    )
    result.provider = "codex_cli"
    result.api_key_storage = None
    result.ping_status = "skipped_test_mode" if test_mode else "ok"
    model = "codex:gpt-5"
    try:
        engine.db.execute(
            """INSERT INTO company_config (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value, updated_at = datetime('now')""",
            ("custom_model_picked", model),
        )
    except Exception as exc:  # noqa: BLE001 — tier persist is best-effort
        result.notes.append(f"model tier persist failed ({exc})")
    for attr in ("model_apex", "model_primary", "model_economy"):
        try:
            setattr(engine.settings, attr, model)
        except (ValueError, TypeError):
            pass
    return True


def _write_model_source(
    console: Console,
    *,
    engine: Any,
    result: OnboardResult,
    kind: str,
    monthly_fee: float | None,
) -> None:
    """Persist the chosen model source via the engine (non-fatal)."""
    setter = getattr(engine, "set_model_source", None)
    if setter is None:
        return
    payload: dict[str, Any] = {"kind": kind}
    if monthly_fee is not None:
        payload["monthly_fee_usd"] = float(monthly_fee)
    try:
        setter(payload)
    except Exception as exc:  # noqa: BLE001 — settings stay editable later
        result.notes.append(f"model source not saved: {exc}")
        return
    result.model_source_kind = kind
    console.print(f"      [green]✓[/green] model source: {kind}")
