"""Tauri-sidecar entry point for the Kompany FastAPI backend.

Bundled with PyInstaller --onedir; the resulting binary
``kompany-server-<target-triple>`` is spawned by the Rust shell with::

    kompany-server --port <random> --data-dir <app_data_dir>

The Rust side picks the port, hands us a writable data directory, and
polls ``/health`` until the uvicorn server accepts connections. Once
healthy, the Tauri WebView opens against ``http://127.0.0.1:<port>/ui/``.

Keep this file dependency-light: PyInstaller's ``--onedir`` sweep
follows direct imports, so anything imported here at module load time
gets bundled. Lazy-import everything heavy (engine, etc.) inside the
uvicorn worker so cold-start stays fast.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kompany-server",
        description="Kompany REST + UI sidecar for the Tauri shell.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="TCP port to bind (0 = let the OS pick).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (loopback only by default).",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="Override the Kompany data directory (KOMPANY_DATA_DIR).",
    )
    parser.add_argument(
        "--ready-file",
        default="",
        help="Optional path; write 'ready' here once the server is up.",
    )
    return parser.parse_args(argv)


def _write_ready_file(path: Path, port: int) -> None:
    """Drop a tiny signal file the Rust shell can stat instead of poll.

    Best-effort: if the path isn't writable we just skip — the Rust
    side falls back to HTTP health polling.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"ready {port}\n", encoding="utf-8")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.data_dir:
        os.environ["KOMPANY_DATA_DIR"] = str(Path(args.data_dir).expanduser())

    # Defer the imports until after env wiring so any settings loader
    # inside the FastAPI app (and the discovery-file data_dir
    # resolution) picks up the KOMPANY_DATA_DIR override on first
    # access.
    import threading
    import time

    import uvicorn

    from kompany.interfaces.mcp_proxy import (
        remove_discovery_file,
        write_discovery_file,
    )

    config = uvicorn.Config(
        "kompany.interfaces.api:app",
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
        # Single-worker model — the Tauri shell hosts exactly one
        # player at a time and we never want PyInstaller to fork.
        workers=1,
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    # MCP sidecar discovery (task 06-10-mcp-sidecar-proxy): once uvicorn
    # reports started, publish <data_dir>/server.json so kompany-mcp
    # proxies tool calls into this process instead of constructing a
    # second engine. A watcher thread is required because uvicorn binds
    # the socket *after* the app's startup event fires, and with
    # ``--port 0`` only the bound socket knows the real port.
    def _publish_discovery() -> None:  # pragma: no cover — real-boot seam
        while not server.started:
            if server.should_exit:
                return
            time.sleep(0.05)
        try:
            port = server.servers[0].sockets[0].getsockname()[1]
        except (AttributeError, IndexError, OSError):
            port = args.port
        write_discovery_file(port)

    threading.Thread(
        target=_publish_discovery,
        name="kompany-discovery",
        daemon=True,
    ).start()

    # Run the server in the current thread and drop the ready-file
    # right after startup. Uvicorn's startup hook is the cleanest seam
    # but we don't want to monkey-patch a FastAPI lifespan from here;
    # writing the file before .run() means the Rust shell only sees
    # it once the server has actually started listening (since we
    # block on .run() and Rust polls).
    if args.ready_file:
        # Defer the write until after uvicorn has bound the socket.
        # Hook into the config's installed_signal_handlers cycle via
        # a startup callback on the app's router.
        from kompany.interfaces.api import app as _app

        @_app.on_event("startup")
        async def _signal_ready() -> None:  # pragma: no cover — exercised in real boot
            _write_ready_file(Path(args.ready_file), args.port)

    try:
        server.run()
    finally:
        # Best-effort: a crash leaves a stale server.json behind, which
        # readers detect via the health probe + pid check anyway.
        remove_discovery_file()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
