"""Parent-side isolated worker (plan step 3).

Runs one extension job in a child ``python -I -S`` process (no site-packages,
no ambient env, scrubbed environment, private cwd, CPU/time limits) and
answers its capability requests — each checked against the manifest first.
Undeclared capability → the request is refused, recorded in ``denied`` and
audited; the extension gets a ``PermissionError``. The child never imports
kompany and never sees a credential value.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from kompany.core.extensions.manifest import ExtensionManifest
from kompany.core.harness.env_scrub import scrubbed_env

_WORKER_MAIN = Path(__file__).with_name("worker_main.py")
_MAX_REQUESTS = 500
_FETCH_CAP = 200_000


@dataclass
class RunOutcome:
    ok: bool
    result: Any = None
    error: str | None = None
    exit_code: int | None = None
    denied: list[dict[str, str]] = field(default_factory=list)
    requests: int = 0
    logs: list[str] = field(default_factory=list)
    stderr_tail: str = ""
    proposals: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "result": self.result, "error": self.error, "exit_code": self.exit_code,
                "denied": self.denied, "requests": self.requests, "logs": self.logs[-50:],
                "stderr_tail": self.stderr_tail, "proposals": self.proposals}


class ExtensionHost:
    """Capability broker for one run. ``engine`` may be None (pure checks)."""

    def __init__(self, engine: Any, manifest: ExtensionManifest, data_dir: Path, run_id: str):
        self.engine = engine
        self.m = manifest
        self.data_dir = Path(data_dir).resolve()
        self.run_id = run_id
        self.outcome = RunOutcome(ok=False)
        self._spent_estimate = 0.0

    # -- dispatch -----------------------------------------------------------

    def handle(self, req: dict[str, Any]) -> dict[str, Any]:
        self.outcome.requests += 1
        if self.outcome.requests > _MAX_REQUESTS:
            return self._deny(req, "request budget exhausted")
        op = req.get("op")
        try:
            if op == "log":
                self.outcome.logs.append(str(req.get("message", ""))[:2000]); return {"ok": True}
            if op == "tool":
                return self._tool(req)
            if op in ("read", "write"):
                return self._file(req)
            if op == "fetch":
                return self._fetch(req)
            if op == "credential":
                return self._credential(req)
        except PermissionError as exc:
            return self._deny(req, str(exc))
        except Exception as exc:  # noqa: BLE001 — host errors go back as failures, not crashes
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return self._deny(req, f"unknown capability {op!r}")

    def _deny(self, req: dict[str, Any], reason: str) -> dict[str, Any]:
        entry = {"op": str(req.get("op")), "reason": reason,
                 "target": str(req.get("name") or req.get("path") or req.get("url") or req.get("connector") or "")}
        self.outcome.denied.append(entry)
        audit = getattr(self.engine, "audit", None)
        if audit is not None:
            audit.record("extension.capability_denied", f"Extension {self.m.id}: {reason}",
                         detail={"extension_id": self.m.id, "run_id": self.run_id, **entry})
        return {"ok": False, "denied": True, "error": f"capability denied: {reason}"}

    # -- capabilities ------------------------------------------------------

    def _tool(self, req: dict[str, Any]) -> dict[str, Any]:
        name = str(req.get("name") or "")
        if name not in self.m.capabilities.tools:
            raise PermissionError(f"tool {name!r} not declared in manifest")
        inputs = dict(req.get("inputs") or {})
        from kompany.core import tool_actions

        entry = tool_actions.tool_registry(self.engine).get(name)
        if entry is None:
            return {"ok": False, "error": f"unknown tool: {name}"}
        tool = entry["tool"]
        try:
            parsed = tool.input_schema(**inputs) if tool.input_schema else inputs
            est = float(tool.estimate_cost(parsed).total_usd)
        except Exception as exc:  # noqa: BLE001 — bad inputs are the extension's error, not a denial
            return {"ok": False, "error": f"invalid inputs for {name}: {type(exc).__name__}: {exc}"}
        if self._spent_estimate + est > self.m.capabilities.budget_usd + 1e-9 and est > 0:
            raise PermissionError(f"tool {name!r} estimate ${est:.2f} exceeds the extension budget "
                                  f"(${self.m.capabilities.budget_usd:.2f})")
        out = self.engine.execute_tool(name, inputs)
        if out.get("ok"):
            return {"ok": True, "value": out.get("result")}
        if out.get("requires_approval"):
            card = self.engine.propose_action(
                name, inputs, f"Extension {self.m.id} proposes {name}",
                requested_by=f"extension:{self.m.id}", reason=f"extension run {self.run_id}",
            )
            self._spent_estimate += est
            approval_id = card.get("approval_id") or card.get("id")
            if approval_id:
                self.outcome.proposals.append(str(approval_id))
            return {"ok": True, "value": {"proposed": True, "approval_id": approval_id}}
        return {"ok": False, "error": str(out.get("detail") or "tool refused")}

    def _resolve_path(self, rel: str) -> Path:
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise PermissionError(f"path {rel!r} must be relative to the extension data dir")
        allowed = any(rel == p or rel.startswith(p.rstrip("/") + "/") or fnmatch.fnmatch(rel, p)
                      for p in self.m.capabilities.paths)
        if not allowed:
            raise PermissionError(f"path {rel!r} not declared in manifest paths")
        target = (self.data_dir / rel).resolve()
        if self.data_dir not in target.parents and target != self.data_dir:
            raise PermissionError(f"path {rel!r} escapes the extension data dir")
        return target

    def _file(self, req: dict[str, Any]) -> dict[str, Any]:
        target = self._resolve_path(str(req.get("path") or ""))
        if req.get("op") == "read":
            return {"ok": True, "value": target.read_text(encoding="utf-8") if target.is_file() else None}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(req.get("text", "")), encoding="utf-8")
        return {"ok": True}

    def _fetch(self, req: dict[str, Any]) -> dict[str, Any]:
        url = str(req.get("url") or "")
        host = (urlparse(url).hostname or "").lower()
        if not host or not any(host == a.lower() or fnmatch.fnmatch(host, a.lower()) for a in self.m.capabilities.network):
            raise PermissionError(f"host {host or url!r} not declared in manifest network")
        from kompany.core.agent_tools.net_guard import check_url, fetch_with_guard
        import httpx

        check_url(url)
        res = fetch_with_guard(url, client_get=httpx.get, timeout=20)
        return {"ok": True, "value": {"status": res.status_code, "text": res.text[:_FETCH_CAP]}}

    def _credential(self, req: dict[str, Any]) -> dict[str, Any]:
        connector = str(req.get("connector") or "")
        if connector not in self.m.capabilities.credentials:
            raise PermissionError(f"credential connector {connector!r} not declared in manifest")
        broker = getattr(self.engine, "credential_broker", None)
        if broker is None:
            return {"ok": False, "error": "credential broker unavailable"}
        from kompany.core.credential_broker import LeaseRequest

        lease = broker.issue_lease(LeaseRequest(
            secret_ref_id=connector, company_id=str(getattr(self.engine.settings, "company_id", "") or "default"),
            project_id=f"extension:{self.m.id}", agent_id=f"extension:{self.m.id}", worker_id=self.run_id,
            connector=connector, action="use", destination=(self.m.capabilities.network or [""])[0],
            ttl_seconds=300, max_uses=1,
        ))
        # Lease metadata only — the broker delivers the secret to the worker
        # boundary itself; plaintext never crosses this channel.
        return {"ok": True, "value": {"lease_id": lease.id, "expires_at": lease.expires_at.isoformat(),
                                      "uses_remaining": lease.uses_remaining}}


def _limits(cpu_seconds: int):
    def apply() -> None:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        except Exception:  # noqa: BLE001 — best-effort on platforms without rlimits
            pass
    return apply


def run_extension(engine: Any, manifest: ExtensionManifest, pkg_dir: Path, data_dir: Path,
                  job: dict[str, Any], *, run_id: str, timeout_seconds: int = 120,
                  python: str | None = None) -> RunOutcome:
    """Execute one job out of process and mediate its capability requests."""
    data_dir = Path(data_dir); data_dir.mkdir(parents=True, exist_ok=True)
    host = ExtensionHost(engine, manifest, data_dir, run_id)
    env = {k: v for k, v in scrubbed_env().items() if k in ("PATH", "LANG", "LC_ALL", "TZ")}
    env.update({"HOME": str(data_dir), "KOMPANY_EXTENSION_ID": manifest.id,
                "KOMPANY_EXTENSION_DATA": str(data_dir), "PYTHONDONTWRITEBYTECODE": "1"})
    cmd = [python or sys.executable, "-I", "-S", str(_WORKER_MAIN), str(pkg_dir), manifest.entrypoint]
    try:
        proc = subprocess.Popen(cmd, cwd=str(data_dir), env=env, text=True, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1,
                                preexec_fn=_limits(timeout_seconds) if sys.platform != "win32" else None)
    except OSError as exc:
        host.outcome.error = f"could not start worker: {exc}"
        return host.outcome
    timer = threading.Timer(timeout_seconds, proc.kill); timer.start()
    stderr_chunks: list[str] = []
    reader = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read() if proc.stderr else ""), daemon=True)
    reader.start()
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps({"type": "job", "job": job}) + "\n"); proc.stdin.flush()
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                host.outcome.logs.append(line[:2000]); continue
            if msg.get("type") == "request":
                res = host.handle(msg)
                proc.stdin.write(json.dumps({"type": "response", "id": msg.get("id"), **res}) + "\n"); proc.stdin.flush()
            elif msg.get("type") == "result":
                host.outcome.ok = bool(msg.get("ok")); host.outcome.result = msg.get("result")
                host.outcome.error = msg.get("error")
    except (BrokenPipeError, OSError) as exc:
        host.outcome.error = host.outcome.error or f"worker channel broke: {exc}"
    finally:
        try:
            proc.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        proc.wait(); timer.cancel(); reader.join(timeout=2)
    host.outcome.exit_code = proc.returncode
    host.outcome.stderr_tail = ("".join(stderr_chunks))[-2000:]
    if proc.returncode == -9 and not host.outcome.error:
        host.outcome.error = f"worker killed after {timeout_seconds}s"
    if host.outcome.result is None and not host.outcome.ok and not host.outcome.error:
        host.outcome.error = f"worker exited {proc.returncode} without a result"
    return host.outcome


__all__ = ["ExtensionHost", "RunOutcome", "run_extension"]
