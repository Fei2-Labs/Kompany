"""Step 2 — provider selection, API key, and LLM connectivity ping.

Split out of ``onboard.py`` (ADR-0003). Tests monkeypatch
``kompany.installer.onboard._ping_llm`` / ``._ping_claude_code``, so
call sites here resolve those names through the ``onboard`` module at
call time.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

import typer
from rich.console import Console
from rich.prompt import Prompt

from kompany.installer.onboard_parts.common import (
    HEADLESS_DEFAULT_PROVIDER,
    PROVIDER_ENV_VARS,
    PROVIDER_VAULT_KEYS,
    SUPPORTED_PROVIDERS,
    OnboardResult,
    _emit_step,
)

logger = logging.getLogger(__name__)


def _onboard():
    """Late import of the facade module so monkeypatches apply."""
    from kompany.installer import onboard

    return onboard


def _resolve_api_key(
    provider: str,
    flag_value: str | None,
) -> str | None:
    """Pick an API key from (flag, then env var). Empty string ≠ provided."""
    if flag_value:
        return flag_value
    env_var = PROVIDER_ENV_VARS.get(provider)
    if env_var:
        env_value = os.environ.get(env_var, "")
        if env_value:
            return env_value
    return None


def _ping_llm(
    provider: str,
    api_key: str,
    *,
    settings_factory: Callable[[], Any] | None = None,
    model_override: str | None = None,
) -> tuple[bool, str]:
    """Single tiny LLM call to confirm the key works.

    ``KOMPANY_TEST_MODE=1`` bypasses the network entirely — used by the
    test suite and by anyone smoke-testing without an API key handy.

    Returns ``(ok, detail)``. ``ok=False`` is non-fatal in interactive
    mode; the caller surfaces retry/skip/abort.
    """
    if os.environ.get("KOMPANY_TEST_MODE", "") == "1":
        return True, "skipped_test_mode"
    # Build a minimal settings shim so we can use the existing LLMClient
    # without having to spin up a full engine just to ping.
    try:
        from kompany.config.settings import KompanySettings
        from kompany.llm.client import LLMClient
        from kompany.llm.cost_tracker import CostTracker
        from kompany.llm.providers import Provider
        from kompany.state.database import Database
        from kompany.state.ledger import Ledger
    except Exception as exc:  # pragma: no cover — import errors surface elsewhere
        return False, f"import_error: {exc}"

    if settings_factory is not None:
        settings = settings_factory()
    else:
        settings = KompanySettings()
        # Inject the key on the in-memory settings instance so the
        # client can read it back. We don't persist this; the vault
        # write is the persistent record.
        attr = PROVIDER_VAULT_KEYS.get(provider)
        if attr:
            setattr(settings, attr, api_key)

    # Use a temp-on-disk DB so cost_tracker has somewhere to write the
    # ledger entry. The DB is thrown away after the ping.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp))
        ledger = Ledger(db)
        cost = CostTracker(ledger)
        client = LLMClient(
            settings=settings,
            cost_tracker=cost,
            # No watchdog → legacy unguarded path; that's what we want
            # for a one-shot reachability ping.
            watchdog=None,
        )
        model = model_override or _ping_model_for_provider(provider, settings)
        logger.info("ping using provider=%s model=%s", provider, model)
        try:
            provider_enum = Provider(provider)
        except ValueError:
            provider_enum = None
        try:
            client.call(
                model=model,
                system="You are a connectivity probe. Reply with one word.",
                prompt="ping",
                agent_name="onboard",
                max_tokens=8,
                provider_override=provider_enum,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


def _ping_claude_code(timeout: float = 120.0) -> str | None:
    """Probe the local ``claude`` CLI for the claude_code provider.

    No SDK and no API key — the CLI carries the founder's subscription
    auth. First a PATH lookup, then a minimal headless round-trip so
    subscription auth problems surface at onboarding instead of at the
    first directive dispatch.

    Returns ``None`` on success, otherwise a human-readable failure
    detail string (the same shape as the detail half of
    :func:`_ping_llm`).
    """
    import json
    import shutil
    import subprocess

    if shutil.which("claude") is None:
        return (
            "claude CLI not found on PATH — install Claude Code "
            "first, or pick an API-key provider"
        )
    try:
        proc = subprocess.run(
            ["claude", "-p", "ping", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "").strip()[-500:]
            return (
                f"claude CLI exited {proc.returncode}: "
                f"{stderr_tail or '(no stderr)'}"
            )
        json.loads(proc.stdout)  # must be valid JSON
        return None
    except subprocess.TimeoutExpired:
        return f"claude CLI timed out after {timeout:.0f}s"
    except json.JSONDecodeError as exc:
        return f"claude CLI returned non-JSON output: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


_CUSTOM_MODEL_PRIORITY: tuple[str, ...] = (
    "gpt-5",
    "gpt-4.1",
    "gpt-4o",
    "claude-opus-4",
    "claude-sonnet-4",
    "claude-haiku-4",
    "gemini-2",
    "gemini-1.5",
    "deepseek",
    "qwen",
    "glm-4",
    "moonshot",
)


def _list_custom_models(base_url: str, api_key: str) -> list[str]:
    """Fetch model ids from an OpenAI-compatible ``/models`` endpoint.

    Thin wrapper over :func:`kompany.llm.providers.list_openai_compatible_models`
    so the provider SDK touchpoint stays inside the ``llm/`` layer. See
    :func:`tests.test_llm_spend_coverage.test_no_direct_provider_sdk_use_outside_llm_layer`.
    """
    from kompany.llm.providers import list_openai_compatible_models

    return list_openai_compatible_models(base_url=base_url, api_key=api_key)


def _pick_latest_custom_model(ids: list[str]) -> str | None:
    if not ids:
        return None
    lowered = [(i, i.lower()) for i in ids]
    for prefix in _CUSTOM_MODEL_PRIORITY:
        matches = [orig for orig, low in lowered if low.startswith(prefix)]
        if matches:
            matches.sort(reverse=True)
            return matches[0]
    return sorted(ids, reverse=True)[0]


def _ping_model_for_provider(provider: str, settings: Any) -> str:
    """Pick the cheapest available model for a connectivity probe."""
    # Prefer the configured economy tier — it's what every provider's
    # cost-conscious smoke test should use.
    if provider == "anthropic":
        return getattr(settings, "model_economy", "claude-haiku-4-20250414")
    if provider == "openai":
        return "gpt-4o-mini"
    if provider == "gemini":
        return "gemini-1.5-flash"
    if provider == "glm":
        return "glm-4-flash"
    if provider == "kimi":
        return "moonshot-v1-8k"
    # Custom: discovery happens at the API boundary (so failures surface
    # as classified ping errors). If we get here without an override, the
    # caller didn't discover — return a neutral placeholder that won't
    # match any provider prefix.
    return "custom-unset"


def _step_provider(
    console: Console,
    *,
    yes: bool,
    provider_flag: str | None,
    api_key_flag: str | None,
    engine: Any,
    result: OnboardResult,
    reused: bool,
) -> None:
    _emit_step(console, 2, "LLM provider")

    if reused:
        # Reuse path: don't re-prompt or re-ping. We trust whatever the
        # previous install configured.
        console.print("      [dim]reusing existing provider credentials[/dim]")
        result.api_key_storage = "reused"
        result.ping_status = "skipped"
        # Best-effort: surface which provider has a key in the vault.
        # The vault probe can fail (missing vault_key, etc.); the reuse
        # path must never crash because we couldn't *describe* the prior
        # state — that's diagnostic, not load-bearing.
        for prov, vault_key in PROVIDER_VAULT_KEYS.items():
            try:
                if engine.credentials.get(vault_key):
                    result.provider = prov
                    break
            except Exception:  # noqa: BLE001
                continue
        # Fall back to env-var detection if vault probe found nothing.
        if result.provider is None:
            for prov, env_var in PROVIDER_ENV_VARS.items():
                if os.environ.get(env_var):
                    result.provider = prov
                    break
        return

    # Provider selection ---------------------------------------------------
    provider = provider_flag
    if not provider:
        if yes:
            provider = HEADLESS_DEFAULT_PROVIDER
        else:
            provider = Prompt.ask(
                "      Provider",
                choices=list(SUPPORTED_PROVIDERS),
                default=HEADLESS_DEFAULT_PROVIDER,
            )
    if provider not in SUPPORTED_PROVIDERS:
        console.print(
            f"[red]Unknown provider {provider!r}. "
            f"Choose one of: {', '.join(SUPPORTED_PROVIDERS)}.[/red]"
        )
        raise typer.Exit(2)
    result.provider = provider

    # API key resolution ---------------------------------------------------
    api_key = _resolve_api_key(provider, api_key_flag)
    if not api_key:
        if yes:
            env_var = PROVIDER_ENV_VARS.get(provider, "?")
            console.print(
                f"[red]Headless mode requires an API key. "
                f"Pass --api-key=... or set {env_var}.[/red]"
            )
            raise typer.Exit(2)
        api_key = Prompt.ask(
            f"      {provider} API key",
            password=True,
        )
        if not api_key:
            console.print("[red]API key is required.[/red]")
            raise typer.Exit(2)

    # Persist the key. We always *try* the vault first — that's the
    # safer storage (encrypted at rest). If the vault isn't initialised
    # yet (no KOMPANY_VAULT_KEY anywhere) we fall back to setting the
    # in-process settings only and tell the player to set the env var
    # for future sessions.
    vault_field = PROVIDER_VAULT_KEYS.get(provider)
    storage = "env"
    if vault_field and engine.settings.vault_key:
        try:
            engine.credentials.set(vault_field, api_key)
            storage = "vault"
        except Exception as exc:  # noqa: BLE001
            console.print(
                f"[yellow]Vault write failed ({exc}); using process env only.[/yellow]"
            )
    else:
        result.notes.append(
            f"vault key not configured; set {PROVIDER_ENV_VARS[provider]} "
            f"in your shell to make the API key persistent across runs."
        )
    # Always reflect the key on the live settings so the upcoming ping +
    # any same-process directive call can find it.
    if vault_field:
        setattr(engine.settings, vault_field, api_key)
    result.api_key_storage = storage

    # Ping -----------------------------------------------------------------
    ok, detail = _onboard()._ping_llm(
        provider,
        api_key,
        settings_factory=lambda: engine.settings,
    )
    if ok and detail == "skipped_test_mode":
        console.print("      [yellow]✓[/yellow] LLM ping skipped (KOMPANY_TEST_MODE=1)")
        result.ping_status = "skipped_test_mode"
        return
    if ok:
        console.print(f"      [green]✓[/green] {provider} reachable")
        result.ping_status = "ok"
        return

    # Ping failed. Headless never offers an interactive retry — surface
    # the error and bail with a clear exit code. Interactive players get
    # retry/skip/abort.
    console.print(f"      [red]✗ {provider} ping failed: {detail}[/red]")
    if yes:
        console.print(
            "[red]Provider unreachable in headless mode. "
            "Re-run interactively to retry, or fix your network/key and try again.[/red]"
        )
        raise typer.Exit(2)
    action = Prompt.ask(
        "      Retry / Skip / Abort?",
        choices=["retry", "skip", "abort"],
        default="retry",
    )
    if action == "abort":
        console.print("[red]Aborted by user.[/red]")
        raise typer.Exit(1)
    if action == "skip":
        result.ping_status = "skipped"
        result.notes.append("LLM ping skipped after failure; directives may not work.")
        return
    # Retry: one more attempt, then accept whatever happens.
    ok2, detail2 = _onboard()._ping_llm(provider, api_key, settings_factory=lambda: engine.settings)
    if ok2:
        console.print(f"      [green]✓[/green] {provider} reachable")
        result.ping_status = "ok"
    else:
        console.print(f"      [yellow]Still failing: {detail2}. Continuing anyway.[/yellow]")
        result.ping_status = "failed"
        result.notes.append(f"LLM ping failed twice ({detail2}); directives may not work.")
