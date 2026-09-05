"""``kompany doctor`` — one health tree that says what is broken and how to fix it.

Read-only, offline (no LLM or network calls), cheap enough to run from a
REST handler. Every check is isolated: a check that raises becomes a
``warn`` node carrying the exception instead of failing the whole report,
so the founder always gets a tree.

Node shape (stable, additive-only)::

    {"id", "label", "status": "ok" | "warn" | "fail" | "info",
     "detail": str, "fix": str | None, "children": [node, ...]}

Roll-up: a parent's status is the worst of its children (fail > warn > ok);
``info`` never affects the roll-up. The root carries ``summary`` counts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

_RANK = {"ok": 0, "info": 0, "warn": 1, "fail": 2}
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def node(id_: str, label: str, status: str, detail: str = "", fix: str | None = None,
         children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    kids = children or []
    if kids:
        worst = max((k["status"] for k in kids), key=lambda s: _RANK.get(s, 0))
        if _RANK.get(worst, 0) > _RANK.get(status, 0):
            status = worst
    return {"id": id_, "label": label, "status": status, "detail": detail, "fix": fix, "children": kids}


def _guard(id_: str, label: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — a broken check is itself a finding
        return node(id_, label, "warn", f"check could not run: {type(exc).__name__}: {exc}",
                    "Run `kompany doctor --json` and attach the output to a bug report.")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_database(engine: Any) -> dict[str, Any]:
    row = engine.db.execute("PRAGMA quick_check").fetchone()
    verdict = str(row[0]) if row else "no result"
    ok = verdict.lower() == "ok"
    size = engine.db.db_path.stat().st_size if engine.db.db_path.exists() else 0
    return node("database", "SQLite database", "ok" if ok else "fail",
                f"{engine.db.db_path.name} quick_check={verdict}, {size / 1024:.0f} KiB",
                None if ok else "Restore the latest verified backup: `kompany backups` then `kompany restore <id>`.")


def check_runtime(engine: Any) -> dict[str, Any]:
    state = engine.get_runtime_state() or {}
    st = str(state.get("state") or "unknown")
    ticker = getattr(engine, "ticker", None)
    ticks = None
    try:
        ticks = engine.daemon_ticks.count() if hasattr(engine, "daemon_ticks") and hasattr(engine.daemon_ticks, "count") else None
    except Exception:  # noqa: BLE001
        ticks = None
    if st == "suspended":
        return node("runtime", "Runtime", "warn", f"suspended since {state.get('since')}: {state.get('reason') or 'no reason'}",
                    "Resume with `kompany runtime resume` once the cause is fixed.")
    detail = f"state={st}"
    if ticker is not None:
        detail += f", tick interval {getattr(ticker, 'tick_interval_seconds', '?')}s"
    if ticks is not None:
        detail += f", {ticks} ticks recorded"
    return node("runtime", "Runtime", "ok" if st == "running" else "info", detail)


def check_llm(engine: Any) -> dict[str, Any]:
    s = engine.settings
    keys = {name: bool(getattr(s, name, "")) for name in (
        "anthropic_api_key", "openai_api_key", "gemini_api_key", "glm_api_key", "kimi_api_key", "custom_api_key")}
    source = None
    try:
        from kompany.core.model_source_ops import get_model_source
        source = get_model_source(engine)
    except Exception:  # noqa: BLE001
        source = None
    tiers = {t: s.get_model_for_tier(t) for t in ("apex", "primary", "economy")}
    has_key = any(keys.values())
    detail = "models " + ", ".join(f"{t}={m}" for t, m in tiers.items())
    if source:
        detail += f"; model source {source.get('provider') or source.get('kind') or 'configured'}"
    if has_key:
        detail += "; keys: " + ", ".join(k.replace("_api_key", "") for k, v in keys.items() if v)
    if has_key or source:
        return node("llm", "LLM provider", "ok", detail)
    return node("llm", "LLM provider", "fail", detail + "; no API key and no model source",
                "Connect a provider in Settings → Model, or `kompany credentials set <provider>_api_key`.")


def check_health_events(engine: Any) -> dict[str, Any]:
    rows = engine.health_events.list(status="open", limit=50)
    if not rows:
        return node("health_events", "Watchdog events", "ok", "no open events")
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[str(r.get("kind"))] = kinds.get(str(r.get("kind")), 0) + 1
    children = [node(f"health.{k}", k, "fail" if k in ("runway_alert", "retry_exhausted", "stranded_in_progress") else "warn",
                     f"{n} open", "Resolve in NEEDS YOU (continue / snooze / dismiss).") for k, n in sorted(kinds.items())]
    return node("health_events", "Watchdog events", "warn", f"{len(rows)} open", None, children)


def check_work(engine: Any) -> dict[str, Any]:
    projects = engine.projects.list_active()
    blocked: list[str] = []
    for p in projects:
        for t in engine.projects.list_tasks(p.id):
            if str(getattr(t.status, "value", t.status)) == "blocked":
                ask = (t.result or {}).get("founder_action") if isinstance(t.result, dict) else None
                blocked.append(f"{p.name}: {t.title} — {ask or 'needs a connection or approval'}")
    pending = len(engine.approvals.list_pending())
    children = [
        node("work.projects", "Active projects", "ok" if projects else "info", f"{len(projects)} active"),
        node("work.blocked", "Blocked tasks", "warn" if blocked else "ok",
             "; ".join(blocked)[:600] if blocked else "none",
             "Each blocked task names the connection or approval it needs — see NEEDS YOU." if blocked else None),
        node("work.approvals", "Pending approvals", "info" if pending else "ok", f"{pending} waiting for you"),
    ]
    return node("work", "Work", "ok", "", None, children)


def check_integrations(engine: Any) -> dict[str, Any]:
    from kompany.core.tool_actions import integrations_list
    rows = integrations_list(engine)
    children = []
    for r in rows:
        connected = bool(r.get("connected"))
        children.append(node(f"integration.{r['integration_id']}", r.get("display_name") or r["integration_id"],
                             "ok" if connected else "info",
                             "connected" if connected else "not connected (" + ", ".join(r.get("required_credentials") or []) + ")",
                             None if connected else "Connect it in Settings → Integrations when the team needs it."))
    return node("integrations", "Integrations", "ok", f"{sum(1 for c in children if c['status'] == 'ok')}/{len(children)} connected", None, children)


def check_backups(engine: Any) -> dict[str, Any]:
    rows = engine.backups.list_backups()
    if not rows:
        return node("backups", "Backups", "warn", "no backups yet", "Run `kompany backup create` (or enable remote backup).")
    latest = rows[0]
    created = latest.get("created_at") or ""
    age_days = None
    try:
        age_days = (datetime.now(UTC) - datetime.fromisoformat(created)).days
    except ValueError:
        pass
    stale = age_days is not None and age_days > 7
    detail = f"{len(rows)} snapshot(s); latest {latest.get('id')}"
    if age_days is not None:
        detail += f" ({age_days}d old)"
    if not latest.get("sha256"):
        detail += "; latest has no integrity digest"
    return node("backups", "Backups", "warn" if stale else "ok", detail,
                "Take a fresh snapshot: `kompany backup create`." if stale else None)


def check_access(engine: Any) -> dict[str, Any]:
    token = bool(getattr(engine.settings, "web_dashboard_token", ""))
    return node("access", "API access", "ok" if token else "info",
                "dashboard token set — every route requires it" if token
                else "no dashboard token — API is open on loopback only (public binds are refused)",
                None if token else "Set WEB_DASHBOARD_TOKEN before exposing the API beyond this machine.")


def check_build(engine: Any) -> dict[str, Any]:
    info: dict[str, Any] = {}
    try:
        from kompany.interfaces.api_parts.system import build_info
        info = build_info(engine) or {}
    except Exception:  # noqa: BLE001
        info = {}
    stale = bool(info.get("stale"))
    release = info.get("release") or {}
    drift = info.get("drift") or {}
    parts = [f"{k}={v}" for k, v in info.items() if v and k in ("version", "commit", "repo_head", "newer_commits")]
    if release.get("source"):
        parts.append(f"source={release['source']}")
    if drift.get("drift"):
        return node("build", "Build", "fail",
                    "deployment drift: " + ", ".join(parts), drift.get("hint"))
    return node("build", "Build", "warn" if stale else "info",
                ", ".join(parts) or "build info unavailable",
                info.get("hint") if stale else None)


CHECKS: tuple[tuple[str, str, Callable[[Any], dict[str, Any]]], ...] = (
    ("database", "SQLite database", check_database),
    ("runtime", "Runtime", check_runtime),
    ("llm", "LLM provider", check_llm),
    ("health_events", "Watchdog events", check_health_events),
    ("work", "Work", check_work),
    ("integrations", "Integrations", check_integrations),
    ("backups", "Backups", check_backups),
    ("access", "API access", check_access),
    ("build", "Build", check_build),
)


def run_doctor(engine: Any) -> dict[str, Any]:
    """Run every check; return the health tree with a summary."""
    children = [_guard(id_, label, lambda fn=fn: fn(engine)) for id_, label, fn in CHECKS]
    root = node("kompany", "Kompany", "ok", "", None, children)
    flat = _flatten(root)[1:]  # counts exclude the root roll-up itself
    root["summary"] = {
        "status": root["status"],
        "ok": sum(1 for n in flat if n["status"] == "ok"),
        "warn": sum(1 for n in flat if n["status"] == "warn"),
        "fail": sum(1 for n in flat if n["status"] == "fail"),
        "fixes": [f"{n['label']}: {n['fix']}" for n in flat if n.get("fix") and n["status"] in ("warn", "fail")],
        "checked_at": datetime.now(UTC).isoformat(),
    }
    return root


def _flatten(n: dict[str, Any]) -> list[dict[str, Any]]:
    out = [n]
    for c in n.get("children", []):
        out.extend(_flatten(c))
    return out


def render_tree(root: dict[str, Any]) -> str:
    """Plain-text tree for terminals without rich."""
    glyph = {"ok": "✓", "info": "·", "warn": "!", "fail": "✗"}
    lines: list[str] = []

    def walk(n: dict[str, Any], depth: int) -> None:
        pad = "  " * depth
        line = f"{pad}{glyph.get(n['status'], '?')} {n['label']}"
        if n.get("detail"):
            line += f" — {n['detail']}"
        lines.append(line)
        if n.get("fix") and n["status"] in ("warn", "fail"):
            lines.append(f"{pad}    fix: {n['fix']}")
        for c in n.get("children", []):
            walk(c, depth + 1)

    walk(root, 0)
    return "\n".join(lines)


__all__ = ["CHECKS", "node", "render_tree", "run_doctor"]
