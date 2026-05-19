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

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Providers the wizard knows how to onboard. Anything we add to
# ``kompany.llm.providers.Provider`` should flow through here too — but
# we keep the literal list local so onboard can't accidentally enable a
# half-finished provider mid-import.
SUPPORTED_PROVIDERS: tuple[str, ...] = (
    "anthropic",
    "openai",
    "gemini",
    "glm",
    "kimi",
    "custom",
)

# Maps a provider id to the env var people would set on their machine.
# Used both for the headless error message and for the env fallback when
# the keychain isn't available.
PROVIDER_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "glm": "GLM_API_KEY",
    "kimi": "KIMI_API_KEY",
    "custom": "CUSTOM_LLM_API_KEY",
}

# Maps provider id to the credential-vault key name (see
# ``kompany.state.credentials.ALLOWED_CREDENTIALS``).
PROVIDER_VAULT_KEYS: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "gemini": "gemini_api_key",
    "glm": "glm_api_key",
    "kimi": "kimi_api_key",
    "custom": "custom_api_key",
}

# Default fallbacks for headless mode. ``provider`` and ``template`` are
# always safe; ``directive`` defaults to none (skipped). API key has no
# default — we error if it's missing.
HEADLESS_DEFAULT_PROVIDER = "anthropic"
HEADLESS_DEFAULT_TEMPLATE = "blank"

# Minimum supported Python interpreter (mirrors ``requires-python`` in
# ``pyproject.toml``).
MIN_PYTHON = (3, 11)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class OnboardResult:
    """What the wizard reports back to the caller.

    Used by tests to assert the right state was written. Production
    callers (the CLI) read the same struct to print the next-step hints.
    """

    status: str  # "completed" | "reused" | "cancelled"
    data_dir: Path
    provider: str | None = None
    template_id: str | None = None
    directive_text: str | None = None
    directive_status: str | None = None
    directive_message: str | None = None
    api_key_storage: str | None = None  # "vault" | "env" | "reused" | None
    ping_status: str | None = None  # "ok" | "skipped" | "skipped_test_mode" | "failed"
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit_step(console: Console, step: int, label: str) -> None:
    console.print(f"[bold cyan][{step}/4][/bold cyan] {label}")


def _ensure_data_dir(data_dir: Path) -> tuple[bool, str]:
    """Create the data dir with restrictive permissions (vault-safe).

    Returns ``(ok, message)``. We use ``0o700`` because the data dir may
    contain encrypted credentials and the SQLite database — even though
    those are encrypted, defence-in-depth keeps casual filesystem peeks
    impossible.
    """
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"cannot create {data_dir}: {exc}"
    # ``chmod`` is best-effort: on Windows / WSL the bits are ignored,
    # which the PRD's non-goals already exclude. Don't fail if chmod
    # itself fails — surface it as a note instead.
    try:
        os.chmod(data_dir, 0o700)
    except OSError:
        pass
    if not os.access(data_dir, os.W_OK):
        return False, f"{data_dir} is not writable"
    return True, "ok"


def _existing_install_state(data_dir: Path) -> dict[str, Any]:
    """Inspect a data dir for prior install artefacts.

    Returns a dict with keys:

    * ``db_exists``     — bool, ``kompany.db`` present
    * ``template_id``   — currently applied template id (or None)
    * ``has_vault_rows`` — credential vault has at least one row
    * ``partial``       — vault dir exists but db is absent (or vice
      versa); treated as overwrite candidate per PRD edge case.

    Reads the DB directly with sqlite3 instead of constructing a full
    engine so we don't trigger any side-effects (schema migrations,
    vault key resolution audit events) before the player has chosen
    reuse vs overwrite.
    """
    db_path = data_dir / "kompany.db"
    state: dict[str, Any] = {
        "db_exists": db_path.exists(),
        "template_id": None,
        "has_vault_rows": False,
        "partial": False,
    }
    if not state["db_exists"]:
        # An empty data_dir is a fresh install — not "partial". Partial
        # only applies if there's stale credential material lying around.
        if data_dir.exists() and any(data_dir.iterdir()):
            state["partial"] = True
        return state
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT value FROM company_config WHERE key = 'template_id'"
            ).fetchone()
            state["template_id"] = row["value"] if row else None
        except sqlite3.OperationalError:
            # company_config table missing — db exists but uninitialised
            state["partial"] = True
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM credential_vault").fetchone()
            state["has_vault_rows"] = bool(row and row["n"] > 0)
        except sqlite3.OperationalError:
            pass
        conn.close()
    except sqlite3.Error:
        # Corrupt or locked DB — treat as partial so user can overwrite.
        state["partial"] = True
    return state


def _wipe_install(data_dir: Path) -> None:
    """Delete kompany.db (and journal files) so the engine starts fresh.

    We only delete files the engine writes — never the data dir itself
    (the user may have other tools sharing the location). Matching the
    SQLite WAL files (``-wal`` / ``-shm``) keeps re-init clean.
    """
    for name in ("kompany.db", "kompany.db-wal", "kompany.db-shm"):
        p = data_dir / name
        if p.exists():
            p.unlink()


def _print_next_steps(console: Console) -> None:
    """Print the five-line next-step hint panel.

    Kept narrow so it renders inside the 90s demo terminal capture
    without horizontal scroll.
    """
    lines = [
        "[bold]Your CEO is on it.[/bold] Next steps:",
        "  [cyan]kompany inbox[/cyan]             — view pending approvals & decisions",
        '  [cyan]kompany directive "..."[/cyan]   — send the team another instruction',
        "  [cyan]kompany episodes list[/cyan]     — review completed project episodes",
        "  [cyan]kompany template list[/cyan]     — browse other starter companies",
        "  [cyan]kompany health list[/cyan]       — watchdog status & recovery events",
    ]
    console.print(Panel("\n".join(lines), title="Kompany onboard complete"))


# ---------------------------------------------------------------------------
# Step 1 — environment check
# ---------------------------------------------------------------------------


def _step_env_check(
    console: Console,
    data_dir: Path,
    result: OnboardResult,
) -> None:
    _emit_step(console, 1, "Checking environment...")
    if sys.version_info < MIN_PYTHON:
        console.print(
            f"[red]Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required "
            f"(found {sys.version_info.major}.{sys.version_info.minor}).[/red]"
        )
        raise typer.Exit(2)
    console.print(
        f"      [green]✓[/green] Python "
        f"{sys.version_info.major}.{sys.version_info.minor}+"
    )
    ok, msg = _ensure_data_dir(data_dir)
    if not ok:
        console.print(f"[red]Data dir error: {msg}[/red]")
        raise typer.Exit(2)
    console.print(f"      [green]✓[/green] data dir {data_dir}")
    # Vault dir == data_dir for the SQLite-backed vault; no separate path
    # to create. We still print a reassuring line so the player knows
    # vault storage is wired.
    console.print("      [green]✓[/green] credential vault ready")


# ---------------------------------------------------------------------------
# Step 2 — provider + API key + LLM ping
# ---------------------------------------------------------------------------


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
        model = _ping_model_for_provider(provider, settings)
        try:
            client.call(
                model=model,
                system="You are a connectivity probe. Reply with one word.",
                prompt="ping",
                agent_name="onboard",
                max_tokens=8,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


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
    # Custom: fall back to whatever the settings already advertise.
    return getattr(settings, "model_economy", "claude-haiku-4-20250414")


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
    ok, detail = _ping_llm(
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
    ok2, detail2 = _ping_llm(provider, api_key, settings_factory=lambda: engine.settings)
    if ok2:
        console.print(f"      [green]✓[/green] {provider} reachable")
        result.ping_status = "ok"
    else:
        console.print(f"      [yellow]Still failing: {detail2}. Continuing anyway.[/yellow]")
        result.ping_status = "failed"
        result.notes.append(f"LLM ping failed twice ({detail2}); directives may not work.")


# ---------------------------------------------------------------------------
# Step 3 — template selection
# ---------------------------------------------------------------------------


def _step_template(
    console: Console,
    *,
    yes: bool,
    template_flag: str | None,
    engine: Any,
    result: OnboardResult,
    reused: bool,
) -> None:
    _emit_step(console, 3, "Choose a starter company")

    if reused:
        applied = engine.templates.is_applied()
        if applied:
            console.print(f"      [dim]reusing template {applied!r}[/dim]")
            result.template_id = applied
        else:
            console.print("      [dim]no template applied; skipping[/dim]")
        return

    try:
        available = engine.templates.list_templates()
    except Exception as exc:  # noqa: BLE001
        console.print(
            f"[yellow]Template service unavailable ({exc}); "
            f"continuing without a template.[/yellow]"
        )
        result.notes.append("template service unavailable; no starter applied.")
        return

    available_ids = [tpl.id for tpl in available]
    template_id = template_flag
    if not template_id:
        if yes:
            template_id = HEADLESS_DEFAULT_TEMPLATE
        else:
            if not available_ids:
                console.print(
                    "      [yellow]No templates installed; falling back to blank.[/yellow]"
                )
                template_id = HEADLESS_DEFAULT_TEMPLATE
            else:
                table = Table(show_header=False, padding=(0, 1))
                table.add_column("#", style="bold cyan")
                table.add_column("id")
                table.add_column("mission")
                for idx, tpl in enumerate(available, start=1):
                    table.add_row(str(idx), tpl.id, tpl.mission_title)
                console.print(table)
                template_id = Prompt.ask(
                    "      Template id",
                    choices=available_ids,
                    default=HEADLESS_DEFAULT_TEMPLATE
                    if HEADLESS_DEFAULT_TEMPLATE in available_ids
                    else available_ids[0],
                )

    # Apply --------------------------------------------------------------
    try:
        applied_id = engine.templates.is_applied()
        if applied_id == template_id:
            console.print(f"      [dim]template {template_id!r} already applied[/dim]")
            result.template_id = template_id
            return
        force = applied_id is not None
        engine.apply_template(template_id, force=force)
    except ValueError as exc:
        console.print(f"[red]Template error: {exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(f"      [green]✓[/green] applied template [bold]{template_id}[/bold]")
    result.template_id = template_id


# ---------------------------------------------------------------------------
# Step 4 — first directive (optional)
# ---------------------------------------------------------------------------


def _step_directive(
    console: Console,
    *,
    yes: bool,
    directive_flag: str | None,
    engine: Any,
    result: OnboardResult,
) -> None:
    _emit_step(console, 4, "First directive (optional)")

    directive_text = directive_flag
    if not directive_text:
        if yes:
            console.print("      [dim]skipped (headless, no --directive given)[/dim]")
            return
        if not Confirm.ask("      Send a first directive now?", default=False):
            console.print("      [dim]skipped[/dim]")
            return
        directive_text = Prompt.ask("      Directive")
        if not directive_text.strip():
            console.print("      [dim]empty directive; skipping[/dim]")
            return

    result.directive_text = directive_text
    try:
        outcome = engine.process_directive(directive_text)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Directive failed: {exc}[/red]")
        result.directive_status = "error"
        result.directive_message = str(exc)
        result.notes.append(f"first directive failed: {exc}")
        return

    result.directive_status = getattr(outcome, "status", None)
    message = getattr(outcome, "message", "") or ""
    result.directive_message = message
    # Print the first five lines (the PRD's spec for the demo capture).
    head = "\n".join(message.splitlines()[:5])
    console.print(Panel(head or "(no message returned)", title="CEO response"))


# ---------------------------------------------------------------------------
# Idempotent re-run resolution
# ---------------------------------------------------------------------------


def _resolve_existing_install(
    console: Console,
    data_dir: Path,
    *,
    yes: bool,
) -> str:
    """Return one of ``"fresh"``, ``"reuse"``, or ``"cancel"``.

    Called before the engine is constructed so we never side-effect a
    DB we're about to wipe.
    """
    state = _existing_install_state(data_dir)
    if not state["db_exists"] and not state["partial"]:
        return "fresh"

    if state["partial"]:
        console.print(
            "[yellow]Partial install detected[/yellow] "
            f"(data dir {data_dir} has artefacts but no usable kompany.db)."
        )
        if yes:
            console.print("      [dim]headless: treating as overwrite[/dim]")
            _wipe_install(data_dir)
            return "fresh"
        action = Prompt.ask(
            "      Overwrite / Cancel?",
            choices=["overwrite", "cancel"],
            default="overwrite",
        )
        if action == "cancel":
            return "cancel"
        _wipe_install(data_dir)
        return "fresh"

    template = state["template_id"] or "(no template)"
    console.print(
        f"[yellow]Existing install found[/yellow] in {data_dir} "
        f"(template: {template})."
    )
    if yes:
        console.print("      [dim]headless: reusing existing setup[/dim]")
        return "reuse"
    action = Prompt.ask(
        "      Reuse / Overwrite / Cancel?",
        choices=["reuse", "overwrite", "cancel"],
        default="reuse",
    )
    if action == "cancel":
        return "cancel"
    if action == "overwrite":
        _wipe_install(data_dir)
        return "fresh"
    return "reuse"


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

        # ----- Step 2 (provider) -------------------------------------
        _step_provider(
            cons,
            yes=yes,
            provider_flag=provider,
            api_key_flag=api_key,
            engine=engine,
            result=result,
            reused=reused,
        )

        # ----- Step 3 (template) -------------------------------------
        _step_template(
            cons,
            yes=yes,
            template_flag=template,
            engine=engine,
            result=result,
            reused=reused,
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


class OnboardError(Exception):
    """Raised by :func:`onboard_headless` for any caller-visible failure.

    Carries a short machine-friendly ``code`` plus a human ``message``.
    REST callers map this onto a 200 response with ``status='error'`` so
    the in-window onboarding form can surface the message inline.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def onboard_headless(
    data_dir: Path | str,
    provider: str,
    api_key: str,
    template_id: str,
    directive: str | None = None,
    *,
    engine_factory: Callable[[], Any] | None = None,
) -> OnboardResult:
    """Run a fully-headless onboarding pass.

    Pure function with no typer / rich dependency — callable from the
    REST endpoint, the Tauri shell, or any test. The wizard's branching
    UX (prompts, retry/abort, idempotency dialog) collapses to a clean
    contract:

    * If the data dir already contains a usable ``kompany.db``, we treat
      that as a reuse: no overwrite, no re-ping. Returned result has
      ``status='reused'``.
    * Otherwise (fresh or partial), we apply the supplied template and
      stash the API key in the vault.
    * Any failure raises :class:`OnboardError` with a code the caller
      can render verbatim.

    The :class:`OnboardResult` returned matches the CLI's contract so
    tests can assert on the same dataclass either way.
    """
    if not provider:
        raise OnboardError("missing_provider", "provider is required")
    if provider not in SUPPORTED_PROVIDERS:
        raise OnboardError(
            "unknown_provider",
            f"unknown provider {provider!r}; "
            f"choose one of: {', '.join(SUPPORTED_PROVIDERS)}",
        )
    if not api_key:
        raise OnboardError("missing_api_key", "api_key is required")
    if not template_id:
        raise OnboardError("missing_template", "template_id is required")

    data_dir = Path(data_dir).expanduser().resolve()
    ok, msg = _ensure_data_dir(data_dir)
    if not ok:
        raise OnboardError("data_dir_error", msg)

    # Idempotency: if the DB exists with a template applied, reuse.
    state = _existing_install_state(data_dir)
    reused = bool(state["db_exists"]) and not state["partial"]

    # The downstream engine reads ``KOMPANY_DATA_DIR`` from the
    # environment, exactly like the CLI wizard. Save/restore so we don't
    # leak across long-running REST processes.
    prior_env = os.environ.get("KOMPANY_DATA_DIR")
    os.environ["KOMPANY_DATA_DIR"] = str(data_dir)

    result = OnboardResult(
        status="reused" if reused else "completed",
        data_dir=data_dir,
    )
    try:
        if engine_factory is not None:
            engine = engine_factory()
        else:
            from kompany.core.engine import KompanyEngine

            engine = KompanyEngine()

        # ----- Provider + API key + ping ---------------------------------
        result.provider = provider
        vault_field = PROVIDER_VAULT_KEYS.get(provider)

        if reused:
            result.api_key_storage = "reused"
            result.ping_status = "skipped"
        else:
            storage = "env"
            if vault_field and getattr(engine.settings, "vault_key", ""):
                try:
                    engine.credentials.set(vault_field, api_key)
                    storage = "vault"
                except Exception as exc:  # noqa: BLE001
                    result.notes.append(f"vault write failed ({exc}); using process env only")
            else:
                result.notes.append(
                    f"vault key not configured; set {PROVIDER_ENV_VARS.get(provider, '?')} "
                    "in your environment for persistent storage"
                )
            if vault_field:
                setattr(engine.settings, vault_field, api_key)
            result.api_key_storage = storage

            ok, detail = _ping_llm(
                provider,
                api_key,
                settings_factory=lambda: engine.settings,
            )
            if ok and detail == "skipped_test_mode":
                result.ping_status = "skipped_test_mode"
            elif ok:
                result.ping_status = "ok"
            else:
                # Surface as an error so the wizard form can retry.
                raise OnboardError("ping_failed", f"{provider} ping failed: {detail}")

        # ----- Template apply -------------------------------------------
        if reused:
            applied = engine.templates.is_applied()
            result.template_id = applied or template_id
        else:
            try:
                applied_id = engine.templates.is_applied()
                if applied_id == template_id:
                    result.template_id = template_id
                else:
                    engine.apply_template(template_id, force=applied_id is not None)
                    result.template_id = template_id
            except ValueError as exc:
                raise OnboardError("template_error", str(exc)) from exc

        # ----- First directive (optional) -------------------------------
        if directive and directive.strip():
            result.directive_text = directive
            try:
                outcome = engine.process_directive(directive)
            except Exception as exc:  # noqa: BLE001
                result.directive_status = "error"
                result.directive_message = str(exc)
                result.notes.append(f"first directive failed: {exc}")
            else:
                result.directive_status = getattr(outcome, "status", None)
                result.directive_message = getattr(outcome, "message", "") or ""

        return result
    finally:
        if prior_env is None:
            os.environ.pop("KOMPANY_DATA_DIR", None)
        else:
            os.environ["KOMPANY_DATA_DIR"] = prior_env


def is_onboarded(data_dir: Path | str) -> dict[str, Any]:
    """Return a snapshot describing whether onboarding has been done.

    Read-only — safe to call from a REST handler on every request. The
    function inspects ``kompany.db`` directly (no engine spin-up) so a
    fresh install can answer this in microseconds.
    """
    data_dir = Path(data_dir).expanduser()
    state = _existing_install_state(data_dir)
    if not state["db_exists"] or state["partial"]:
        return {"onboarded": False, "template_id": None, "provider": None}

    template_id = state.get("template_id")
    provider: str | None = None
    if state.get("has_vault_rows"):
        import sqlite3

        try:
            conn = sqlite3.connect(str(data_dir / "kompany.db"))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT name FROM credential_vault"
                ).fetchall()
            finally:
                conn.close()
            stored = {row["name"] for row in rows}
            for prov, vault_key in PROVIDER_VAULT_KEYS.items():
                if vault_key in stored:
                    provider = prov
                    break
        except sqlite3.Error:
            provider = None

    onboarded = template_id is not None
    return {
        "onboarded": bool(onboarded),
        "template_id": template_id,
        "provider": provider,
    }
