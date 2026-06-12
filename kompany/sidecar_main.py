"""Tauri-sidecar entry point for the Kompany FastAPI backend.

Bundled with PyInstaller --onedir; the resulting binary
``kompany-server-<target-triple>`` is spawned by the Rust shell with::

    kompany-server --port <random> --data-dir <app_data_dir>

The Rust side picks the port, hands us a writable data directory, and
polls ``/health`` until the uvicorn server accepts connections. Once
healthy, the Tauri WebView opens against ``http://127.0.0.1:<port>/ui/``.

The actual boot (uvicorn server, ``--port 0`` resolution, discovery
publish thread, ready-file, discovery cleanup) lives in
``kompany.interfaces.server_boot.run_server`` — shared with ``kompany
daemon run`` (06-12-daemon-tick-loop PR2). This file stays a thin
argument-parsing wrapper so the PyInstaller entry point and its CLI
contract never change.

Keep this file dependency-light: PyInstaller's ``--onedir`` sweep
follows direct imports, so anything imported here at module load time
gets bundled. Lazy-import everything heavy (engine, etc.) inside
``main`` so cold-start stays fast.
"""

from __future__ import annotations

import argparse
import sys


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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))

    from kompany.interfaces.server_boot import run_server

    return run_server(
        host=args.host,
        port=args.port,
        data_dir_override=args.data_dir or None,
        ready_file=args.ready_file or None,
        source="sidecar",
    )


if __name__ == "__main__":
    raise SystemExit(main())
