"""Sidecar discovery + MCP tool-call proxy client (stdlib HTTP only).

When the desktop app is running, its FastAPI sidecar publishes a
discovery file ``<data_dir>/server.json`` (written by ``sidecar_main``
once uvicorn is listening, removed on shutdown). ``kompany-mcp`` checks
it on every tool call and, when the sidecar is alive, forwards the call
to ``POST /mcp/tool`` so the work runs inside the sidecar's single
engine — the app panel then receives live SSE events and every euro
books in one cost ledger (no second in-process engine, no
double-billing heartbeats).

Remote mode (07-14 cloud-deploy): when ``KOMPANY_REMOTE_URL`` is set
(e.g. ``http://100.125.151.40:37895`` over Tailscale), the proxy
forwards all tool calls to that remote engine instead of looking for a
local sidecar. The remote URL is validated with a ``GET /health`` probe
on each call (cached ~5s). This lets a laptop CLI/MCP client drive the
VPS engine without running a local engine.

Readers never trust the discovery file blindly: a sidecar can crash
without cleanup and the OS can recycle its port for another process.
Validation = pid liveness + ``GET /health`` probe with response-shape
check; the verdict is cached for ~5 seconds per data_dir.

Dev note: running ``uvicorn kompany.interfaces.api:app`` (or ``kompany
serve``) directly does not write a discovery file — only the sidecar
entry point does.

Stdlib only by design (``urllib.request``): this module ships inside
the ``kompany-mcp`` install which may not have the ``[api]`` extras.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DISCOVERY_FILENAME = "server.json"
HEALTH_PROBE_TIMEOUT_SECONDS = 0.5
# kompany_execute can legitimately run for many minutes; uvicorn does
# not time out handlers, so the client side carries the generous cap.
PROXY_CALL_TIMEOUT_SECONDS = 1800.0
DISCOVERY_CACHE_TTL_SECONDS = 5.0
# Remote engine URL env var (07-14 cloud-deploy). When set, the MCP
# proxy forwards all tool calls to this URL instead of a local sidecar.
REMOTE_URL_ENV = "KOMPANY_REMOTE_URL"


class SidecarProxyError(RuntimeError):
    """A proxied tool call failed (transport error or sidecar-side error)."""


def default_data_dir() -> Path:
    """Resolve the same data_dir the in-process engine would use.

    ``KompanyEngine()`` resolves env ``KOMPANY_DATA_DIR`` > active
    workspace (issue #15 registry) > ``~/.kompany`` — same chain here so
    the MCP process and the sidecar agree on where ``server.json``
    lives, including right after a workspace switch.
    """
    from kompany.config.workspaces import resolve_data_dir

    return resolve_data_dir()


def discovery_path(data_dir: Path | None = None) -> Path:
    return (data_dir if data_dir is not None else default_data_dir()) / DISCOVERY_FILENAME


def write_discovery_file(
    port: int,
    data_dir: Path | None = None,
    source: str = "sidecar",
) -> Path | None:
    """Best-effort: publish the sidecar discovery file. Returns the path or None.

    ``source`` labels who owns the server process (``"sidecar"`` when
    spawned by the Tauri shell, ``"daemon"`` for ``kompany daemon run``)
    so status surfaces can report it. Readers must tolerate its absence
    (files written before 06-12 don't carry it).
    """
    path = discovery_path(data_dir)
    payload = {
        "port": int(port),
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return None
    return path


def remove_discovery_file(data_dir: Path | None = None) -> None:
    """Best-effort cleanup on sidecar shutdown; stale files are validated anyway."""
    try:
        discovery_path(data_dir).unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Discovery (reader side — the MCP server process).
# ---------------------------------------------------------------------------

# data_dir-keyed (path -> (expires_monotonic, verdict)) so tests with
# distinct tmp dirs never poison each other.
_verdict_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def reset_discovery_cache() -> None:
    """Drop cached verdicts (tests, or after a known sidecar restart)."""
    _verdict_cache.clear()


def discover_sidecar(data_dir: Path | None = None) -> dict[str, Any] | None:
    """Return validated sidecar info (``{"port", "pid", ...}``) or None.

    Remote mode (07-14): when ``KOMPANY_REMOTE_URL`` is set, returns a
    synthetic discovery dict ``{"url": "...", "source": "remote"}``
    after a successful health probe. The remote URL takes precedence
    over local sidecar discovery — the operator's intent when they set
    it is "use the VPS engine, not a local one."

    None means: no remote URL, no discovery file, malformed file, the
    sidecar pid is dead, the health probe failed/answered with a
    foreign shape, or the file points at *this* process (self-proxy
    guard).
    """
    # Remote URL takes precedence over local discovery.
    remote_url = os.environ.get(REMOTE_URL_ENV, "").strip()
    if remote_url:
        cache_key = f"remote:{remote_url}"
        now = time.monotonic()
        cached = _verdict_cache.get(cache_key)
        if cached is not None and now < cached[0]:
            return cached[1]
        verdict = _validate_remote(remote_url)
        _verdict_cache[cache_key] = (now + DISCOVERY_CACHE_TTL_SECONDS, verdict)
        return verdict

    path = discovery_path(data_dir)
    key = str(path)
    now = time.monotonic()
    cached = _verdict_cache.get(key)
    if cached is not None and now < cached[0]:
        return cached[1]
    verdict = _validate_sidecar(path)
    _verdict_cache[key] = (now + DISCOVERY_CACHE_TTL_SECONDS, verdict)
    return verdict


def _validate_sidecar(path: Path) -> dict[str, Any] | None:
    info = _read_discovery_file(path)
    if info is None:
        return None
    if info["pid"] == os.getpid():
        # Self-proxy guard: we ARE the sidecar process — dispatch
        # in-process instead of calling our own (blocked) event loop.
        return None
    if not _pid_alive(info["pid"]):
        return None
    if not _probe_health(info["port"]):
        return None
    return info


def _read_discovery_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    port = raw.get("port")
    pid = raw.get("pid")
    if isinstance(port, bool) or not isinstance(port, int):
        return None
    if isinstance(pid, bool) or not isinstance(pid, int):
        return None
    if not (0 < port < 65536) or pid <= 0:
        return None
    return raw


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, owned by another user
    except OSError:
        return False
    return True


def _probe_health(port: int) -> bool:
    """GET /health and validate the kompany response shape.

    A recycled port serving some other local app must not be mistaken
    for the sidecar — we require exactly ``{"status": "ok"}`` semantics.
    """
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=HEALTH_PROBE_TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                return False
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return isinstance(body, dict) and body.get("status") == "ok"


def _validate_remote(remote_url: str) -> dict[str, Any] | None:
    """Validate a remote engine URL via health probe.

    Returns a synthetic discovery dict ``{"url": ..., "source": "remote"}``
    when the remote is alive, None otherwise. No pid check — the remote
    engine is on another machine.
    """
    base = remote_url.rstrip("/")
    try:
        with urllib.request.urlopen(
            f"{base}/health", timeout=HEALTH_PROBE_TIMEOUT_SECONDS
        ) as resp:
            if resp.status != 200:
                return None
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not (isinstance(body, dict) and body.get("status") == "ok"):
        return None
    return {"url": base, "source": "remote"}


# ---------------------------------------------------------------------------
# Proxy client.
# ---------------------------------------------------------------------------


def proxy_tool_call(
    port: int,
    name: str,
    arguments: dict[str, Any],
    timeout: float = PROXY_CALL_TIMEOUT_SECONDS,
    *,
    base_url: str | None = None,
) -> Any:
    """POST one tool call to the sidecar bridge and return its payload.

    When ``base_url`` is set (remote mode), the call goes to
    ``{base_url}/mcp/tool`` instead of ``http://127.0.0.1:{port}/mcp/tool``.

    Raises :class:`SidecarProxyError` on any failure. Callers must NOT
    fall back to in-process execution on error — the sidecar may have
    started real work before the connection died (double-execution
    risk); surface the error instead.
    """
    body = json.dumps({"name": name, "arguments": arguments}).encode("utf-8")
    target = f"{base_url}/mcp/tool" if base_url else f"http://127.0.0.1:{port}/mcp/tool"
    headers = {"Content-Type": "application/json"}
    # The sidecar's api_guard token-gates /mcp/tool when a dashboard token is
    # configured; present it (env override first, then settings).
    token = _proxy_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(target, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SidecarProxyError(_http_error_message(exc)) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SidecarProxyError(str(exc)) from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        message = "sidecar returned a malformed /mcp/tool response"
        if isinstance(payload, dict) and payload.get("error"):
            message = str(payload["error"])
        raise SidecarProxyError(message)
    return payload.get("result")


def _proxy_token() -> str:
    """Bearer token for the sidecar bridge: ``KOMPANY_REMOTE_TOKEN`` /
    ``WEB_DASHBOARD_TOKEN`` env, else the configured ``web_dashboard_token``."""
    for name in ("KOMPANY_REMOTE_TOKEN", "WEB_DASHBOARD_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        from kompany.config.settings import KompanySettings

        return str(KompanySettings.load(None).web_dashboard_token or "")
    except Exception:  # noqa: BLE001 — no settings yet: send no token
        return ""


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    """Prefer the bridge's structured ``{"ok": false, "error": ...}`` body."""
    try:
        detail = json.loads(exc.read().decode("utf-8"))
    except (OSError, ValueError):
        detail = None
    if isinstance(detail, dict):
        message = detail.get("error") or detail.get("detail")
        if message:
            return f"HTTP {exc.code}: {message}"
    return f"HTTP {exc.code}: {exc.reason}"
