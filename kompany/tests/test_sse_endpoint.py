"""SSE endpoint + static UI smoke tests."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from kompany.interfaces.api import app


def test_events_endpoint_is_registered():
    """The ``/events`` route is registered as a SSE streaming endpoint.

    We avoid actually opening the stream from the synchronous TestClient
    because the body is an infinite async generator and TestClient will
    block reading it. The generator itself is exercised directly in
    :func:`test_sse_stream_serializes_envelopes`.
    """
    routes = {r.path: r for r in app.routes if hasattr(r, "path")}
    assert "/events" in routes


def test_sse_stream_serializes_envelopes():
    """Exercise the SSE async generator in isolation so we don't rely on
    the FastAPI TestClient (which can't easily inject a publish)."""
    import asyncio

    from kompany.core.event_hub import reset_event_hub, get_event_hub
    from kompany.interfaces.api import _sse_event_stream

    async def run() -> bytes:
        reset_event_hub()
        hub = get_event_hub()
        gen = _sse_event_stream()
        chunks: list[bytes] = []
        # First chunk: ``: connected\n\n``.
        chunks.append(await asyncio.wait_for(gen.__anext__(), timeout=0.5))
        # Now publish — the subscriber queue is registered.
        hub.publish("audit.x", {"k": "v"})
        chunks.append(await asyncio.wait_for(gen.__anext__(), timeout=0.5))
        await gen.aclose()
        return b"".join(chunks)

    blob = asyncio.run(run())
    text = blob.decode()
    assert ": connected" in text
    assert "audit.x" in text
    # Validate the wire format: each event is on its own line.
    assert "data: " in text


def test_static_ui_index_served():
    with TestClient(app) as client:
        resp = client.get("/ui/")
        assert resp.status_code == 200
        assert "KOMPANY" in resp.text
        assert "/ui/static/app.js" in resp.text


def test_static_ui_assets_reachable():
    with TestClient(app) as client:
        for path in (
            "/ui/static/app.js",
            "/ui/static/style.css",
            "/ui/static/modules/store.js",
            "/ui/static/modules/sse.js",
            "/ui/static/modules/api.js",
            "/ui/static/modules/ui/office.js",
            "/ui/static/modules/ui/inbox.js",
            "/ui/static/modules/ui/timeline.js",
            "/ui/static/modules/ui/ledger.js",
            "/ui/static/modules/ui/episodes.js",
            "/ui/static/modules/ui/directive.js",
        ):
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} -> {resp.status_code}"


def test_root_serves_board_or_falls_back_to_ui():
    """``/`` serves the operations board SPA when it has been built.

    The board (kompany-core/board-ui) builds into ``board_ui/dist/``. When
    present, ``/`` returns the SPA shell (200). When the board hasn't been
    built (dev checkout without ``npm run build``), ``/`` falls back to a
    redirect to the old cyberpunk terminal at ``/ui/``.
    """
    with TestClient(app) as client:
        resp = client.get("/", follow_redirects=False)
        if resp.status_code == 200:
            # SPA shell served directly.
            assert '<div id="root"></div>' in resp.text
        else:
            # No build on disk — graceful fallback to /ui/.
            assert resp.status_code in (302, 303, 307)
            assert resp.headers["location"] in ("/ui/", "http://testserver/ui/")


def test_agents_status_returns_eleven_rows(monkeypatch):
    """The ``/agents/status`` endpoint always emits exactly 11 C-suite rows."""

    class _FakeAgentStatus:
        def list_all(self):
            return []

    class _FakeAudit:
        def recent(self, limit=200):
            return []

    class _FakeEngine:
        agent_status = _FakeAgentStatus()
        audit = _FakeAudit()

    monkeypatch.setattr("kompany.interfaces.api._engine", _FakeEngine())

    with TestClient(app) as client:
        resp = client.get("/agents/status")
        assert resp.status_code == 200
        rows = resp.json()
        assert isinstance(rows, list)
        assert len(rows) == 11
        roles = [r["role"] for r in rows]
        assert "CEO" in roles
        assert "CFO" in roles
        # All idle by default since the fake engine returns nothing.
        assert all(r["status"] == "idle" for r in rows)
