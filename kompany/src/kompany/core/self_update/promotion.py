"""Upstream promotion of an approved self-update (07-24 installation role).

Three gates before anything leaves the machine, all audited by the caller:

1. **Role** — only a trusted ``maintainer`` installation may push/PR
   (:mod:`kompany.core.installation_role`). Everyone else gets a patch file
   (:func:`export_patch`) they can inspect or submit by hand.
2. **Destination** — the clone's ``origin`` must be one of the allowlisted
   Core/Pro repos, and the branch must be a ``self-update/*`` branch. No
   role can push a default branch.
3. **Credential** — a short-lived GitHub App *installation* token read from
   a root-managed file (``/etc/kompany/promotion_token`` or
   ``KOMPANY_PROMOTION_TOKEN_FILE``). Personal access tokens are refused by
   prefix. The App private key that mints these tokens never lives on the
   daemon; an operator-side job refreshes the file. When no token file
   exists, the ambient git/gh credentials of the daemon user are used only
   if the operator allowed that (``self_update_ambient_credentials``) —
   recorded as such.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from kompany.core.self_update.workspace import _git, _stderr_tail, default_branch

PROMOTION_TOKEN_ENV = "KOMPANY_PROMOTION_TOKEN_FILE"
DEFAULT_TOKEN_FILE = Path("/etc/kompany/promotion_token")
DEFAULT_ALLOWED_REPOS: tuple[str, ...] = ("Fei2-Labs/Kompany", "Fei2-Labs/kompany-pro")
PROPOSAL_BRANCH_PREFIX = "self-update/"
_APP_TOKEN_PREFIX = "ghs_"
_PAT_PREFIXES = ("ghp_", "github_pat_", "gho_", "ghu_", "ghr_")


@dataclass(frozen=True)
class PromotionCredential:
    kind: str  # "app_token" | "ambient"
    token: str | None
    fingerprint: str | None  # sha256[:12] of the token, never the token

    @property
    def http_extraheader(self) -> str | None:
        if not self.token:
            return None
        basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        return f"AUTHORIZATION: basic {basic}"


def repo_slug(origin_url: str) -> str | None:
    """``owner/repo`` from https / ssh GitHub remotes; None otherwise."""
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", origin_url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def origin_slug(clone: Path) -> str | None:
    proc = _git(clone, "remote", "get-url", "origin")
    return repo_slug(proc.stdout) if proc.returncode == 0 else None


def destination_allowed(clone: Path, branch: str, allowed_repos: tuple[str, ...] | list[str]) -> tuple[bool, str, str | None]:
    """(ok, reason, slug). Branch prefix + repo allowlist."""
    if not branch.startswith(PROPOSAL_BRANCH_PREFIX) or branch in ("main", "master"):
        return False, f"branch {branch!r} is not a {PROPOSAL_BRANCH_PREFIX}* proposal branch", None
    slug = origin_slug(clone)
    if slug is None:
        return False, "clone origin is not a GitHub repository", None
    if slug.lower() not in {r.lower() for r in allowed_repos}:
        return False, f"origin {slug} is not an allowlisted Core/Pro repo", slug
    return True, "ok", slug


def token_file_path() -> Path:
    override = os.environ.get(PROMOTION_TOKEN_ENV, "").strip()
    return Path(override).expanduser() if override else DEFAULT_TOKEN_FILE


def load_credential(*, ambient_ok: bool, path: Path | None = None) -> tuple[PromotionCredential | None, str]:
    """Read the scoped App token; fall back to ambient only when allowed."""
    p = Path(path) if path is not None else token_file_path()
    if p.exists():
        try:
            token = p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        except (OSError, IndexError):
            token = ""
        if token.startswith(_PAT_PREFIXES):
            return None, "promotion token file holds a personal access token — only GitHub App installation tokens (ghs_) are accepted"
        if not token.startswith(_APP_TOKEN_PREFIX):
            return None, "promotion token file does not hold a GitHub App installation token (ghs_)"
        return PromotionCredential("app_token", token, hashlib.sha256(token.encode()).hexdigest()[:12]), f"scoped app token from {p}"
    if ambient_ok:
        return PromotionCredential("ambient", None, None), "ambient git/gh credentials (no token file; operator allowed)"
    return None, f"no promotion token file at {p} and ambient credentials are disabled"


def push_with_credential(clone: Path, branch: str, cred: PromotionCredential) -> tuple[bool, str]:
    """Push the proposal branch. Never a default branch, whatever the token."""
    if not branch.startswith(PROPOSAL_BRANCH_PREFIX):
        return False, "refusing to push a non-proposal branch"
    args: list[str] = []
    header = cred.http_extraheader
    if header:
        args += ["-c", "credential.helper=", "-c", f"http.extraheader={header}"]
    proc = _git(clone, *args, "push", "origin", branch)
    if proc.returncode == 0:
        return True, f"pushed origin/{branch}"
    return False, _stderr_tail(proc)


def open_pull_request(clone: Path, slug: str, branch: str, title: str, body: str, cred: PromotionCredential,
                      *, post=None) -> tuple[str | None, str]:
    """Open (never merge) a PR. App token → GitHub REST; ambient → ``gh``."""
    if cred.kind == "app_token" and cred.token:
        import httpx

        poster = post or (lambda url, **kw: httpx.post(url, **kw))
        try:
            res = poster(
                f"https://api.github.com/repos/{slug}/pulls",
                json={"title": title, "body": body, "head": branch, "base": default_branch(clone)},
                headers={"Authorization": f"Bearer {cred.token}", "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 — network failure is an expected condition
            return None, f"pull request creation failed: {type(exc).__name__}"
        if res.status_code in (200, 201):
            try:
                return res.json().get("html_url"), "pr created"
            except Exception:  # noqa: BLE001
                return None, "pr created (no url in response)"
        return None, f"GitHub API {res.status_code} on pull request creation"
    from kompany.core.self_update.workspace import create_pr

    return create_pr(clone, branch, title, body)


def export_patch(clone: Path, branch: str, data_dir: Path, proposal_id: str) -> tuple[Path | None, str]:
    """Non-maintainer path: write ``<data_dir>/self_update/patches/<id>.patch``."""
    base = default_branch(clone)
    proc = _git(clone, "format-patch", "--stdout", f"{base}..{branch}")
    if proc.returncode != 0:
        return None, f"git format-patch failed: {_stderr_tail(proc)}"
    out_dir = Path(data_dir) / "self_update" / "patches"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{proposal_id}.patch"
    path.write_text(proc.stdout, encoding="utf-8")
    meta = {"proposal_id": proposal_id, "branch": branch, "base": base, "bytes": len(proc.stdout)}
    (out_dir / f"{proposal_id}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return path, f"patch exported to {path}"


__all__ = [
    "DEFAULT_ALLOWED_REPOS",
    "PROMOTION_TOKEN_ENV",
    "PROPOSAL_BRANCH_PREFIX",
    "PromotionCredential",
    "destination_allowed",
    "export_patch",
    "load_credential",
    "open_pull_request",
    "push_with_credential",
    "repo_slug",
]
