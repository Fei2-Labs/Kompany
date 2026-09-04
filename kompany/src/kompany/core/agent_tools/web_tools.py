"""Read-only web tools for the agentic loop (06-16 P1).

``web_fetch`` — httpx GET, strip HTML to text, cap length. No key.
``web_search`` — env-keyed Tavily provider when configured; a graceful
"search unconfigured" observation otherwise (never crashes, never a hard
key requirement).

Both are ``SideEffect.READ`` so the registry runs them inline.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

import httpx

from kompany.core.agent_tools.base import FunctionTool, SideEffect, ToolContext
from kompany.core.agent_tools.net_guard import BlockedURL, fetch_with_guard

__all__ = ["web_tools", "web_fetch_tool", "web_search_tool"]

MAX_TEXT_CHARS = 8000
HTTP_TIMEOUT_SECONDS = 20.0
TAVILY_ENDPOINT = "https://api.tavily.com/search"


class _TextExtractor(HTMLParser):
    """Collect visible text, dropping script/style content."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        joined = " ".join(self._chunks)
        return re.sub(r"\s+", " ", joined).strip()


def _strip_html(body: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(body)
    except Exception:  # noqa: BLE001 — malformed HTML, best-effort
        pass
    return parser.text()


def _cap(text: str) -> str:
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return text[:MAX_TEXT_CHARS] + f"\n... [truncated, {len(text)} chars total]"


def _web_fetch(args: dict[str, Any], ctx: ToolContext) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        return "ERROR: web_fetch requires a 'url'."
    if not url.startswith(("http://", "https://")):
        return f"ERROR: url {url!r} must start with http:// or https://."
    try:
        # SSRF guard: public hosts only, every redirect hop re-checked.
        resp = fetch_with_guard(
            url,
            client_get=httpx.get,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": "Kompany/agentic-fetch"},
        )
    except BlockedURL as exc:
        return f"ERROR: web_fetch refused {url}: {exc}"
    except httpx.HTTPError as exc:
        return f"ERROR: web_fetch failed for {url}: {type(exc).__name__}: {exc}"
    ctype = resp.headers.get("content-type", "")
    body = resp.text
    text = _strip_html(body) if "html" in ctype.lower() else body
    return _cap(
        f"GET {url} -> {resp.status_code} ({ctype or 'unknown'})\n\n{text}"
    )


def _search_api_key(ctx: ToolContext) -> str:
    settings = ctx.settings
    if settings is None:
        return ""
    return (getattr(settings, "tavily_api_key", "") or "").strip()


def _web_search(args: dict[str, Any], ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "ERROR: web_search requires a 'query'."
    key = _search_api_key(ctx)
    if not key:
        return (
            "web_search unconfigured: no TAVILY_API_KEY set. Use web_fetch "
            "with a known URL, or ask the founder to connect a search "
            "provider. (No action taken.)"
        )
    try:
        resp = httpx.post(
            TAVILY_ENDPOINT,
            timeout=HTTP_TIMEOUT_SECONDS,
            json={
                "api_key": key,
                "query": query,
                "max_results": int(args.get("max_results", 5) or 5),
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return f"ERROR: web_search failed: {type(exc).__name__}: {exc}"
    results = data.get("results", []) or []
    if not results:
        return f"web_search: no results for {query!r}."
    lines = [f"web_search results for {query!r}:"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        url = r.get("url", "")
        snippet = (r.get("content", "") or "")[:300]
        lines.append(f"{i}. {title}\n   {url}\n   {snippet}")
    return _cap("\n".join(lines))


def web_fetch_tool() -> FunctionTool:
    return FunctionTool(
        name="web_fetch",
        description=(
            "Fetch a URL over HTTP GET and return its text (HTML stripped, "
            "length-capped). Read-only, no API key needed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL."},
            },
            "required": ["url"],
        },
        side_effect=SideEffect.READ,
        func=_web_fetch,
    )


def web_search_tool() -> FunctionTool:
    return FunctionTool(
        name="web_search",
        description=(
            "Search the web for a query and return ranked results "
            "(title/url/snippet). Requires a configured search provider; "
            "returns a clear notice when unconfigured."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "max_results": {"type": "integer", "description": "Default 5."},
            },
            "required": ["query"],
        },
        side_effect=SideEffect.READ,
        func=_web_search,
    )


def web_tools() -> list[FunctionTool]:
    return [web_fetch_tool(), web_search_tool()]
