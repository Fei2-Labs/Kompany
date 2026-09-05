"""Child-process entry for one extension run. STDLIB ONLY — never import
kompany here. Started as ``python -I -S worker_main.py <pkg_dir> <entrypoint>``
so the extension sees no site-packages and no ambient environment; every
capability goes back to the parent over the JSON-lines protocol on stdio.

Protocol (one JSON object per line):
  parent → child   {"type": "job", "job": {...}}
  child  → parent  {"type": "request", "id": n, "op": "tool|read|write|fetch|credential|log", ...}
  parent → child   {"type": "response", "id": n, "ok": bool, ...}
  child  → parent  {"type": "result", "ok": bool, "result": ..., "error": ...}
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback


class _Host:
    """Capability proxy handed to ``run(job, host)``. Every call is mediated
    and allowlisted by the parent; a denied capability raises here."""

    def __init__(self) -> None:
        self._n = 0

    def _call(self, op: str, **payload):
        self._n += 1
        sys.stdout.write(json.dumps({"type": "request", "id": self._n, "op": op, **payload}) + "\n")
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            raise RuntimeError("host closed the channel")
        res = json.loads(line)
        if not res.get("ok"):
            if res.get("denied"):
                raise PermissionError(res.get("error") or f"{op} denied")
            raise RuntimeError(res.get("error") or f"{op} failed")
        return res.get("value")

    def tool(self, name: str, inputs: dict | None = None):
        return self._call("tool", name=name, inputs=inputs or {})

    def read(self, path: str) -> str:
        return self._call("read", path=path)

    def write(self, path: str, text: str) -> None:
        self._call("write", path=path, text=text)

    def fetch(self, url: str) -> dict:
        return self._call("fetch", url=url)

    def credential(self, connector: str) -> dict:
        return self._call("credential", connector=connector)

    def log(self, message: str) -> None:
        self._call("log", message=str(message)[:2000])


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    pkg_dir, entrypoint = sys.argv[1], sys.argv[2]
    first = sys.stdin.readline()
    try:
        job = json.loads(first).get("job", {}) if first else {}
    except ValueError:
        job = {}
    sys.path.insert(0, pkg_dir)
    try:
        spec = importlib.util.spec_from_file_location("kompany_extension_entry", f"{pkg_dir}/{entrypoint}")
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {entrypoint}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        run = getattr(module, "run", None)
        if not callable(run):
            raise AttributeError(f"{entrypoint} defines no run(job, host)")
        result = run(job, _Host())
        json.dumps(result)  # must be JSON-able
        _emit({"type": "result", "ok": True, "result": result})
        return 0
    except Exception as exc:  # noqa: BLE001 — reported to the parent, never swallowed
        _emit({"type": "result", "ok": False, "error": f"{type(exc).__name__}: {exc}",
               "traceback": traceback.format_exc()[-2000:]})
        return 1


if __name__ == "__main__":
    sys.exit(main())
