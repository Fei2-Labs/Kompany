"""Shared onboarding constants, result types, and console helpers.

Split out of ``onboard.py`` (ADR-0003). All names are re-exported from
``kompany.installer.onboard`` — tests and the REST layer import (and
monkeypatch) them there, so always patch on the ``onboard`` module.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt


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
    # Mission-targets task (05-19). ``targets_review_id`` is the
    # approval_request id created by ``engine.run_target_feasibility_review``
    # after onboarding completes, so callers (CLI, REST) can deep-link to
    # the team's recommendation.
    targets_review_id: str | None = None
    # ModelSource written during onboarding (06-11-harness-execution-leg
    # PR5b): "custom_api" for the API-key path, a *_subscription kind
    # when the founder picked a detected zero-key CLI. ``None`` when the
    # source could not be written (stub engine / reuse path).
    model_source_kind: str | None = None


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

    # A DB that exists but never had a template applied is an aborted
    # onboarding, not a reusable install. Treat as partial so the
    # resolve step prompts overwrite (or auto-overwrites under --yes)
    # rather than silently skipping template apply + feasibility review.
    # This guards against the incident that surfaced this bug: an
    # empty-config DB was treated
    # as "reused", and the frontend then misreported the missing
    # feasibility review as "blank template or quota error".
    if state["db_exists"] and state["template_id"] is None and not state["partial"]:
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

