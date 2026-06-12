"""``python -m kompany.interfaces.daemon_main`` — headless daemon server.

The launchd LaunchAgent fallback entry point when no bundled
PyInstaller server binary exists (06-12-daemon-tick-loop PR2, D4):
``kompany daemon install`` writes ``[sys.executable, "-m",
"kompany.interfaces.daemon_main"]`` into the plist's ProgramArguments.
It boots the exact same server as the Tauri sidecar via
``server_boot.run_server`` — one engine, ticker included — and labels
the discovery file ``source="daemon"``.
"""

from __future__ import annotations

import argparse
import sys


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kompany-daemon",
        description="Kompany 24/7 daemon server (launchd entry point).",
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))

    from kompany.interfaces.server_boot import run_server

    return run_server(
        host=args.host,
        port=args.port,
        data_dir_override=args.data_dir or None,
        source="daemon",
    )


if __name__ == "__main__":
    raise SystemExit(main())
