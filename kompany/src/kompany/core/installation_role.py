"""Installation role — who this instance is allowed to be (07-24 task).

Answers "how does the agent know it is the maintainer's own instance and
may open a PR against Core/Pro?" with a static, operator-set, tamper-proof
value the agent cannot change — never by inferring identity from usage.

The role lives in a file the daemon cannot write: ``/etc/kompany/
installation_role`` (or ``KOMPANY_INSTALLATION_ROLE_FILE``), owned by root
(or by any user other than the daemon user) and not group/world-writable.
A missing, unreadable, malformed or *untrusted* file resolves to
``customer`` — the least-privileged role — with the reason recorded so the
operator can see why. There is no tool, REST route or setting that writes
the file; ``kompany daemon install --role`` (an operator action under
sudo) is the only in-tree writer.

Roles: ``customer`` (default) and ``contributor`` produce local patch
artifacts only; ``maintainer`` may push a ``self-update/*`` branch and open
a PR. No role can push a default branch or merge.
"""

from __future__ import annotations

import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

ROLES: tuple[str, ...] = ("customer", "contributor", "maintainer")
DEFAULT_ROLE = "customer"
DEFAULT_ROLE_FILE = Path("/etc/kompany/installation_role")
ROLE_FILE_ENV = "KOMPANY_INSTALLATION_ROLE_FILE"


@dataclass(frozen=True)
class InstallationRole:
    role: str
    source: str  # "file" | "default"
    path: str | None
    trusted: bool
    reason: str

    @property
    def can_promote(self) -> bool:
        return self.role == "maintainer" and self.trusted

    def as_dict(self) -> dict:
        d = asdict(self)
        d["can_promote"] = self.can_promote
        return d


def role_file_path() -> Path:
    override = os.environ.get(ROLE_FILE_ENV, "").strip()
    return Path(override).expanduser() if override else DEFAULT_ROLE_FILE


def _owner_trusted(st: os.stat_result) -> tuple[bool, str]:
    """Root-owned, or owned by someone other than the daemon user; never
    writable by group/others. Running as root trusts root-owned files."""
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False, "role file is group/world-writable"
    euid = os.geteuid() if hasattr(os, "geteuid") else 0
    if st.st_uid == 0:
        return True, "root-owned"
    if euid != 0 and st.st_uid != euid:
        return True, f"owned by uid {st.st_uid} (not the daemon user)"
    return False, "role file is owned by the daemon user — it could rewrite its own role"


def resolve_installation_role(path: Path | None = None, *, require_privileged_owner: bool = True) -> InstallationRole:
    """Read + validate the role file. Never raises; untrusted → customer."""
    p = Path(path) if path is not None else role_file_path()
    try:
        st = p.stat()
    except OSError:
        return InstallationRole(DEFAULT_ROLE, "default", str(p), True, "no role file — default customer")
    if not stat.S_ISREG(st.st_mode):
        return InstallationRole(DEFAULT_ROLE, "default", str(p), False, "role file is not a regular file")
    try:
        raw = p.read_text(encoding="utf-8").strip().lower()
    except OSError as exc:
        return InstallationRole(DEFAULT_ROLE, "default", str(p), False, f"role file unreadable: {exc}")
    value = raw.splitlines()[0].strip() if raw else ""
    if value not in ROLES:
        return InstallationRole(DEFAULT_ROLE, "default", str(p), False,
                                f"role file holds {value!r}; expected one of {', '.join(ROLES)}")
    if require_privileged_owner:
        ok, why = _owner_trusted(st)
        if not ok:
            return InstallationRole(DEFAULT_ROLE, "default", str(p), False, why + " — treated as customer")
    else:
        why = "owner check skipped"
    return InstallationRole(value, "file", str(p), True, why)


def write_role_file(role: str, path: Path | None = None) -> Path:
    """Operator-side writer (``kompany daemon install --role``). Requires
    privileges to write the target directory; the daemon never calls it."""
    if role not in ROLES:
        raise ValueError(f"invalid installation role {role!r}; expected one of {', '.join(ROLES)}")
    p = Path(path) if path is not None else role_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(role + "\n", encoding="utf-8")
    os.chmod(p, 0o644)
    return p


__all__ = [
    "DEFAULT_ROLE",
    "DEFAULT_ROLE_FILE",
    "ROLES",
    "ROLE_FILE_ENV",
    "InstallationRole",
    "resolve_installation_role",
    "role_file_path",
    "write_role_file",
]
