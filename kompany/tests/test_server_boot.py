"""Shared server boot (06-12-daemon-tick-loop PR2).

No real uvicorn binds: ``run_server`` takes an injectable
``server_factory`` so these tests drive the discovery-file lifecycle
with a fake server object and assert pure config assembly. The real
uvicorn path is exercised by the sidecar integration test in
``test_mcp_sidecar_proxy.py``.
"""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import pytest

import sidecar_main
from kompany.interfaces import daemon_main, mcp_proxy
from kompany.interfaces.server_boot import run_server, write_ready_file


@pytest.fixture(autouse=True)
def _fresh_discovery_cache():
    mcp_proxy.reset_discovery_cache()
    yield
    mcp_proxy.reset_discovery_cache()


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """run_server mutates KOMPANY_DATA_DIR; keep that scoped to the test."""
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path))
    return tmp_path


class _FakeSocket:
    def __init__(self, port: int):
        self._port = port

    def getsockname(self):
        return ("127.0.0.1", self._port)


def _make_factory(
    tmp_path,
    bound_port: int = 43210,
    run_error: Exception | None = None,
    broken_sockets: bool = False,
    wait_for_discovery: bool = True,
):
    """A uvicorn.Server stand-in factory; returns (factory, created_list).

    The fake's ``run()`` flips ``started`` (releasing the discovery
    watcher thread), then blocks until the discovery file appears and
    records its contents — so the test can assert the publish even
    though ``run_server``'s ``finally`` removes the file afterwards.
    """
    created: list = []

    class _FakeServer:
        def __init__(self, config):
            self.config = config
            self.started = False
            self.should_exit = False
            if broken_sockets:
                self.servers = []
            else:
                self.servers = [SimpleNamespace(sockets=[_FakeSocket(bound_port)])]
            self.discovery_seen = None
            created.append(self)

        def run(self):
            if run_error is not None:
                # Let the watcher thread exit WITHOUT publishing —
                # otherwise it would outlive this test and write into
                # a later test's data dir.
                self.should_exit = True
                raise run_error
            if not wait_for_discovery:
                self.should_exit = True
                return
            self.started = True
            path = tmp_path / mcp_proxy.DISCOVERY_FILENAME
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if path.exists():
                    self.discovery_seen = json.loads(
                        path.read_text(encoding="utf-8")
                    )
                    return
                time.sleep(0.01)
            raise AssertionError("discovery file was never published")

    return _FakeServer, created


# ---------------------------------------------------------------------------
# run_server — config assembly + discovery lifecycle
# ---------------------------------------------------------------------------


def test_run_server_assembles_uvicorn_config(tmp_path):
    factory, created = _make_factory(tmp_path, wait_for_discovery=False)

    rc = run_server(
        host="127.0.0.1",
        port=7777,
        data_dir_override=str(tmp_path),
        server_factory=factory,
    )

    assert rc == 0
    assert len(created) == 1
    config = created[0].config
    assert config.app == "kompany.interfaces.api:app"
    assert config.host == "127.0.0.1"
    assert config.port == 7777
    assert config.workers == 1


def test_run_server_sets_data_dir_env(tmp_path):
    factory, _ = _make_factory(tmp_path, wait_for_discovery=False)

    run_server(data_dir_override=str(tmp_path), server_factory=factory)

    assert os.environ["KOMPANY_DATA_DIR"] == str(tmp_path)


def test_discovery_published_with_bound_port_then_removed(tmp_path):
    factory, created = _make_factory(tmp_path, bound_port=43210)

    run_server(
        port=0,
        data_dir_override=str(tmp_path),
        source="daemon",
        server_factory=factory,
    )

    seen = created[0].discovery_seen
    assert seen is not None
    assert seen["port"] == 43210  # the bound socket, not the requested 0
    assert seen["pid"] == os.getpid()
    assert seen["source"] == "daemon"
    # Shutdown cleanup: the lock file is gone after run() returns.
    assert not (tmp_path / mcp_proxy.DISCOVERY_FILENAME).exists()


def test_discovery_source_defaults_to_sidecar(tmp_path):
    factory, created = _make_factory(tmp_path)

    run_server(data_dir_override=str(tmp_path), server_factory=factory)

    assert created[0].discovery_seen["source"] == "sidecar"


def test_discovery_falls_back_to_requested_port_without_sockets(tmp_path):
    factory, created = _make_factory(tmp_path, broken_sockets=True)

    run_server(port=5555, data_dir_override=str(tmp_path), server_factory=factory)

    assert created[0].discovery_seen["port"] == 5555


def test_discovery_removed_even_when_server_run_raises(tmp_path):
    factory, _ = _make_factory(tmp_path, run_error=RuntimeError("bind exploded"))
    # Simulate a stale publish so the finally-cleanup is observable.
    mcp_proxy.write_discovery_file(1234, tmp_path)

    with pytest.raises(RuntimeError, match="bind exploded"):
        run_server(data_dir_override=str(tmp_path), server_factory=factory)

    assert not (tmp_path / mcp_proxy.DISCOVERY_FILENAME).exists()


# ---------------------------------------------------------------------------
# write_ready_file
# ---------------------------------------------------------------------------


def test_write_ready_file_writes_port(tmp_path):
    path = tmp_path / "nested" / "ready.txt"

    write_ready_file(path, 8123)

    assert path.read_text(encoding="utf-8") == "ready 8123\n"


def test_write_ready_file_swallows_unwritable_path():
    # /dev/null is a file, so the parent mkdir fails with OSError.
    write_ready_file(__import__("pathlib").Path("/dev/null/x/ready.txt"), 1)


# ---------------------------------------------------------------------------
# Entry-point smoke — sidecar_main keeps its CLI contract, daemon_main
# delegates with source="daemon".
# ---------------------------------------------------------------------------


def test_sidecar_main_arg_parsing_defaults():
    args = sidecar_main._parse_args([])
    assert args.port == 0
    assert args.host == "127.0.0.1"
    assert args.data_dir == ""
    assert args.ready_file == ""


def test_sidecar_main_delegates_to_run_server(monkeypatch, tmp_path):
    calls = []

    def fake_run_server(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(
        "kompany.interfaces.server_boot.run_server", fake_run_server
    )

    rc = sidecar_main.main([
        "--port", "8123",
        "--host", "0.0.0.0",
        "--data-dir", str(tmp_path),
        "--ready-file", str(tmp_path / "ready"),
    ])

    assert rc == 0
    assert calls == [{
        "host": "0.0.0.0",
        "port": 8123,
        "data_dir_override": str(tmp_path),
        "ready_file": str(tmp_path / "ready"),
        "source": "sidecar",
    }]


def test_sidecar_main_empty_optionals_become_none(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "kompany.interfaces.server_boot.run_server",
        lambda **kwargs: calls.append(kwargs) or 0,
    )

    sidecar_main.main([])

    assert calls[0]["data_dir_override"] is None
    assert calls[0]["ready_file"] is None


def test_daemon_main_delegates_with_daemon_source(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "kompany.interfaces.server_boot.run_server",
        lambda **kwargs: calls.append(kwargs) or 0,
    )

    rc = daemon_main.main(["--port", "9911", "--data-dir", str(tmp_path)])

    assert rc == 0
    assert calls == [{
        "host": "127.0.0.1",
        "port": 9911,
        "data_dir_override": str(tmp_path),
        "source": "daemon",
    }]


def test_api_startup_starts_engine_background_workers(monkeypatch):
    """Server boot must start the watchdog + ticker (live-verification
    finding: engine.start() existed but nothing awaited it, so background
    workers never ran in production)."""
    from fastapi.testclient import TestClient

    import kompany.interfaces.api as api_mod

    class _WorkerEngine:
        def __init__(self):
            self.started = 0
            self.stopped = 0

        async def start(self):
            self.started += 1

        async def stop(self):
            self.stopped += 1

    engine = _WorkerEngine()
    monkeypatch.setattr(api_mod, "_engine", engine)
    with TestClient(api_mod.app):
        pass
    assert engine.started == 1
    assert engine.stopped == 1


def test_api_startup_tolerates_engine_without_worker_surface(monkeypatch):
    from fastapi.testclient import TestClient

    import kompany.interfaces.api as api_mod

    class _Bare:
        pass

    monkeypatch.setattr(api_mod, "_engine", _Bare())
    with TestClient(api_mod.app):
        pass  # must not raise
