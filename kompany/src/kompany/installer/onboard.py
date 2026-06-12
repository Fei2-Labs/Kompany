"""The ``kompany onboard`` wizard.

Compresses the legacy four-step install (`pip install` → `.env` → API
key → `kompany init`) into a single command. The flow has four steps:

1. **Environment check** — Python version, data directory writable,
   vault directory creatable.
2. **LLM provider** — pick a provider, capture an API key (vault-encrypted
   or env fallback), and ping the provider once to confirm reachability.
3. **Template selection** — choose a starter company template and apply
   it through :meth:`kompany.core.engine.KompanyEngine.apply_template`.
4. **First directive (optional)** — let the player type a single
   directive that we hand to
   :meth:`kompany.core.engine.KompanyEngine.process_directive`.

Each step is independently skippable through a CLI flag; with all four
flags supplied (plus ``--yes`` for non-interactive confirmation) the
command is fully headless — that's the path the demo video and the
test suite exercise.

The wizard is **idempotent**: if a ``kompany.db`` already exists in the
chosen data directory we ask whether to reuse it, overwrite it, or
cancel. Under ``--yes`` we default to "reuse" so a re-run never
destroys existing player state.

Test-mode escape hatch: the ``KOMPANY_TEST_MODE`` environment variable
short-circuits the LLM ping (step 2) so unit/integration tests don't
need a real API key. Production runs always issue a real ping.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

from rich.console import Console
from rich.panel import Panel

# Tests monkeypatch ``kompany.installer.onboard.Prompt.ask`` — keep the
# class importable on this module even though run_onboard itself doesn't
# prompt directly anymore.
from rich.prompt import Prompt  # noqa: F401


# ---------------------------------------------------------------------------
# Re-exports (ADR-0003 split). Tests and the REST layer import — and
# monkeypatch — these names on THIS module, so the step modules resolve
# the patch-sensitive ones (_ping_llm, _ping_claude_code,
# _detected_subscription_kinds) back through here at call time.
# ---------------------------------------------------------------------------

from kompany.installer.onboard_parts.common import (  # noqa: F401
    HEADLESS_DEFAULT_PROVIDER,
    HEADLESS_DEFAULT_TEMPLATE,
    MIN_PYTHON,
    PROVIDER_ENV_VARS,
    PROVIDER_VAULT_KEYS,
    SUPPORTED_PROVIDERS,
    OnboardError,
    OnboardResult,
    _emit_step,
    _ensure_data_dir,
    _existing_install_state,
    _print_next_steps,
    _resolve_existing_install,
    _step_env_check,
    _wipe_install,
)
from kompany.installer.onboard_parts.company_steps import (  # noqa: F401
    _step_directive,
    _step_founder_profile,
    _step_template,
)
from kompany.installer.onboard_parts.model_source_step import (  # noqa: F401
    _SOURCE_CHOICE_LABELS,
    _choose_model_source,
    _detected_subscription_kinds,
    _setup_claude_subscription_provider,
    _setup_codex_subscription_provider,
    _write_model_source,
)
from kompany.installer.onboard_parts.provider_step import (  # noqa: F401
    _CUSTOM_MODEL_PRIORITY,
    _list_custom_models,
    _pick_latest_custom_model,
    _ping_claude_code,
    _ping_llm,
    _ping_model_for_provider,
    _resolve_api_key,
    _step_provider,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_onboard(
    *,
    yes: bool = False,
    provider: str | None = None,
    api_key: str | None = None,
    template: str | None = None,
    directive: str | None = None,
    data_dir: Path | None = None,
    initial_budget: float | None = None,
    revenue_target: float | None = None,
    customer_target: int | None = None,
    deadline: str | None = None,
    console: Console | None = None,
    engine_factory: Callable[[], Any] | None = None,
) -> OnboardResult:
    """Run the four-step wizard and return a structured result.

    ``engine_factory`` is the test seam: production callers leave it
    ``None`` so we build a real :class:`KompanyEngine`; tests pass a
    callable that returns a stub. Either way, the engine instance is
    constructed *after* idempotency resolution so we never touch a DB
    we're about to wipe.
    """
    cons = console or Console()
    if data_dir is None:
        data_dir = Path("~/.kompany").expanduser()
    else:
        data_dir = Path(data_dir).expanduser().resolve()

    # Make the data dir override visible to KompanySettings so the
    # downstream engine picks it up via env. Save and restore the prior
    # value so we don't leak state across processes (matters in tests
    # that run the wizard multiple times).
    prior_env = os.environ.get("KOMPANY_DATA_DIR")
    os.environ["KOMPANY_DATA_DIR"] = str(data_dir)

    result = OnboardResult(status="completed", data_dir=data_dir)
    try:
        cons.print(
            Panel(
                "[bold]Welcome to Kompany.[/bold] "
                "Let's set up your first company in 90 seconds.",
                title="kompany onboard",
            )
        )

        # ----- Step 1 (env check, no engine yet) ---------------------
        _step_env_check(cons, data_dir, result)

        # ----- Idempotency resolution --------------------------------
        action = _resolve_existing_install(cons, data_dir, yes=yes)
        if action == "cancel":
            cons.print("[yellow]Onboard cancelled by user.[/yellow]")
            result.status = "cancelled"
            return result
        reused = action == "reuse"

        # ----- Build engine ------------------------------------------
        if engine_factory is not None:
            engine = engine_factory()
        else:
            from kompany.core.engine import KompanyEngine

            engine = KompanyEngine()

        # ----- Step 2 (model source + provider) ----------------------
        # 06-11-harness-execution-leg PR5b: detected agent CLIs are
        # offered as zero-key subscription model sources; the existing
        # API-key flow maps to a ``custom_api`` source.
        source_kind: str = "custom_api"
        monthly_fee: float | None = None
        if not reused:
            source_kind, monthly_fee = _choose_model_source(
                cons, yes=yes, provider_flag=provider
            )
        if source_kind == "claude_subscription" and _setup_claude_subscription_provider(
            cons, engine=engine, result=result
        ):
            pass  # zero-key path complete — no API key prompt
        elif source_kind == "openai_subscription" and _setup_codex_subscription_provider(
            cons, engine=engine, result=result
        ):
            pass  # zero-key path complete — codex:* tiers set
        else:
            if source_kind in ("claude_subscription", "openai_subscription"):
                # CLI probe failed — degrade to the API-key flow.
                source_kind, monthly_fee = "custom_api", None
            _step_provider(
                cons,
                yes=yes,
                provider_flag=provider,
                api_key_flag=api_key,
                engine=engine,
                result=result,
                reused=reused,
            )
        if not reused:
            _write_model_source(
                cons,
                engine=engine,
                result=result,
                kind=source_kind,
                monthly_fee=monthly_fee,
            )

        # ----- Step 2.8 (founder profile, optional) -------------------
        # Issues #6/#7: skippable, mirrors the optional first-directive
        # step. Editable later in Settings or `kompany founder`.
        if not reused:
            _step_founder_profile(cons, yes=yes, engine=engine, result=result)

        # ----- Step 3 (template) -------------------------------------
        _step_template(
            cons,
            yes=yes,
            template_flag=template,
            engine=engine,
            result=result,
            reused=reused,
            initial_budget=initial_budget,
            revenue_target=revenue_target,
            customer_target=customer_target,
            deadline=deadline,
        )

        # ----- Step 3.5 (team target feasibility review) -------------
        # Mission-targets task (05-19): fires once on fresh installs.
        # Failures don't block onboarding; the founder can still send
        # the first directive and re-run review later.
        if not reused:
            try:
                review_payload = engine.run_target_feasibility_review()
            except Exception as exc:  # noqa: BLE001
                result.notes.append(f"target feasibility review skipped: {exc}")
            else:
                if review_payload and isinstance(review_payload, dict):
                    result.targets_review_id = review_payload.get("id")
                    cons.print(
                        f"      [green]✓[/green] team feasibility review "
                        f"[dim]({result.targets_review_id})[/dim]"
                    )

        # ----- Step 4 (first directive) ------------------------------
        _step_directive(
            cons,
            yes=yes,
            directive_flag=directive,
            engine=engine,
            result=result,
        )

        if reused:
            result.status = "reused"

        _print_next_steps(cons)
        for note in result.notes:
            cons.print(f"[dim]note: {note}[/dim]")
        return result
    finally:
        if prior_env is None:
            os.environ.pop("KOMPANY_DATA_DIR", None)
        else:
            os.environ["KOMPANY_DATA_DIR"] = prior_env


# ---------------------------------------------------------------------------
# Headless entry point (no typer / no rich) — used by REST + Tauri shell.
# ---------------------------------------------------------------------------


from kompany.installer.onboard_parts.headless import (  # noqa: F401,E402
    is_onboarded,
    onboard_headless,
)
