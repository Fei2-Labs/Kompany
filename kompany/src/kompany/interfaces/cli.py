"""Kompany CLI — the primary interface."""

from __future__ import annotations

import time

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from kompany.core.debate import DebateEngine

from kompany.interfaces.cli_parts.common import (  # noqa: E402
    app,
    console,
)


def _get_engine(config: str | None = None):
    from kompany.core.engine import KompanyEngine
    return KompanyEngine(config_path=config)


def _emit_json(data):
    console.print_json(data=data)


# Command modules register themselves on ``app`` at import time. Import
# order preserves the original cli.py registration order (help listing).
from kompany.interfaces import cli_parts  # noqa: E402,F401
from kompany.interfaces.cli_parts import (  # noqa: E402,F401
    core as _cli_core,
    control as _cli_control,
    projects as _cli_projects,
    approvals as _cli_approvals,
    runtime as _cli_runtime,
    knowledge as _cli_knowledge,
    model_source as _cli_model_source,
    portability as _cli_portability,
)
# auth sub-app (06-16-agentic-chat-engine P3). The module registers itself
# on the shared ``app`` at import (like model_source); just import it.
from kompany.interfaces.cli_parts import auth as _cli_auth  # noqa: E402,F401

# Daemon sub-app (06-12-daemon-tick-loop PR2). Lives in cli_daemon.py —
# cli.py is over the file-size cap, new command groups go in siblings.
from kompany.interfaces.cli_anima import anima_app  # noqa: E402
from kompany.interfaces.cli_channels import channels_app  # noqa: E402
from kompany.interfaces.cli_daemon import daemon_app  # noqa: E402
from kompany.interfaces.cli_extensions import extensions_app  # noqa: E402
from kompany.interfaces.cli_founder import (  # noqa: E402
    founder_app,
    outward_policy_app,
)
from kompany.interfaces.cli_self_update import self_update_app  # noqa: E402
from kompany.interfaces.cli_workspace import workspace_app  # noqa: E402

app.add_typer(anima_app, name="anima")
app.add_typer(channels_app, name="channels")
app.add_typer(daemon_app, name="daemon")
app.add_typer(extensions_app, name="extensions")
app.add_typer(founder_app, name="founder")
app.add_typer(outward_policy_app, name="outward-policy")
app.add_typer(self_update_app, name="self-update")
app.add_typer(workspace_app, name="workspace")

from kompany.interfaces.cli_parts import (  # noqa: E402,F401
    ops as _cli_ops,
    misc as _cli_misc,
    workflows as _cli_workflows,
)
