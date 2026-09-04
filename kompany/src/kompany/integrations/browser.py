"""Browser-based integrations — Core foundation for headed-CDP automation.

Many high-value channels (LinkedIn, X, Weibo, 小红书) are bot-protected and
cannot be driven via official APIs at a price a solo founder will pay. The
working pattern is a **headed** Brave on a virtual display (Xvfb), driven
over the Chrome DevTools Protocol — see ``kompany-browser/`` on the VPS for
the shared browser infrastructure.

This module is the **Core reusable layer**: four generic Tools
(``feed`` / ``engage`` / ``post`` / ``metrics``) that any browser-based Pro
Integration instantiates with its own script directory. The Pro plugin owns
the per-site DOM logic (``feed.mjs`` / ``engage.mjs`` / …) because selectors
are site-specific; Core owns the Tool contract, the side-effect/autonomy
gating, and the subprocess plumbing.

Why subprocess + node, not Python playwright-core
-------------------------------------------------
The node scripts already exist, are battle-tested against LinkedIn's
shifting DOM, and share ``lib-li.mjs`` (CDP connect + headed guards). Re-
implementing in Python would double the surface and desync the two runtimes.
The Tools shell out to ``node <script_dir>/<action>.mjs`` and parse stdout.

Side-effect / autonomy tiers
----------------------------
* ``feed`` / ``metrics`` — READ / AUTO: no state mutation, free to invoke.
* ``engage`` (like/comment) — EXTERNAL_ACTION / APPROVAL: comments are
  outward actions on the founder's account; the engine gates them.
* ``post`` — EXTERNAL_ACTION / APPROVAL: original posts are gated too.

Exit-code contract (all scripts)
--------------------------------
* 0 + stdout JSON → success.
* 3 → ``NOT_LOGGED_IN`` (session lost; the engine surfaces an alert).
* 4 → ``EMPTY`` / ``NO_COMPOSER`` / ``NO_MATCH`` (nothing to act on; not
  an error, a benign empty result).
* other non-zero → real failure; stderr surfaced in ``detail``.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from kompany.plugins.contract import (
    AutonomyTier,
    CostEstimate,
    Integration,
    SideEffect,
    Tool,
    ToolContext,
)

# Exit codes the per-integration scripts agree on (see module docstring).
_EXIT_NOT_LOGGED_IN = 3
_EXIT_EMPTY = 4


@dataclass(frozen=True)
class BrowserToolConfig:
    """Per-integration config a Pro ``BrowserIntegration`` passes into its
    Tools. Kept as a dataclass so the Tools are cheap to construct and the
    Integration can declare its paths declaratively."""

    integration_id: str
    script_dir: str
    """Absolute path to the directory holding feed.mjs / engage.mjs / …"""

    cdp_port: int = 0
    """CDP port the headed Brave for this integration listens on. Passed to
    the scripts via ``CDP_PORT`` env so they don't hardcode it."""

    node_bin: str = "node"
    node_modules_dir: str | None = None
    """Optional path to a node_modules dir; propagated as NODE_PATH so
    .mjs scripts resolve playwright-core etc. without each integration
    vendoring its own copy. The Pro LinkedIn integration points this at
    the shared node_modules the standalone worker.sh already installed."""
    extra_env: dict[str, str] = field(default_factory=dict)
    """Extra env vars merged into every script invocation (e.g. DRY=1 for
    sandboxed runs, SEARCH=<query> for engage-on-search)."""


def _run_script(
    cfg: BrowserToolConfig,
    script_name: str,
    args: list[str],
    *,
    timeout: int = 120,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run ``node <script_dir>/<script_name> <args>`` and return
    ``(returncode, stdout, stderr)``.

    ``CDP_PORT`` is injected from ``cfg`` so the script talks to the right
    headed Brave instance. ``cfg.extra_env`` + per-call ``extra_env`` merge
    on top of the current process env.
    """
    script_path = Path(cfg.script_dir) / script_name
    # Node drives a real browser on pages the agent chose: start from an
    # environment without the engine's secrets; cfg/extra_env add back only
    # what the script needs (security audit 2026-09-04).
    from kompany.core.harness.env_scrub import scrubbed_env

    env = scrubbed_env()
    if cfg.cdp_port:
        env["CDP_PORT"] = str(cfg.cdp_port)
    if cfg.node_modules_dir:
        # NODE_PATH lets .mjs scripts resolve playwright-core from a shared
        # node_modules without each integration vendoring its own copy.
        env["NODE_PATH"] = str(cfg.node_modules_dir)
    env.update(cfg.extra_env)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [cfg.node_bin, str(script_path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _map_exit_status(returncode: int, stderr: str) -> str:
    """Map the script exit code to a stable status token."""
    if returncode == 0:
        return "ok"
    if returncode == _EXIT_NOT_LOGGED_IN:
        return "NOT_LOGGED_IN"
    if returncode == _EXIT_EMPTY:
        return "EMPTY"
    return "error"


# ---------------------------------------------------------------------------
# Tool 1: feed — discover posts to engage with
# ---------------------------------------------------------------------------


class BrowserFeedInput(BaseModel):
    query: str = ""
    """Content-search query. Empty => the integration's home feed."""

    limit: int = Field(default=12, ge=1, le=50)


class BrowserFeedOutput(BaseModel):
    status: str
    """``ok`` | ``NOT_LOGGED_IN`` | ``EMPTY`` | ``error``."""

    posts: list[dict[str, Any]] = Field(default_factory=list)
    """Each post: ``{author, degree, text, idx, ...}`` (site-specific extras
    may be present; the contract guarantees ``author`` + ``idx``)."""

    detail: str = ""


class BrowserFeedTool(Tool):
    """Discover posts on a browser-based channel.

    No query → home feed. With a query → content search (where on-theme
    posts actually live for LinkedIn). Output is the raw post list the
    agent reasons over to pick engagement targets.
    """

    input_schema = BrowserFeedInput
    output_schema = BrowserFeedOutput
    side_effect = SideEffect.READ
    autonomy_tier = AutonomyTier.AUTO

    def __init__(self, cfg: BrowserToolConfig) -> None:
        self._cfg = cfg
        self.name = f"{cfg.integration_id}.feed"
        self.description = (
            f"Discover posts on {cfg.integration_id} to engage with. "
            "No query = home feed; with a query = content search "
            "(latest-first). Returns [{author, degree, text, idx}]."
        )

    def estimate_cost(self, inputs: BaseModel) -> CostEstimate:
        return CostEstimate(llm_usd=0.0, external_usd=0.0, confidence=1.0)

    def execute(self, inputs: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inputs, BrowserFeedInput)
        rc, stdout, stderr = _run_script(
            self._cfg,
            "feed.mjs",
            [str(inputs.limit), inputs.query],
        )
        status = _map_exit_status(rc, stderr)
        if status == "ok":
            try:
                posts = json.loads(stdout)
            except json.JSONDecodeError as exc:
                return BrowserFeedOutput(
                    status="error",
                    detail=f"feed JSON parse failed: {exc}; stdout={stdout[:200]}",
                )
            return BrowserFeedOutput(status="ok", posts=posts or [])
        return BrowserFeedOutput(status=status, detail=stderr.strip()[:300])


# ---------------------------------------------------------------------------
# Tool 2: engage — like / comment on a feed post
# ---------------------------------------------------------------------------


class BrowserEngageInput(BaseModel):
    target: str = Field(..., min_length=1)
    """Post index (numeric string from feed output) OR an author-name
    substring (reorder-proof against feed reshuffles)."""

    action: str = Field(..., pattern="^(like|comment)$")
    text: str = ""
    """Comment body. Required when action == 'comment'."""

    search_query: str = ""
    """If set, engage on the content-SEARCH page for this query (where
    on-theme posts live) instead of the home feed."""


class BrowserEngageOutput(BaseModel):
    status: str
    """``ok`` | ``NOT_LOGGED_IN`` | ``NO_MATCH`` | ``EMPTY`` | ``error``."""

    detail: str = ""


class BrowserEngageTool(Tool):
    """Like or comment on a feed post IN-FEED (no permalink — LinkedIn's
    current feed exposes none). Author-match is reorder-proof."""

    input_schema = BrowserEngageInput
    output_schema = BrowserEngageOutput
    side_effect = SideEffect.EXTERNAL_ACTION
    autonomy_tier = AutonomyTier.APPROVAL

    def __init__(self, cfg: BrowserToolConfig) -> None:
        self._cfg = cfg
        self.name = f"{cfg.integration_id}.engage"
        self.description = (
            f"Like or comment on a {cfg.integration_id} feed post. "
            "target = post idx (from feed) or author-name substring "
            "(reorder-proof). action = like|comment. comment needs text."
        )

    def estimate_cost(self, inputs: BaseModel) -> CostEstimate:
        return CostEstimate(llm_usd=0.0, external_usd=0.0, confidence=1.0)

    def execute(self, inputs: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inputs, BrowserEngageInput)
        if inputs.action == "comment" and not inputs.text.strip():
            return BrowserEngageOutput(
                status="error", detail="comment action requires non-empty text"
            )
        env: dict[str, str] = {}
        if inputs.search_query:
            env["SEARCH"] = inputs.search_query
        rc, stdout, stderr = _run_script(
            self._cfg,
            "engage.mjs",
            [inputs.target, inputs.action, inputs.text],
            extra_env=env,
        )
        out = stdout.strip()
        # engage.mjs prints COMMENTED / NO_MATCH / NOT_LOGGED_IN on stdout.
        # NO_MATCH is exit 0 (the script ran fine, just found nothing to act
        # on) — distinguish it from a real COMMENTED success.
        if rc == 0 and "NO_MATCH" in out:
            return BrowserEngageOutput(status="NO_MATCH", detail=out[:300])
        status = _map_exit_status(rc, stderr)
        if status == "ok":
            return BrowserEngageOutput(status="ok", detail=out[:300] or "engaged")
        return BrowserEngageOutput(status=status, detail=(out or stderr).strip()[:300])


# ---------------------------------------------------------------------------
# Tool 3: post — publish an original post
# ---------------------------------------------------------------------------


class BrowserPostInput(BaseModel):
    text: str = Field(..., min_length=1, max_length=3000)


class BrowserPostOutput(BaseModel):
    status: str
    """``ok`` | ``NOT_LOGGED_IN`` | ``NO_COMPOSER`` | ``error``."""

    detail: str = ""


class BrowserPostTool(Tool):
    """Publish an original post on the channel."""

    input_schema = BrowserPostInput
    output_schema = BrowserPostOutput
    side_effect = SideEffect.EXTERNAL_ACTION
    autonomy_tier = AutonomyTier.APPROVAL

    def __init__(self, cfg: BrowserToolConfig) -> None:
        self._cfg = cfg
        self.name = f"{cfg.integration_id}.post"
        self.description = (
            f"Publish an original post on {cfg.integration_id}. "
            "Gated — the founder approves before it ships."
        )

    def estimate_cost(self, inputs: BaseModel) -> CostEstimate:
        return CostEstimate(llm_usd=0.0, external_usd=0.0, confidence=1.0)

    def execute(self, inputs: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inputs, BrowserPostInput)
        rc, stdout, stderr = _run_script(
            self._cfg,
            "post.mjs",
            [inputs.text],
        )
        status = _map_exit_status(rc, stderr)
        out = stdout.strip()
        if status == "ok":
            return BrowserPostOutput(status="ok", detail=out[:300] or "posted")
        return BrowserPostOutput(status=status, detail=(out or stderr).strip()[:300])


# ---------------------------------------------------------------------------
# Tool 4: metrics — scrape own-account metrics
# ---------------------------------------------------------------------------


class BrowserMetricsInput(BaseModel):
    pass


class BrowserMetricsOutput(BaseModel):
    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    detail: str = ""


class BrowserMetricsTool(Tool):
    """Scrape the channel's own-account metrics (followers, profile views,
    post impressions, …) for the journal. Site-specific keys."""

    input_schema = BrowserMetricsInput
    output_schema = BrowserMetricsOutput
    side_effect = SideEffect.READ
    autonomy_tier = AutonomyTier.AUTO

    def __init__(self, cfg: BrowserToolConfig) -> None:
        self._cfg = cfg
        self.name = f"{cfg.integration_id}.metrics"
        self.description = (
            f"Scrape own-account metrics on {cfg.integration_id} "
            "(followers, profile views, post impressions, …)."
        )

    def estimate_cost(self, inputs: BaseModel) -> CostEstimate:
        return CostEstimate(llm_usd=0.0, external_usd=0.0, confidence=1.0)

    def execute(self, inputs: BaseModel, ctx: ToolContext) -> BaseModel:
        assert isinstance(inputs, BrowserMetricsInput)
        rc, stdout, stderr = _run_script(self._cfg, "metrics.mjs", [])
        status = _map_exit_status(rc, stderr)
        if status == "ok":
            try:
                metrics = json.loads(stdout)
            except json.JSONDecodeError as exc:
                return BrowserMetricsOutput(
                    status="error",
                    detail=f"metrics JSON parse failed: {exc}; stdout={stdout[:200]}",
                )
            return BrowserMetricsOutput(status="ok", metrics=metrics or {})
        return BrowserMetricsOutput(status=status, detail=stderr.strip()[:300])


# ---------------------------------------------------------------------------
# BrowserIntegration — Pro base class
# ---------------------------------------------------------------------------


class BrowserIntegration(Integration):
    """Base for headed-CDP browser integrations (LinkedIn, X, Weibo, …).

    Pro subclasses set ``integration_id``, ``display_name``, ``script_dir``,
    and ``cdp_port`` as class attributes, then inherit ``tools()`` which
    constructs the four generic Tools wired to those paths. A subclass may
    override ``tools()`` to add channel-specific Tools (e.g. a Weibo
    repost tool) — additive, never replacing the core four.
    """

    # Declared by subclasses.
    script_dir: str = ""
    cdp_port: int = 0
    node_bin: str = "node"
    node_modules_dir: str | None = None
    extra_env: dict[str, str] = {}

    def _tool_config(self) -> BrowserToolConfig:
        return BrowserToolConfig(
            integration_id=self.integration_id,
            script_dir=self.script_dir,
            cdp_port=self.cdp_port,
            node_bin=self.node_bin,
            node_modules_dir=self.node_modules_dir,
            extra_env=dict(self.extra_env),
        )

    def tools(self) -> list[Tool]:
        cfg = self._tool_config()
        return [
            BrowserFeedTool(cfg),
            BrowserEngageTool(cfg),
            BrowserPostTool(cfg),
            BrowserMetricsTool(cfg),
        ]
