"""OAuth-subscription login commands (06-16-agentic-chat-engine P3).

`kompany auth openai`  — interactive ChatGPT/Codex PKCE login (browser
                         flow → encrypted token sink in the credential vault).
`kompany auth status`  — show whether an OAuth login exists + its expiry.
`kompany auth logout`  — clear the stored OAuth tokens.

The browser/callback/network bits live in :mod:`kompany.llm.oauth`; this
module is the founder entry point only. The LIVE flow needs a real founder
login — it can't be auto-tested (the unit tests exercise the flow with
injected fakes).
"""

from __future__ import annotations

import time

import typer
from rich.panel import Panel

from kompany.interfaces.cli_parts.common import (
    app,
    console,
    _get_engine,
    _emit_json,
)
from kompany.llm.oauth import OAuthTokenStore
from kompany.llm.oauth.openai_pkce import (
    OAuthLoginError,
    OpenAIOAuthConfig,
    OpenAIPKCEClient,
)

auth_app = typer.Typer(
    name="auth",
    help="Log in to an LLM subscription (OAuth) so the native loop can run on it.",
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")


def _token_store(config: str | None) -> OAuthTokenStore:
    """Build an OAuth token store backed by the engine's credential vault."""
    engine = _get_engine(config)
    vault = engine.credentials
    pkce = OpenAIPKCEClient(config=OpenAIOAuthConfig.from_env())
    return OAuthTokenStore(vault, pkce_client=pkce)


@auth_app.command("openai")
def auth_openai(
    config: str = typer.Option(None, "--config", "-c"),
    timeout: float = typer.Option(
        300.0, "--timeout", help="Seconds to wait for the browser callback."
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable JSON"),
):
    """Run the interactive ChatGPT/Codex OAuth (PKCE) login.

    Opens your browser, captures the redirect on 127.0.0.1:1455, exchanges
    the code, and stores the tokens encrypted in the credential vault. After
    this, set the model source to ``openai_oauth_subscription`` to run the
    native loop on your ChatGPT subscription.
    """
    store = _token_store(config)
    console.print(
        "[dim]Opening your browser to authorize Kompany with ChatGPT/Codex…\n"
        "If it doesn't open, copy the printed URL into a browser.[/dim]"
    )
    try:
        tokens = store.pkce_client.login_interactive(timeout=timeout)
    except OAuthLoginError as exc:
        if as_json:
            _emit_json({"ok": False, "error": str(exc)})
        else:
            console.print(f"[red]Login failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    store.save(tokens)
    status = store.status()
    if as_json:
        _emit_json({"ok": True, "status": status})
        return
    console.print(
        Panel(
            "\n".join([
                "Logged in to ChatGPT/Codex (OAuth subscription).",
                f"Account: {tokens.account_id or '—'}",
                f"Expires: {_fmt_expiry(tokens.expires_at)}",
                "",
                "Next: `kompany model-source set --kind "
                "openai_oauth_subscription --monthly-fee <USD>`",
            ]),
            title="kompany auth openai",
        )
    )


@auth_app.command("status")
def auth_status(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable JSON"),
):
    """Show whether a ChatGPT/Codex OAuth login is stored and its expiry."""
    store = _token_store(config)
    status = store.status()
    if as_json:
        _emit_json(status)
        return
    if not status.get("logged_in"):
        console.print(
            "[dim]Not logged in to ChatGPT/Codex. "
            "Run `kompany auth openai`.[/dim]"
        )
        return
    lines = [
        "Logged in: yes",
        f"Account: {status.get('account_id') or '—'}",
        f"Expired: {'yes' if status.get('expired') else 'no'}",
        f"Expires: {_fmt_expiry(status.get('expires_at'))}",
        f"Refresh token: {'yes' if status.get('has_refresh_token') else 'no'}",
    ]
    console.print(Panel("\n".join(lines), title="ChatGPT/Codex OAuth"))


@auth_app.command("logout")
def auth_logout(
    config: str = typer.Option(None, "--config", "-c"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable JSON"),
):
    """Clear the stored ChatGPT/Codex OAuth tokens."""
    store = _token_store(config)
    store.clear()
    if as_json:
        _emit_json({"ok": True, "logged_in": False})
        return
    console.print("[green]Logged out — ChatGPT/Codex OAuth tokens cleared.[/green]")


def _fmt_expiry(expires_at: float | None) -> str:
    if not expires_at:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expires_at))
