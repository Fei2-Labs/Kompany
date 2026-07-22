"""Edge CDP browser tools for the agentic loop (06-16 P4).

The founder's browser is driven over the Chrome DevTools Protocol against an
already-running Edge instance exposing CDP at ``http://127.0.0.1:9223`` (the
established Kompany pattern — reuse the logged-in profile, never spawn a fresh
headless context). Python Playwright's ``connect_over_cdp`` attaches to it.

Design constraints:

- ``playwright`` is an OPTIONAL dependency (``[browser]`` extra). Imports are
  guarded; when Playwright or Edge-on-9223 is absent the tools degrade to a
  clear observation and NEVER crash the loop.
- A single reusable :class:`BrowserSession` is kept on the :class:`ToolContext`
  for the whole chat run and closed at the end (``close_browser_session``).
- Classification: navigate / read / find / screenshot = ``READ`` (inline);
  click / type = ``EXTERNAL_ACTION`` (gated — operating the founder's
  logged-in accounts is a side effect that routes through ``propose_action``).
"""

from __future__ import annotations

from typing import Any

from kompany.core.agent_tools.base import FunctionTool, SideEffect, ToolContext

__all__ = [
    "browser_tools",
    "close_browser_session",
    "CDP_ENDPOINT",
    "BrowserSession",
]

CDP_ENDPOINT = "http://127.0.0.1:9223"
_SESSION_KEY = "browser_session"
MAX_TEXT_CHARS = 8000


def _playwright_available() -> bool:
    """Whether the optional ``playwright`` package is importable."""
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — optional dep, import is best-effort
        return False


class BrowserSession:
    """A lazily-attached Playwright browser session for one chat run.

    Two modes, tried in order:

    1. **CDP attach** — if a browser is listening on :data:`CDP_ENDPOINT`
       (the founder's Edge/Chrome with ``--remote-debugging-port=9223``),
       attach to it and reuse the logged-in profile. This is the preferred
       mode on the founder's desktop where their real browser runs.
    2. **Headless launch** — if no CDP endpoint responds, launch a headless
       Chromium. This is the mode on a VPS / server with no desktop browser.
       No login sessions, but full public-web browsing capability.

    Every method returns ``(page_or_None, error_or_None)`` style only through
    :meth:`page`; tool bodies translate ``error`` into an observation. Nothing
    here raises into the loop.
    """

    def __init__(self, endpoint: str = CDP_ENDPOINT) -> None:
        self.endpoint = endpoint
        self._pw = None
        self._browser = None
        self._connected = False
        self._is_headless = False

    def _connect(self) -> str | None:
        """Attach to a browser. Returns an error string or ``None`` on success."""
        if self._connected:
            return None
        if not _playwright_available():
            return (
                "browser unavailable: the optional 'playwright' package is not "
                "installed. Ask the founder to install Kompany's [browser] "
                "extra. (No action taken.)"
            )
        from playwright.sync_api import sync_playwright

        try:
            self._pw = sync_playwright().start()
        except Exception as exc:  # noqa: BLE001
            self._cleanup()
            return f"browser unavailable: playwright failed to start ({exc})."

        # Try CDP attach first (founder's real browser with login sessions).
        try:
            self._browser = self._pw.chromium.connect_over_cdp(self.endpoint)
            self._connected = True
            self._is_headless = False
            return None
        except Exception:  # noqa: BLE001 — no Edge on 9223, try headless
            pass

        # Fall back to headless Chromium (VPS / no desktop browser).
        try:
            self._browser = self._pw.chromium.launch(headless=True)
            self._connected = True
            self._is_headless = True
            return None
        except Exception as exc:  # noqa: BLE001
            self._cleanup()
            return (
                f"browser unavailable: could not attach to CDP at "
                f"{self.endpoint} nor launch headless Chromium "
                f"({type(exc).__name__}: {exc}). (No action taken.)"
            )

    def page(self):
        """Return ``(page, None)`` or ``(None, error_observation)``."""
        err = self._connect()
        if err is not None:
            return None, err
        try:
            if self._is_headless:
                # Headless: always create a fresh context + page.
                ctx = self._browser.new_context()
                page = ctx.new_page()
            else:
                # CDP: reuse the founder's existing context + page.
                contexts = self._browser.contexts
                ctx = contexts[0] if contexts else self._browser.new_context()
                pages = ctx.pages
                page = pages[0] if pages else ctx.new_page()
            return page, None
        except Exception as exc:  # noqa: BLE001
            return None, (
                f"browser error: could not obtain a page "
                f"({type(exc).__name__}: {exc})."
            )

    def close(self) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        # CDP attach must NOT close the founder's Edge — only detach.
        # Headless launch: close the browser we created.
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = None
        self._browser = None
        self._connected = False
        self._is_headless = False


def _session(ctx: ToolContext) -> BrowserSession:
    """Get-or-create the per-run browser session stored on the ctx."""
    sess = ctx.extra.get(_SESSION_KEY)
    if sess is None:
        sess = BrowserSession()
        ctx.extra[_SESSION_KEY] = sess
    return sess


def close_browser_session(ctx: ToolContext) -> None:
    """Close the chat run's browser session, if any. Idempotent."""
    sess = ctx.extra.pop(_SESSION_KEY, None)
    if sess is not None:
        sess.close()


def _cap(text: str) -> str:
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return text[:MAX_TEXT_CHARS] + f"\n... [truncated, {len(text)} chars total]"


# --------------------------------------------------------------------- #
# tool bodies — each returns an observation string, never raises
# --------------------------------------------------------------------- #

def _navigate(args: dict[str, Any], ctx: ToolContext) -> str:
    url = str(args.get("url", "")).strip()
    if not url:
        return "ERROR: browser_navigate requires a 'url'."
    if not url.startswith(("http://", "https://")):
        return f"ERROR: url {url!r} must start with http:// or https://."
    page, err = _session(ctx).page()
    if err:
        return err
    try:
        page.goto(url, wait_until="domcontentloaded")
        return f"navigated to {page.url} (title: {page.title()!r})"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: browser_navigate failed for {url}: {type(exc).__name__}: {exc}"


def _read(args: dict[str, Any], ctx: ToolContext) -> str:
    page, err = _session(ctx).page()
    if err:
        return err
    try:
        text = page.inner_text("body")
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: browser_read failed: {type(exc).__name__}: {exc}"
    return _cap(f"PAGE {page.url}\n\n{text}")


def _find(args: dict[str, Any], ctx: ToolContext) -> str:
    target = str(args.get("selector") or args.get("text") or "").strip()
    if not target:
        return "ERROR: browser_find requires a 'selector' or 'text'."
    page, err = _session(ctx).page()
    if err:
        return err
    try:
        if args.get("text"):
            loc = page.get_by_text(target)
        else:
            loc = page.locator(target)
        count = loc.count()
        if count == 0:
            return f"browser_find: no element matched {target!r}."
        snippets = []
        for i in range(min(count, 5)):
            try:
                snippets.append(loc.nth(i).inner_text().strip()[:120])
            except Exception:  # noqa: BLE001
                snippets.append("(no text)")
        return f"browser_find: {count} match(es) for {target!r}: " + " | ".join(snippets)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: browser_find failed: {type(exc).__name__}: {exc}"


def _click(args: dict[str, Any], ctx: ToolContext) -> str:
    target = str(args.get("target") or args.get("selector") or "").strip()
    if not target:
        return "ERROR: browser_click requires a 'target' selector."
    page, err = _session(ctx).page()
    if err:
        return err
    try:
        page.click(target)
        return f"clicked {target!r} — page now {page.url}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: browser_click failed for {target!r}: {type(exc).__name__}: {exc}"


def _type(args: dict[str, Any], ctx: ToolContext) -> str:
    target = str(args.get("target") or args.get("selector") or "").strip()
    text = str(args.get("text", ""))
    if not target:
        return "ERROR: browser_type requires a 'target' selector."
    page, err = _session(ctx).page()
    if err:
        return err
    try:
        page.fill(target, text)
        return f"typed {len(text)} chars into {target!r}."
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: browser_type failed for {target!r}: {type(exc).__name__}: {exc}"


def _screenshot(args: dict[str, Any], ctx: ToolContext) -> str:
    page, err = _session(ctx).page()
    if err:
        return err
    path = str(args.get("path", "")).strip()
    try:
        if path:
            page.screenshot(path=path)
            return f"screenshot of {page.url} saved to {path}."
        data = page.screenshot()
        return f"screenshot of {page.url} captured ({len(data)} bytes, not saved)."
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: browser_screenshot failed: {type(exc).__name__}: {exc}"


# --------------------------------------------------------------------- #
# tool factories
# --------------------------------------------------------------------- #

def browser_navigate_tool() -> FunctionTool:
    return FunctionTool(
        name="browser_navigate",
        description=(
            "Navigate the founder's logged-in Edge browser (via CDP) to a URL "
            "and return the page title. Read-only navigation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL."},
            },
            "required": ["url"],
        },
        side_effect=SideEffect.READ,
        func=_navigate,
    )


def browser_read_tool() -> FunctionTool:
    return FunctionTool(
        name="browser_read",
        description=(
            "Read the visible text of the current page in the founder's Edge "
            "browser. Read-only."
        ),
        parameters={"type": "object", "properties": {}},
        side_effect=SideEffect.READ,
        func=_read,
    )


def browser_find_tool() -> FunctionTool:
    return FunctionTool(
        name="browser_find",
        description=(
            "Find elements on the current page by CSS 'selector' or by visible "
            "'text'. Returns match count and snippets. Read-only."
        ),
        parameters={
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector."},
                "text": {"type": "string", "description": "Visible text to match."},
            },
        },
        side_effect=SideEffect.READ,
        func=_find,
    )


def browser_click_tool() -> FunctionTool:
    return FunctionTool(
        name="browser_click",
        description=(
            "Click an element (CSS 'target' selector) in the founder's "
            "logged-in browser. SIDE EFFECT — operates real signed-in "
            "accounts; routed to the founder for approval."
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "CSS selector to click."},
            },
            "required": ["target"],
        },
        side_effect=SideEffect.EXTERNAL_ACTION,
        func=_click,
    )


def browser_type_tool() -> FunctionTool:
    return FunctionTool(
        name="browser_type",
        description=(
            "Type text into a form field (CSS 'target' selector) in the "
            "founder's logged-in browser. SIDE EFFECT — operates real "
            "signed-in accounts; routed to the founder for approval."
        ),
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "CSS selector of the field."},
                "text": {"type": "string", "description": "Text to type."},
            },
            "required": ["target", "text"],
        },
        side_effect=SideEffect.EXTERNAL_ACTION,
        func=_type,
    )


def browser_screenshot_tool() -> FunctionTool:
    return FunctionTool(
        name="browser_screenshot",
        description=(
            "Capture a screenshot of the current page (optional 'path' to "
            "save). Read-only."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Optional file path to save."},
            },
        },
        side_effect=SideEffect.READ,
        func=_screenshot,
    )


def browser_tools() -> list[FunctionTool]:
    """All Edge-CDP browser tools (4 READ + 2 EXTERNAL_ACTION)."""
    return [
        browser_navigate_tool(),
        browser_read_tool(),
        browser_find_tool(),
        browser_screenshot_tool(),
        browser_click_tool(),
        browser_type_tool(),
    ]
