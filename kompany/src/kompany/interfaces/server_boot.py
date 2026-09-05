"""Shared headless server boot for the sidecar and the daemon CLI.

Extracted from ``sidecar_main.py`` (06-12-daemon-tick-loop PR2) so the
Tauri sidecar entry point and ``kompany daemon run`` boot the exact
same uvicorn + discovery-file path instead of duplicating it:

* ``--port 0`` resolution — once uvicorn binds, the real port is read
  off the bound socket and published.
* Discovery publish — ``<data_dir>/server.json`` is written by a
  watcher thread (uvicorn binds the socket *after* the app's startup
  event fires, so only the bound socket knows the real port) and
  removed in a ``finally`` on shutdown. The discovery file is the
  single-server lock (PRD D2): MCP proxy, panel SSE, daemon status and
  ``kompany daemon run``'s refuse-when-running all read it.
* Ready-file — optional tiny signal file the Rust shell can stat
  instead of polling ``/health``.

Keep this module dependency-light at import time: PyInstaller's onedir
sweep follows direct imports, and the engine must only load inside the
uvicorn worker after ``KOMPANY_DATA_DIR`` is wired.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable


def write_ready_file(path: Path, port: int) -> None:
    """Drop a tiny signal file the host shell can stat instead of poll.

    Best-effort: if the path isn't writable we just skip — the Rust
    side falls back to HTTP health polling.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"ready {port}\n", encoding="utf-8")
    except OSError:
        pass


def run_server(
    host: str = "127.0.0.1",
    port: int = 0,
    data_dir_override: str | None = None,
    ready_file: str | None = None,
    *,
    source: str = "sidecar",
    server_factory: Callable[..., Any] | None = None,
) -> int:
    """Boot the Kompany FastAPI server and block until shutdown.

    ``source`` labels the discovery file (``"sidecar"`` | ``"daemon"``)
    so ``kompany daemon status`` can report who owns the server.
    ``server_factory`` is an injectable ``uvicorn.Server`` substitute
    for tests — production always uses the real one.
    """
    if data_dir_override:
        os.environ["KOMPANY_DATA_DIR"] = str(Path(data_dir_override).expanduser())

    # Defer the imports until after env wiring so any settings loader
    # inside the FastAPI app (and the discovery-file data_dir
    # resolution) picks up the KOMPANY_DATA_DIR override on first
    # access.
    import threading
    import time

    import uvicorn

    from kompany.config.settings import KompanySettings
    from kompany.interfaces.api_guard import assert_bind_allowed

    # Never expose an unauthenticated API beyond loopback by accident.
    assert_bind_allowed(host, KompanySettings.load(None))
    # Let the access guard default its Host allowlist to the bound address
    # (#45); explicit KOMPANY_ALLOWED_HOSTS still wins.
    os.environ.setdefault("KOMPANY_BIND_HOST", host)

    from kompany.interfaces.mcp_proxy import (
        remove_discovery_file,
        write_discovery_file,
    )

    config = uvicorn.Config(
        "kompany.interfaces.api:app",
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        # Single-worker model — exactly one engine per machine and we
        # never want PyInstaller to fork.
        workers=1,
        loop="asyncio",
    )
    server = (server_factory or uvicorn.Server)(config)

    # MCP sidecar discovery (task 06-10-mcp-sidecar-proxy): once uvicorn
    # reports started, publish <data_dir>/server.json so kompany-mcp
    # proxies tool calls into this process instead of constructing a
    # second engine. A watcher thread is required because uvicorn binds
    # the socket *after* the app's startup event fires, and with
    # ``--port 0`` only the bound socket knows the real port.
    def _publish_discovery() -> None:
        while not server.started:
            if server.should_exit:
                return
            time.sleep(0.05)
        try:
            bound_port = server.servers[0].sockets[0].getsockname()[1]
        except (AttributeError, IndexError, OSError):
            bound_port = port
        write_discovery_file(bound_port, source=source)

    threading.Thread(
        target=_publish_discovery,
        name="kompany-discovery",
        daemon=True,
    ).start()

    # Drop the ready-file right after startup. Uvicorn's startup hook
    # is the cleanest seam without monkey-patching a FastAPI lifespan
    # from here; the host shell only sees the file once the server has
    # actually started listening (since we block on .run()).
    if ready_file:
        from kompany.interfaces.api import app as _app

        @_app.on_event("startup")
        async def _signal_ready() -> None:  # pragma: no cover — exercised in real boot
            write_ready_file(Path(ready_file), port)

    try:
        server.run()
    finally:
        # Best-effort: a crash leaves a stale server.json behind, which
        # readers detect via the health probe + pid check anyway.
        remove_discovery_file()
    return 0


__all__ = ["run_server", "write_ready_file"]
