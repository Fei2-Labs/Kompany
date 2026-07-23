"""Tests for the Core browser-integration Tools (integrations/browser.py).

The Tools shell out to ``node <script_dir>/<action>.mjs``. We don't need a
real headed Brave here — we point ``script_dir`` at a temp dir of fake
scripts that emit canned stdout / exit codes, and verify the Tools map
them correctly. This locks the exit-code contract (0=ok, 3=NOT_LOGGED_IN,
4=EMPTY) and the JSON-parsing / env-injection behavior.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel

from kompany.integrations.browser import (
    BrowserEngageInput,
    BrowserEngageTool,
    BrowserFeedInput,
    BrowserFeedTool,
    BrowserMetricsTool,
    BrowserPostInput,
    BrowserPostTool,
    BrowserToolConfig,
)
from kompany.plugins.contract import AutonomyTier, SideEffect


class _Ctx(BaseModel):
    """Minimal ToolContext stand-in — the browser tools don't use it yet."""


def _write_script(dir_: Path, name: str, body: str) -> None:
    path = dir_ / name
    path.write_text(body)
    path.chmod(0o755)


def _cfg(tmp: Path, **kw) -> BrowserToolConfig:
    return BrowserToolConfig(
        integration_id="test_chan",
        script_dir=str(tmp),
        node_bin="bash",  # the fake scripts are bash, not node — avoids node dep
        **kw,
    )


def test_feed_ok_parses_json(tmp_path: Path):
    _write_script(
        tmp_path,
        "feed.mjs",
        """#!/bin/bash
echo '[{"author":"A","degree":"3rd","text":"hi","idx":0}]'
exit 0
""",
    )
    tool = BrowserFeedTool(_cfg(tmp_path))
    out = tool.execute(BrowserFeedInput(), _Ctx())
    assert out.status == "ok"
    assert out.posts == [{"author": "A", "degree": "3rd", "text": "hi", "idx": 0}]


def test_feed_not_logged_in_exit3(tmp_path: Path):
    _write_script(
        tmp_path,
        "feed.mjs",
        """#!/bin/bash
echo 'NOT_LOGGED_IN' >&2
exit 3
""",
    )
    tool = BrowserFeedTool(_cfg(tmp_path))
    out = tool.execute(BrowserFeedInput(), _Ctx())
    assert out.status == "NOT_LOGGED_IN"
    assert out.posts == []


def test_feed_empty_exit4(tmp_path: Path):
    _write_script(tmp_path, "feed.mjs", "#!/bin/bash\nexit 4\n")
    tool = BrowserFeedTool(_cfg(tmp_path))
    out = tool.execute(BrowserFeedInput(), _Ctx())
    assert out.status == "EMPTY"


def test_feed_error_nonzero(tmp_path: Path):
    _write_script(
        tmp_path,
        "feed.mjs",
        """#!/bin/bash
echo 'boom' >&2
exit 2
""",
    )
    tool = BrowserFeedTool(_cfg(tmp_path))
    out = tool.execute(BrowserFeedInput(), _Ctx())
    assert out.status == "error"
    assert "boom" in out.detail


def test_feed_passes_query_and_limit_as_args(tmp_path: Path):
    _write_script(
        tmp_path,
        "feed.mjs",
        """#!/bin/bash
echo "ARGS=$@" >> {log}
echo '[]'
""".format(log=tmp_path / "args.log"),
    )
    tool = BrowserFeedTool(_cfg(tmp_path))
    tool.execute(BrowserFeedInput(query="AI PM", limit=14), _Ctx())
    log = (tmp_path / "args.log").read_text()
    assert "14 AI PM" in log


def test_feed_injects_cdp_port_env(tmp_path: Path):
    _write_script(
        tmp_path,
        "feed.mjs",
        """#!/bin/bash
echo "CDP_PORT=$CDP_PORT" >> {log}
echo '[]'
""".format(log=tmp_path / "env.log"),
    )
    tool = BrowserFeedTool(_cfg(tmp_path, cdp_port=9335))
    tool.execute(BrowserFeedInput(), _Ctx())
    assert "CDP_PORT=9335" in (tmp_path / "env.log").read_text()


def test_engage_comment_requires_text(tmp_path: Path):
    _write_script(tmp_path, "engage.mjs", "#!/bin/bash\necho COMMENTED\nexit 0\n")
    tool = BrowserEngageTool(_cfg(tmp_path))
    out = tool.execute(
        BrowserEngageInput(target="0", action="comment", text="   "), _Ctx()
    )
    assert out.status == "error"
    assert "text" in out.detail.lower()


def test_engage_ok(tmp_path: Path):
    _write_script(tmp_path, "engage.mjs", "#!/bin/bash\necho COMMENTED\nexit 0\n")
    tool = BrowserEngageTool(_cfg(tmp_path))
    out = tool.execute(
        BrowserEngageInput(target="Helen V.", action="comment", text="good point"),
        _Ctx(),
    )
    assert out.status == "ok"
    assert "COMMENTED" in out.detail


def test_engage_no_match(tmp_path: Path):
    _write_script(
        tmp_path,
        "engage.mjs",
        """#!/bin/bash
echo 'NO_MATCH'
exit 0
""",
    )
    tool = BrowserEngageTool(_cfg(tmp_path))
    out = tool.execute(
        BrowserEngageInput(target="nobody", action="like"), _Ctx()
    )
    assert out.status == "NO_MATCH"


def test_engage_search_env_passed(tmp_path: Path):
    _write_script(
        tmp_path,
        "engage.mjs",
        """#!/bin/bash
echo "SEARCH=$SEARCH" >> {log}
echo COMMENTED
""".format(log=tmp_path / "s.log"),
    )
    tool = BrowserEngageTool(_cfg(tmp_path))
    tool.execute(
        BrowserEngageInput(
            target="0", action="like", search_query="agentic AI delivery"
        ),
        _Ctx(),
    )
    assert "SEARCH=agentic AI delivery" in (tmp_path / "s.log").read_text()


def test_post_ok(tmp_path: Path):
    _write_script(tmp_path, "post.mjs", "#!/bin/bash\necho POSTED\nexit 0\n")
    tool = BrowserPostTool(_cfg(tmp_path))
    out = tool.execute(BrowserPostInput(text="hello world"), _Ctx())
    assert out.status == "ok"


def test_post_no_composer_exit4(tmp_path: Path):
    _write_script(tmp_path, "post.mjs", "#!/bin/bash\nexit 4\n")
    tool = BrowserPostTool(_cfg(tmp_path))
    out = tool.execute(BrowserPostInput(text="x"), _Ctx())
    assert out.status == "NO_COMPOSER" or out.status == "EMPTY"


def test_metrics_ok_parses_json(tmp_path: Path):
    _write_script(
        tmp_path,
        "metrics.mjs",
        """#!/bin/bash
echo '{"followers":"530","profileViews":"117"}'
""",
    )
    tool = BrowserMetricsTool(_cfg(tmp_path))
    out = tool.execute(_ctx_input(), _Ctx())
    assert out.status == "ok"
    assert out.metrics["followers"] == "530"


def _ctx_input():
    from kompany.integrations.browser import BrowserMetricsInput

    return BrowserMetricsInput()


def test_side_effect_and_autonomy_tiers():
    cfg = BrowserToolConfig(integration_id="x", script_dir="/tmp", node_bin="true")
    assert BrowserFeedTool(cfg).side_effect == SideEffect.READ
    assert BrowserFeedTool(cfg).autonomy_tier == AutonomyTier.AUTO
    assert BrowserEngageTool(cfg).side_effect == SideEffect.EXTERNAL_ACTION
    assert BrowserEngageTool(cfg).autonomy_tier == AutonomyTier.APPROVAL
    assert BrowserPostTool(cfg).side_effect == SideEffect.EXTERNAL_ACTION
    assert BrowserPostTool(cfg).autonomy_tier == AutonomyTier.APPROVAL
    assert BrowserMetricsTool(cfg).side_effect == SideEffect.READ
    assert BrowserMetricsTool(cfg).autonomy_tier == AutonomyTier.AUTO


def test_tool_names_are_integration_scoped(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert BrowserFeedTool(cfg).name == "test_chan.feed"
    assert BrowserEngageTool(cfg).name == "test_chan.engage"
    assert BrowserPostTool(cfg).name == "test_chan.post"
    assert BrowserMetricsTool(cfg).name == "test_chan.metrics"
