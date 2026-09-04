"""API access guard — origin (CSRF) check, token gate, public-bind refusal.

Security posture before this module: only ``/dashboard/*`` checked the
``web_dashboard_token``; every other route (credentials, directives, tool
proposals, remote commands, SSE) was open to whoever could reach the socket.
On loopback that still meant any website the founder visits could POST
cross-site (a ``text/plain`` body needs no CORS preflight), and a
``--host 0.0.0.0`` deployment was fully open.

Three layers, all in one ASGI middleware:

1. **Origin guard (always on).** A request carrying a browser ``Origin``
   header must come from the server's own origin, a configured
   ``KOMPANY_CORS_ORIGINS`` entry, or the Tauri WebView origin. Anything
   else is refused with 403 — this is what stops cross-site requests from
   a malicious page against the loopback API.
2. **Token gate (on when ``web_dashboard_token`` is configured).** Every
   route except the exempt list must present the token as
   ``Authorization: Bearer``, ``?token=``, or the ``kompany_dashboard_session``
   cookie issued by ``/dashboard/login``. Browsers asking for HTML are
   redirected to the login page; API clients get 401. Routes with their
   own authentication (``/remote/*`` bearer / Telegram allowlist,
   ``/intake`` token) and ``/health`` stay reachable.
3. **Public-bind refusal.** :func:`assert_bind_allowed` refuses to bind a
   non-loopback address without a configured token unless the operator
   sets ``KOMPANY_ALLOW_OPEN_BIND=1`` explicitly.

Optional: ``KOMPANY_ALLOWED_HOSTS`` (comma list) enables a Host-header
allowlist against DNS rebinding for exposed deployments.
"""

from __future__ import annotations

import os
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "0:0:0:0:0:0:0:1"})

# Routes that carry their own authentication or must stay reachable so the
# founder can log in / the supervisor can probe liveness.
TOKEN_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/health",
    "/dashboard",  # every /dashboard* handler runs _dashboard_auth_error itself
    "/remote/",  # bearer / Telegram allowlist inside engine.handle_remote_command
    "/intake",  # intake_token checked by the handler
    "/onboarding/ping",  # liveness probe used by the installer shell
)

# Tauri WebView origins (macOS / Linux use the custom scheme, Windows the
# http alias). The desktop shell loads the board from the sidecar URL, so in
# practice its Origin matches Host — these are a safety net.
_TAURI_ORIGINS: frozenset[str] = frozenset({"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"})


def _cors_origins() -> set[str]:
    raw = os.environ.get("KOMPANY_CORS_ORIGINS", "")
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


def _allowed_hosts() -> set[str]:
    raw = os.environ.get("KOMPANY_ALLOWED_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _host_only(netloc: str) -> str:
    """``example.com:8000`` -> ``example.com``; keeps IPv6 brackets."""
    netloc = netloc.strip().lower()
    if netloc.startswith("["):
        return netloc.split("]")[0] + "]"
    return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc


def is_loopback_host(host: str) -> bool:
    return _host_only(host) in LOOPBACK_HOSTS


def origin_allowed(origin: str, host_header: str) -> bool:
    """Same-origin (by Host header), configured CORS origin, or Tauri shell."""
    origin = (origin or "").strip().rstrip("/")
    if not origin or origin == "null":
        return False
    if origin in _cors_origins() or origin in _TAURI_ORIGINS:
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    if parts.netloc.lower() == (host_header or "").strip().lower():
        return True
    # Same loopback machine on a different port is still "us" (dev server,
    # docs). Any non-loopback mismatch is cross-site.
    return is_loopback_host(parts.netloc) and is_loopback_host(host_header or "")


def _is_exempt(path: str) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p) for p in TOKEN_EXEMPT_PREFIXES)


def _supplied_ok(request: Request, expected: str) -> bool:
    from kompany.interfaces.api_parts.dashboard import _dashboard_auth_error

    settings = _settings()
    return _dashboard_auth_error(
        settings, request.headers.get("authorization"), request.query_params.get("token", ""), request
    ) is None


def _settings() -> Any:
    from kompany.interfaces import api

    try:
        return api.get_engine().settings
    except Exception:  # noqa: BLE001 — no engine yet (first boot): gate on env only
        return None


def _configured_token(settings: Any) -> str:
    if settings is None:
        return os.environ.get("WEB_DASHBOARD_TOKEN", "") or ""
    return str(getattr(settings, "web_dashboard_token", "") or "")


def _wants_html(request: Request) -> bool:
    return request.method == "GET" and "text/html" in (request.headers.get("accept") or "")


class ApiAccessGuard:
    """Pure ASGI middleware (keeps SSE streaming untouched)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        denial = self.check(request)
        if denial is not None:
            await denial(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def check(self, request: Request) -> Response | None:
        host_header = request.headers.get("host", "")
        allowed_hosts = _allowed_hosts()
        if allowed_hosts and _host_only(host_header) not in allowed_hosts and not is_loopback_host(host_header):
            return JSONResponse({"detail": "host not allowed"}, status_code=421)
        origin = request.headers.get("origin")
        if origin and not origin_allowed(origin, host_header):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        if request.method == "OPTIONS":
            return None  # CORS preflight — CORSMiddleware answers or 405s
        path = request.url.path
        if _is_exempt(path):
            return None
        settings = _settings()
        expected = _configured_token(settings)
        if not expected:
            return None  # local, unconfigured mode: loopback + origin guard only
        if _supplied_ok(request, expected):
            return None
        if _wants_html(request):
            return RedirectResponse("/dashboard/login", status_code=303)
        return JSONResponse(
            {"detail": "authentication required: dashboard token (Bearer / ?token= / login cookie)"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )


def assert_bind_allowed(host: str, settings: Any = None) -> None:
    """Refuse a non-loopback bind without a token (constitution: no open door).

    Override with ``KOMPANY_ALLOW_OPEN_BIND=1`` (e.g. behind an authenticating
    reverse proxy) — the choice is then explicit and auditable.
    """
    if is_loopback_host(host):
        return
    if _configured_token(settings):
        return
    if os.environ.get("KOMPANY_ALLOW_OPEN_BIND", "").strip() == "1":
        return
    raise SystemExit(
        f"Refusing to bind {host!r} without authentication. Set WEB_DASHBOARD_TOKEN "
        "(or web_dashboard_token in config.yaml) so every route requires the token, "
        "or set KOMPANY_ALLOW_OPEN_BIND=1 to accept an open API on purpose."
    )


__all__ = ["ApiAccessGuard", "assert_bind_allowed", "is_loopback_host", "origin_allowed"]
