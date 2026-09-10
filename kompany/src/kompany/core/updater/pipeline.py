"""check → apply (backup, download, verify, install, switch, restart) → verify-after-restart → rollback."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from kompany.core.event_hub import get_event_hub
from kompany.core.updater import feed, install
from kompany.core.updater.state import UpdateState, load_state, save_state

log = logging.getLogger(__name__)
PRO_TOKEN_CREDENTIAL = "github_release_token"
_APPLY_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _versions() -> tuple[str, str | None]:
    from importlib.metadata import PackageNotFoundError, version

    from kompany import __version__

    try:
        pro: str | None = version("kompany-pro")
    except PackageNotFoundError:
        pro = None
    return str(__version__), pro


def _pro_token(engine: Any) -> str | None:
    try:
        return engine.credentials.get(PRO_TOKEN_CREDENTIAL) or None
    except Exception:  # noqa: BLE001
        return None


def _publish(state: UpdateState) -> None:
    try:
        get_event_hub().publish("update.progress", {"phase": state.phase, "target": state.target_version,
                                                     "error": state.error, "step": state.steps[-1] if state.steps else None})
    except Exception:  # noqa: BLE001
        pass


def _set(engine: Any, state: UpdateState, phase: str, step: str = "", detail: str = "") -> None:
    state.phase = phase
    if step:
        state.step(step, detail)
    save_state(engine.settings.data_dir, state)
    _publish(state)


# ---------------------------------------------------------------------------

def check_for_update(engine: Any, *, fetch: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Compare installed Core/Pro with the latest GitHub releases. Network, no spend."""
    data_dir = engine.settings.data_dir
    state = load_state(data_dir)
    state.installed_version, state.installed_pro_version = _versions()
    state.mode = str(getattr(engine.settings, "update_mode", "manual"))
    if state.phase in ("done", "failed", "rolled_back", "idle", "checking"):
        state.phase = "checking"
    latest: dict[str, Any] = {}
    errors: list[str] = []
    try:
        core = feed.latest_release("kompany", fetch=fetch)
        latest["kompany"] = core.as_dict()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"kompany: {exc}")
    if state.installed_pro_version:
        token = _pro_token(engine)
        try:
            pro = feed.latest_release("kompany-pro", token=token, fetch=fetch)
            latest["kompany-pro"] = pro.as_dict()
        except Exception as exc:  # noqa: BLE001
            hint = ("" if token else f" — the Pro repo is private: `kompany credentials set {PRO_TOKEN_CREDENTIAL}` "
                    "with a read-only GitHub token")
            errors.append(f"kompany-pro: {exc}{hint}")
    state.latest = latest
    state.last_check_at = _now()
    core_latest = latest.get("kompany", {}).get("version")
    pro_latest = latest.get("kompany-pro", {}).get("version")
    state.update_available = bool(
        (core_latest and feed.parse_version(core_latest) > feed.parse_version(state.installed_version))
        or (pro_latest and state.installed_pro_version
            and feed.parse_version(pro_latest) > feed.parse_version(state.installed_pro_version))
    )
    if state.phase == "checking":
        state.phase = "idle"
    state.error = "; ".join(errors) if errors else None
    save_state(data_dir, state)
    out = status(engine, state)
    engine.audit.record("update.checked", f"Update check: installed {state.installed_version}, latest {core_latest or '?'}"
                        + (" — update available" if state.update_available else ""),
                        detail={"installed": state.installed_version, "latest": latest, "errors": errors})
    return out


def status(engine: Any, state: UpdateState | None = None) -> dict[str, Any]:
    st = state or load_state(engine.settings.data_dir)
    if st.installed_version is None:
        st.installed_version, st.installed_pro_version = _versions()
    lay = install.layout(engine.settings.data_dir)
    can_apply, why = _can_apply(lay)
    return {**st.model_dump(), "layout": lay, "can_apply": can_apply, "cannot_apply_reason": why,
            "supervised": install.supervised(), "mode": str(getattr(engine.settings, "update_mode", "manual"))}


def _can_apply(lay: dict[str, Any]) -> tuple[bool, str | None]:
    if lay["frozen"]:
        return False, "This engine is the desktop app's bundled sidecar — update by reinstalling Kompany.app."
    if not lay["running_from_release"]:
        return False, ("This engine does not run from <data_dir>/releases/current — run ops/bootstrap_release.sh once "
                       "(it migrates a checkout install to the release layout); afterwards updates are one click.")
    return True, None


# ---------------------------------------------------------------------------

def apply_update(engine: Any, version: str | None = None, *, fetch: Callable[..., Any] | None = None,
                 run: Callable[..., Any] | None = None, restart: Callable[[], None] | None = None,
                 wait_for_lock: bool = False) -> dict[str, Any]:
    """Download → verify → install into a new venv → backup → switch → restart.

    Synchronous; callers that need a non-blocking surface run it in a thread
    and read ``status``. Never raises: the state carries ``phase`` + ``error``.
    """
    if not _APPLY_LOCK.acquire(blocking=wait_for_lock):
        return {**status(engine), "error": "an update is already running"}
    try:
        return _apply(engine, version, fetch=fetch, run=run, restart=restart)
    finally:
        _APPLY_LOCK.release()


def _apply(engine: Any, version: str | None, *, fetch, run, restart) -> dict[str, Any]:
    data_dir = Path(engine.settings.data_dir)
    state = load_state(data_dir)
    state.installed_version, state.installed_pro_version = _versions()
    state.mode = str(getattr(engine.settings, "update_mode", "manual"))
    lay = install.layout(data_dir)
    ok, why = _can_apply(lay)
    if not ok:
        state.phase, state.error = "failed", why
        save_state(data_dir, state)
        return status(engine, state)
    state.error = None; state.steps = []; state.attestation = {}; state.backup_id = None
    state.restart_required = False; state.verify_pending = False; state.rollback_attempted = False
    state.started_at, state.finished_at = _now(), None
    try:
        # --- resolve target -------------------------------------------------
        _set(engine, state, "checking", "resolve", "reading GitHub releases")
        core = feed.latest_release("kompany", fetch=fetch)
        if version and feed.parse_version(version) != feed.parse_version(core.version):
            raise feed.FeedError(f"requested {version} but the latest release is {core.version}; only the latest release can be installed")
        if feed.parse_version(core.version) <= feed.parse_version(state.installed_version) and not version:
            state.target_version = core.version
            _set(engine, state, "done", "noop", f"already on {state.installed_version}")
            state.finished_at = _now(); save_state(data_dir, state)
            return status(engine, state)
        state.target_version = core.version
        pro: feed.ReleaseInfo | None = None
        pro_feed_error: str | None = None
        if state.installed_pro_version:
            # Pro is private. Without a working feed we still update Core and
            # carry the installed Pro over unchanged — never strand it.
            try:
                pro = feed.latest_release("kompany-pro", token=_pro_token(engine), fetch=fetch)
            except Exception as exc:  # noqa: BLE001
                pro_feed_error = f"{exc}" + ("" if _pro_token(engine) else
                                             f" (no {PRO_TOKEN_CREDENTIAL} in the vault — Pro will be carried over, not updated)")
        release_dir = install.releases_dir(data_dir) / core.version
        dl_dir = data_dir / "update" / "downloads" / core.version

        # --- backup first ---------------------------------------------------
        _set(engine, state, "backing_up", "backup", "sqlite snapshot before the switch")
        try:
            meta = engine.backups.create_backup(label=f"pre-update-{core.version}", kind="auto")
            state.backup_id = str(meta.get("id") or meta.get("backup_id") or "")
        except Exception as exc:  # noqa: BLE001 — a failed backup stops the update
            raise RuntimeError(f"backup failed: {exc}") from exc

        # --- download + verify ------------------------------------------------
        _set(engine, state, "downloading", "download", f"{core.wheel_name}")
        if not core.wheel_name:
            raise feed.FeedError(f"release {core.tag} carries no Core wheel")
        wheels = [feed.download_asset(core, core.wheel_name, dl_dir, fetch=fetch)]
        if pro is not None:
            if not pro.wheel_name:
                raise feed.FeedError(f"Pro release {pro.tag} carries no wheel")
            _set(engine, state, "downloading", "download", f"{pro.wheel_name}")
            wheels.append(feed.download_asset(pro, pro.wheel_name, dl_dir, token=_pro_token(engine), fetch=fetch))
        elif pro_feed_error:
            _set(engine, state, "downloading", "pro_feed_unavailable", pro_feed_error[:400])
        _set(engine, state, "verifying", "verify", "sha256 matched the release manifest; checking provenance")
        att = feed.verify_attestation(wheels[0], core.repo, run=run)
        state.attestation = att
        if att["status"] == "failed":
            raise feed.FeedError(f"build provenance verification failed: {att['detail']}")

        # --- install into a fresh venv --------------------------------------
        _set(engine, state, "installing", "venv", str(release_dir))
        install.create_venv(release_dir, run=run)
        _set(engine, state, "installing", "pip", ", ".join(w.name for w in wheels))
        install.pip_install(release_dir, wheels, run=run)
        got = install.installed_version(release_dir, run=run)
        if got and feed.parse_version(got) != feed.parse_version(core.version):
            raise RuntimeError(f"installed venv reports kompany {got}, expected {core.version}")
        if pro is None and state.installed_pro_version and lay.get("current"):
            carried = install.carry_over_package(install.releases_dir(data_dir) / str(lay["current"]), release_dir)
            if carried is None:
                raise RuntimeError("Pro release feed unavailable and the installed Pro could not be carried over")
            _set(engine, state, "installing", "pro_carried_over", f"kompany-pro {carried} copied from release {lay['current']}")

        # --- switch + restart -------------------------------------------------
        _set(engine, state, "switching", "switch", f"releases/current → {core.version}")
        state.previous_version = install.switch_current(data_dir, core.version)
        state.verify_pending = True
        sup = install.supervised()
        state.restart_required = sup is None
        _set(engine, state, "restarting", "restart",
             f"exiting for {sup} to restart the new release" if sup else "not supervised — restart the daemon by hand")
        engine.audit.record("update.switched", f"Update {state.installed_version} → {core.version} installed; restarting",
                            detail={"previous": state.previous_version, "target": core.version, "backup_id": state.backup_id,
                                    "attestation": att, "supervised": sup})
        _notify(engine, f"Kompany updating {state.installed_version} → {core.version}; the engine restarts now", "info")
        if sup:
            (restart or _exit_soon)()
        return status(engine, state)
    except Exception as exc:  # noqa: BLE001 — every failure is a state, not a crash
        state.error = f"{type(exc).__name__}: {exc}"
        _set(engine, state, "failed", "failed", state.error)
        state.finished_at = _now(); save_state(data_dir, state)
        engine.audit.record("update.failed", f"Update failed: {state.error}", detail={"target": state.target_version,
                            "steps": state.steps[-5:]})
        return status(engine, state)


def _exit_soon(delay: float = 1.5) -> None:
    """Let the HTTP response flush, then exit; the supervisor restarts us on the new release."""
    def _go() -> None:
        time.sleep(delay)
        log.warning("update: exiting for supervisor restart")
        os._exit(0)
    threading.Thread(target=_go, name="kompany-update-exit", daemon=True).start()


# ---------------------------------------------------------------------------

def verify_after_restart(engine: Any, *, restart: Callable[[], None] | None = None) -> dict[str, Any] | None:
    """Boot hook. After a switch: doctor clean → done; doctor fails → roll back once."""
    data_dir = Path(engine.settings.data_dir)
    state = load_state(data_dir)
    if not state.verify_pending:
        return None
    running, _ = _versions()
    state.installed_version = running
    if state.target_version and feed.parse_version(running) != feed.parse_version(state.target_version):
        state.verify_pending = False
        state.error = (f"restarted but still running {running}, not {state.target_version} — the supervisor's ExecStart "
                       "must point at releases/current/venv (run `kompany daemon install`)")
        _set(engine, state, "failed", "verify", state.error)
        engine.audit.record("update.failed", state.error, detail={"target": state.target_version})
        return status(engine, state)
    from kompany.core.doctor import gate_failures

    report = engine.doctor()
    failing = gate_failures(report)  # watchdog alarms and a missing LLM key are not release defects
    if failing and state.previous_version and not state.rollback_attempted:
        state.rollback_attempted = True
        state.verify_pending = False  # the rollback is the terminal outcome of this update
        try:
            install.switch_current(data_dir, state.previous_version)
        except Exception as exc:  # noqa: BLE001
            state.verify_pending = False
            state.error = f"doctor failed ({failing}) and rollback failed: {exc}"
            _set(engine, state, "failed", "rollback", state.error)
            return status(engine, state)
        state.error = f"doctor failed on {', '.join(failing)} — rolled back to {state.previous_version}"
        _set(engine, state, "rolled_back", "rollback", state.error)
        engine.audit.record("update.rolled_back", state.error, detail={"target": state.target_version,
                            "previous": state.previous_version, "failing": failing})
        _notify(engine, f"Kompany update to {state.target_version} rolled back: {', '.join(failing)}", "warning")
        if install.supervised():
            (restart or _exit_soon)()
        return status(engine, state)
    state.verify_pending = False
    state.finished_at = _now()
    if failing:
        state.error = f"doctor still failing after rollback: {', '.join(failing)}"
        _set(engine, state, "failed", "verify", state.error)
    else:
        state.error = None
        _set(engine, state, "done", "verify", f"running {running}, doctor clean")
        engine.audit.record("update.completed", f"Update to {running} verified", detail={"previous": state.previous_version,
                            "backup_id": state.backup_id})
        _notify(engine, f"Kompany updated to {running}", "info")
    return status(engine, state)


def rollback_update(engine: Any, *, restart: Callable[[], None] | None = None) -> dict[str, Any]:
    """Founder-initiated: point current at the previous release and restart."""
    data_dir = Path(engine.settings.data_dir)
    state = load_state(data_dir)
    target = state.previous_version
    if not target:
        state.error = "no previous release to roll back to"
        save_state(data_dir, state)
        return status(engine, state)
    lay = install.layout(data_dir)
    ok, why = _can_apply(lay)
    if not ok:
        state.error = why; save_state(data_dir, state)
        return status(engine, state)
    install.switch_current(data_dir, target)
    state.previous_version, state.target_version = state.installed_version, target
    state.verify_pending, state.rollback_attempted = True, True
    state.restart_required = install.supervised() is None
    _set(engine, state, "restarting", "rollback", f"releases/current → {target} (founder)")
    engine.audit.record("update.rollback_requested", f"Founder rollback to {target}", detail={"from": state.installed_version})
    if install.supervised():
        (restart or _exit_soon)()
    return status(engine, state)


def tick_action(engine: Any) -> list[str]:
    """Ticker: periodic check; automatic_when_idle applies when no agent is working."""
    settings = engine.settings
    hours = float(getattr(settings, "update_check_interval_hours", 6))
    state = load_state(engine.settings.data_dir)
    if state.phase in ("downloading", "installing", "switching", "restarting", "backing_up", "verifying"):
        return []
    due = True
    if state.last_check_at:
        try:
            due = (datetime.now(UTC) - datetime.fromisoformat(state.last_check_at)).total_seconds() >= hours * 3600
        except ValueError:
            due = True
    out: list[str] = []
    if due:
        check_for_update(engine)
        out.append("update_check")
        state = load_state(engine.settings.data_dir)
    if (state.update_available and str(getattr(settings, "update_mode", "manual")) == "automatic_when_idle"
            and _idle(engine) and _can_apply(install.layout(engine.settings.data_dir))[0]):
        threading.Thread(target=apply_update, args=(engine,), name="kompany-auto-update", daemon=True).start()
        out.append("update_apply")
    return out


def _idle(engine: Any) -> bool:
    try:
        return not any(str(r.get("status")) in ("working", "thinking") for r in engine.agent_status.list_all())
    except Exception:  # noqa: BLE001
        return False


def _notify(engine: Any, summary: str, severity: str) -> None:
    try:
        engine.dispatch_notifications([{"summary": summary, "severity": severity, "kind": "update"}])
    except Exception:  # noqa: BLE001
        pass


def _flatten(n: dict[str, Any]) -> list[dict[str, Any]]:
    out = [n]
    for c in n.get("children", []):
        out.extend(_flatten(c))
    return out


__all__ = ["PRO_TOKEN_CREDENTIAL", "apply_update", "check_for_update", "rollback_update", "status", "tick_action",
           "verify_after_restart"]
