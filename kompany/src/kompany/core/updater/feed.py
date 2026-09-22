"""GitHub Releases as the only update source (allowlisted repos)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPOS: dict[str, str] = {"kompany": "Fei2-Labs/Kompany", "kompany-pro": "Fei2-Labs/kompany-pro"}
API = "https://api.github.com"
_WHEEL_RE = {"kompany": re.compile(r"^kompany-(\d[\w.]*)-py3-none-any\.whl$"),
             "kompany-pro": re.compile(r"^kompany_pro-(\d[\w.]*)-py3-none-any\.whl$")}


class FeedError(RuntimeError):
    """Release feed unreachable, malformed, or failed verification."""


@dataclass
class ReleaseInfo:
    package: str
    repo: str
    tag: str
    version: str
    html_url: str
    published_at: str | None
    assets: dict[str, str] = field(default_factory=dict)  # name → api asset url
    wheel_name: str | None = None
    manifest: dict[str, Any] | None = None
    token_warning: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"package": self.package, "repo": self.repo, "tag": self.tag, "version": self.version,
                "html_url": self.html_url, "published_at": self.published_at, "wheel": self.wheel_name,
                "release_digest": (self.manifest or {}).get("release_digest"),
                "commit": (self.manifest or {}).get("commit"),
                "token_warning": self.token_warning}


def parse_version(text: str) -> tuple[int, ...]:
    core = re.split(r"[-+]", str(text).lstrip("v"), maxsplit=1)[0]
    parts = tuple(int(x) for x in core.split(".") if x.isdigit())
    return parts + (0,) * (3 - len(parts))


# Scopes a classic PAT may carry that let it do more than read a release
# asset. The entitlement contract for the Pro feed is read-only and
# short-lived (07-24 Stage C step 5), so anything write-capable is flagged.
_BROAD_SCOPES = {"repo", "workflow", "write:packages", "delete:packages", "admin:org", "delete_repo",
                 "admin:repo_hook", "write:org", "user", "gist"}
_MAX_TOKEN_DAYS = 90


def token_warning(headers: Any) -> str | None:
    """Judge the Pro token from the response headers of a call we already make.

    GitHub echoes a classic PAT's scopes in ``X-OAuth-Scopes`` and a
    fine-grained PAT's expiry in
    ``github-authentication-token-expiration``. A fine-grained,
    contents:read, expiring token — the shape the entitlement contract
    wants — reports an empty scope list and an expiry, so it warns about
    nothing. Advisory only: a broad token still works, it just says so.
    """
    def _h(name: str) -> str:
        try:
            return str(headers.get(name) or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    scopes = {s.strip() for s in _h("X-OAuth-Scopes").split(",") if s.strip()}
    broad = sorted(scopes & _BROAD_SCOPES)
    expires = _h("github-authentication-token-expiration")
    notes: list[str] = []
    if broad:
        notes.append(f"it is a classic token with write scopes ({', '.join(broad)})")
    if not expires:
        notes.append("it never expires")
    else:
        from datetime import UTC, datetime
        try:
            # e.g. "2026-12-31 23:59:59 +0100" or an ISO timestamp.
            text = expires.replace(" UTC", "+0000")
            when = datetime.fromisoformat(text)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            days = (when - datetime.now(UTC)).days
            if days > _MAX_TOKEN_DAYS:
                notes.append(f"it is valid for another {days} days")
        except ValueError:
            pass
    if not notes:
        return None
    return (f"the {'; '.join(notes)} — the Pro release token should be a fine-grained token with "
            "read-only Contents access to Fei2-Labs/kompany-pro and an expiry, so a leak cannot write anywhere")


def _headers(token: str | None, accept: str = "application/vnd.github+json") -> dict[str, str]:
    h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "kompany-updater"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(url: str, *, token: str | None, accept: str, fetch: Callable[..., Any] | None, **kw: Any) -> Any:
    """GET through the SSRF guard (redirect hops re-checked); ``fetch`` is httpx.get-compatible."""
    import httpx

    from kompany.core.agent_tools.net_guard import fetch_with_guard

    client_get = fetch or httpx.get
    resp = fetch_with_guard(url, client_get=client_get, headers=_headers(token, accept), timeout=60, **kw)
    if resp.status_code != 200:
        raise FeedError(f"GET {url.split('?')[0]} → {resp.status_code}")
    return resp


def latest_release(package: str, *, token: str | None = None, fetch: Callable[..., Any] | None = None) -> ReleaseInfo:
    """Latest release of an allowlisted package, with its manifest loaded."""
    repo = REPOS.get(package)
    if repo is None:
        raise FeedError(f"{package!r} is not an allowlisted package ({sorted(REPOS)})")
    resp = _get(f"{API}/repos/{repo}/releases/latest", token=token, accept="application/vnd.github+json", fetch=fetch)
    warn = token_warning(getattr(resp, "headers", {})) if token else None
    data = resp.json()
    tag = str(data.get("tag_name") or "")
    if not tag:
        raise FeedError(f"{repo}: release has no tag")
    assets = {a["name"]: a["url"] for a in data.get("assets", []) if a.get("name") and a.get("url")}
    wheel = next((n for n in assets if _WHEEL_RE[package].match(n)), None)
    info = ReleaseInfo(package=package, repo=repo, tag=tag, version=tag.lstrip("v"), html_url=str(data.get("html_url") or ""),
                       published_at=data.get("published_at"), assets=assets, wheel_name=wheel, token_warning=warn)
    if "release-manifest.json" in assets:
        text = _get(assets["release-manifest.json"], token=token, accept="application/octet-stream", fetch=fetch).text
        try:
            info.manifest = json.loads(text)
        except ValueError as exc:
            raise FeedError(f"{repo}: release-manifest.json is not JSON") from exc
    return info


def download_asset(info: ReleaseInfo, name: str, dest_dir: Path, *, token: str | None = None,
                   fetch: Callable[..., Any] | None = None) -> Path:
    """Download one asset and verify it against the manifest sha256 (required)."""
    url = info.assets.get(name)
    if not url:
        raise FeedError(f"{info.repo} {info.tag}: asset {name!r} not found")
    expected = ((info.manifest or {}).get("artifacts") or {}).get(name, {}).get("sha256")
    if not expected:
        raise FeedError(f"{info.repo} {info.tag}: manifest carries no sha256 for {name} — refusing unverified install")
    resp = _get(url, token=token, accept="application/octet-stream", fetch=fetch)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / name
    path.write_bytes(resp.content)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected.lower():
        path.unlink(missing_ok=True)
        raise FeedError(f"{name}: sha256 mismatch (manifest {expected[:12]}, got {actual[:12]})")
    return path


def verify_attestation(path: Path, repo: str, *, run: Callable[..., Any] | None = None) -> dict[str, Any]:
    """`gh attestation verify` when gh is installed; skipped (not failed) otherwise."""
    gh = shutil.which("gh")
    if gh is None:
        return {"status": "skipped", "detail": "gh CLI not installed — sha256 verified, provenance not checked"}
    runner = run or subprocess.run
    try:
        proc = runner([gh, "attestation", "verify", str(path), "--repo", repo], capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001
        return {"status": "skipped", "detail": f"gh attestation verify could not run: {type(exc).__name__}"}
    if getattr(proc, "returncode", 1) == 0:
        return {"status": "verified", "detail": (proc.stdout or "").strip()[-300:]}
    return {"status": "failed", "detail": ((proc.stderr or proc.stdout or "").strip())[-300:]}


__all__ = ["API", "REPOS", "FeedError", "ReleaseInfo", "download_asset", "latest_release", "parse_version",
           "token_warning", "verify_attestation"]
