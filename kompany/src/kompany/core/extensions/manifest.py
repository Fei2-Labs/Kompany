"""Extension manifest schema + validation (plan step 1).

``extension.json`` at the package root. Everything the host enforces is
declared here: which engine tools the extension may call, which paths
(relative to its private data dir) it may read/write through the host,
which network hosts it may fetch, which credential connectors it may lease,
and its spend budget. Undeclared = denied. The ``core_api`` range plays the
same role as Pro's ``kompany>=0.1,<0.2`` pin: an incompatible Core release
blocks the extension instead of running it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MANIFEST_FILENAME = "extension.json"
CONTRACT_VERSION = "1.1.0"
_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}([-+][0-9A-Za-z.-]+)?$")
_SPEC_RE = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)$")


class ManifestError(ValueError):
    """Manifest missing, unparsable or failing validation."""


class Capabilities(BaseModel):
    """Everything is opt-in; an empty list means the capability is denied."""

    model_config = ConfigDict(extra="forbid")

    tools: list[str] = Field(default_factory=list)
    """Engine tool names (``kompany tools list``) the extension may call."""
    paths: list[str] = Field(default_factory=list)
    """Relative path prefixes inside the extension's private data dir."""
    network: list[str] = Field(default_factory=list)
    """Hostnames the host may fetch for it (``*.example.com`` allowed)."""
    credentials: list[str] = Field(default_factory=list)
    """Credential-broker connector ids it may request scoped leases for."""
    budget_usd: float = Field(default=0.0, ge=0.0)
    """Ceiling on the estimated cost of tool actions it may propose."""

    @field_validator("paths")
    @classmethod
    def _relative_paths(cls, value: list[str]) -> list[str]:
        for p in value:
            if p.startswith("/") or ".." in Path(p).parts:
                raise ValueError(f"path {p!r} must be relative and inside the data dir")
        return value


class ExtensionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    owner: str = "customer"
    """``customer`` (this layer) or ``vendor`` (shipped by Pro)."""
    origin: str = ""
    """Where it came from — a path, URL or ``handwritten``; free text."""
    entrypoint: str = "main.py"
    """Python file inside the package exposing ``run(job, host)``."""
    runtime: str = "python"
    core_api: str = ""
    """Required Core version range, e.g. ``>=0.1,<0.2``. Empty = any."""
    capabilities: Capabilities = Field(default_factory=Capabilities)
    data_migration_version: int = Field(default=0, ge=0)
    rollback_to: str | None = None
    """Previous version to roll back to; the host never deletes old packages."""
    sha256: str | None = None
    """Expected package hash; verified on install when present."""
    contract_version: str = CONTRACT_VERSION
    description: str = ""

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError("id must match ^[a-z][a-z0-9_.-]{2,63}$")
        return value

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str) -> str:
        if not _VERSION_RE.match(value):
            raise ValueError("version must look like 1.2.3")
        return value

    @field_validator("owner")
    @classmethod
    def _owner(cls, value: str) -> str:
        if value not in ("customer", "vendor"):
            raise ValueError("owner must be customer or vendor")
        return value

    @field_validator("runtime")
    @classmethod
    def _runtime(cls, value: str) -> str:
        if value != "python":
            raise ValueError("only the python runtime is supported")
        return value

    @field_validator("entrypoint")
    @classmethod
    def _entry(cls, value: str) -> str:
        if value.startswith("/") or ".." in Path(value).parts or not value.endswith(".py"):
            raise ValueError("entrypoint must be a relative .py file inside the package")
        return value

    @field_validator("core_api")
    @classmethod
    def _spec(cls, value: str) -> str:
        for clause in _clauses(value):
            if not _SPEC_RE.match(clause):
                raise ValueError(f"core_api clause {clause!r} is not like >=0.1")
        return value

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "version": self.version, "owner": self.owner,
            "origin": self.origin, "core_api": self.core_api, "entrypoint": self.entrypoint,
            "capabilities": self.capabilities.model_dump(), "description": self.description,
        }


def load_manifest(package_dir: Path) -> ExtensionManifest:
    path = Path(package_dir) / MANIFEST_FILENAME
    if not path.is_file():
        raise ManifestError(f"{MANIFEST_FILENAME} not found in {package_dir}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"{path}: {exc}") from exc
    try:
        manifest = ExtensionManifest.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — pydantic error → one clear message
        raise ManifestError(f"{path}: {exc}") from exc
    if not (Path(package_dir) / manifest.entrypoint).is_file():
        raise ManifestError(f"entrypoint {manifest.entrypoint} missing from {package_dir}")
    return manifest


def package_hash(package_dir: Path) -> str:
    """sha256 over every regular file (sorted relative path + bytes)."""
    h = hashlib.sha256()
    root = Path(package_dir)
    for p in sorted(x for x in root.rglob("*") if x.is_file() and "__pycache__" not in x.parts):
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode()); h.update(b"\0"); h.update(p.read_bytes()); h.update(b"\0")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Core API range — a tiny PEP 440-ish comparator (no `packaging` dependency)
# ---------------------------------------------------------------------------

def _clauses(spec: str) -> list[str]:
    return [c.strip() for c in spec.split(",") if c.strip()]


def _vtuple(version: str) -> tuple[int, ...]:
    core = re.split(r"[-+]", version, maxsplit=1)[0]
    parts = tuple(int(x) for x in core.split(".") if x.isdigit())
    return parts + (0,) * (4 - len(parts))


def core_compatible(spec: str, core_version: str) -> tuple[bool, str]:
    """(ok, reason). Unknown Core version (``0.0.0+unknown``) never blocks."""
    if not spec.strip():
        return True, "no core_api range declared"
    if core_version.startswith("0.0.0"):
        return True, "core version unknown (source checkout) — range not enforced"
    have = _vtuple(core_version)
    for clause in _clauses(spec):
        m = _SPEC_RE.match(clause)
        if not m:
            return False, f"unparsable core_api clause {clause!r}"
        op, want_s = m.groups(); want = _vtuple(want_s)
        n = len(want_s.split("."))  # ``==0.1`` matches every 0.1.x (prefix semantics)
        ok = {">=": have >= want, "<=": have <= want, "==": have[:n] == want[:n],
              "!=": have != want, ">": have > want, "<": have < want}[op]
        if not ok:
            return False, f"core {core_version} does not satisfy core_api {spec!r}"
    return True, f"core {core_version} satisfies {spec!r}"


__all__ = [
    "CONTRACT_VERSION", "Capabilities", "ExtensionManifest", "MANIFEST_FILENAME", "ManifestError",
    "core_compatible", "load_manifest", "package_hash",
]
