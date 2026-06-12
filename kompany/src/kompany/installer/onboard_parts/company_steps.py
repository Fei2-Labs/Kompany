"""Steps 2.8 / 3 / 4 — founder profile, template apply, first directive.

Split out of ``onboard.py`` (ADR-0003); verbatim moves, re-exported
from ``kompany.installer.onboard``.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from kompany.installer.onboard_parts.common import (
    HEADLESS_DEFAULT_TEMPLATE,
    OnboardResult,
    _emit_step,
)


# ---------------------------------------------------------------------------
# Step 3 — template selection
# ---------------------------------------------------------------------------


def _step_template(
    console: Console,
    *,
    yes: bool,
    template_flag: str | None,
    engine: Any,
    result: OnboardResult,
    reused: bool,
    initial_budget: float | None = None,
    revenue_target: float | None = None,
    customer_target: int | None = None,
    deadline: str | None = None,
) -> None:
    _emit_step(console, 3, "Choose a starter company")

    if reused:
        applied = engine.templates.is_applied()
        if applied:
            console.print(f"      [dim]reusing template {applied!r}[/dim]")
            result.template_id = applied
        else:
            console.print("      [dim]no template applied; skipping[/dim]")
        return

    try:
        available = engine.templates.list_templates()
    except Exception as exc:  # noqa: BLE001
        console.print(
            f"[yellow]Template service unavailable ({exc}); "
            f"continuing without a template.[/yellow]"
        )
        result.notes.append("template service unavailable; no starter applied.")
        return

    available_ids = [tpl.id for tpl in available]
    template_id = template_flag
    if not template_id:
        if yes:
            template_id = HEADLESS_DEFAULT_TEMPLATE
        else:
            if not available_ids:
                console.print(
                    "      [yellow]No templates installed; falling back to blank.[/yellow]"
                )
                template_id = HEADLESS_DEFAULT_TEMPLATE
            else:
                table = Table(show_header=False, padding=(0, 1))
                table.add_column("#", style="bold cyan")
                table.add_column("id")
                table.add_column("mission")
                for idx, tpl in enumerate(available, start=1):
                    table.add_row(str(idx), tpl.id, tpl.mission_title)
                console.print(table)
                template_id = Prompt.ask(
                    "      Template id",
                    choices=available_ids,
                    default=HEADLESS_DEFAULT_TEMPLATE
                    if HEADLESS_DEFAULT_TEMPLATE in available_ids
                    else available_ids[0],
                )

    # Apply --------------------------------------------------------------
    # Mission-targets task (05-19): pass the four overrides so an
    # explicit ``--budget`` / ``--revenue-target`` etc. beats the
    # template manifest's presets.
    overrides_present = any(
        v is not None
        for v in (initial_budget, revenue_target, customer_target, deadline)
    )
    try:
        applied_id = engine.templates.is_applied()
        if applied_id == template_id and not overrides_present:
            console.print(f"      [dim]template {template_id!r} already applied[/dim]")
            result.template_id = template_id
            return
        force = applied_id is not None
        apply_kwargs: dict[str, Any] = {"force": force}
        if initial_budget is not None:
            apply_kwargs["override_budget"] = float(initial_budget)
        if revenue_target is not None:
            apply_kwargs["override_revenue_target"] = float(revenue_target)
        if customer_target is not None:
            apply_kwargs["override_customer_target"] = int(customer_target)
        if deadline is not None and deadline != "":
            apply_kwargs["override_deadline"] = str(deadline)
        engine.apply_template(template_id, **apply_kwargs)
    except ValueError as exc:
        console.print(f"[red]Template error: {exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(f"      [green]✓[/green] applied template [bold]{template_id}[/bold]")
    result.template_id = template_id


# ---------------------------------------------------------------------------
# Step 2.8 — founder profile (optional, issues #6/#7)
# ---------------------------------------------------------------------------


def _step_founder_profile(
    console: Console,
    *,
    yes: bool,
    engine: Any,
    result: OnboardResult,
) -> None:
    """Optional founder profile collection — skippable, non-fatal.

    Mirrors the optional first-directive step: headless runs skip it,
    interactive runs get one Confirm. The profile is editable later in
    Settings or via ``kompany founder profile set``.
    """
    _emit_step(console, 2, "Founder profile (optional)")
    if yes:
        console.print("      [dim]skipped (headless — set it later in Settings)[/dim]")
        return
    if not Confirm.ask(
        "      Tell the team how to address + talk to you?", default=False
    ):
        console.print("      [dim]skipped — edit later in Settings[/dim]")
        return
    payload: dict[str, Any] = {}
    address = Prompt.ask("      Address you as", default="").strip()
    if address:
        payload["address"] = address
    style = Prompt.ask(
        "      Comms style (e.g. terse, direct, no fluff)", default=""
    ).strip()
    if style:
        payload["comms_style"] = style
    language = Prompt.ask("      Language (e.g. zh / en)", default="").strip()
    if language:
        payload["language"] = language
    if not payload:
        console.print("      [dim]nothing entered; skipping[/dim]")
        return
    setter = getattr(engine, "set_founder_profile", None)
    if setter is None:
        return
    try:
        setter(payload)
    except Exception as exc:  # noqa: BLE001 — optional step never blocks
        result.notes.append(f"founder profile not saved: {exc}")
        console.print(f"      [yellow]founder profile not saved: {exc}[/yellow]")
        return
    console.print("      [green]✓[/green] founder profile saved")


# ---------------------------------------------------------------------------
# Step 4 — first directive (optional)
# ---------------------------------------------------------------------------


def _step_directive(
    console: Console,
    *,
    yes: bool,
    directive_flag: str | None,
    engine: Any,
    result: OnboardResult,
) -> None:
    _emit_step(console, 4, "First directive (optional)")

    directive_text = directive_flag
    if not directive_text:
        if yes:
            console.print("      [dim]skipped (headless, no --directive given)[/dim]")
            return
        if not Confirm.ask("      Send a first directive now?", default=False):
            console.print("      [dim]skipped[/dim]")
            return
        directive_text = Prompt.ask("      Directive")
        if not directive_text.strip():
            console.print("      [dim]empty directive; skipping[/dim]")
            return

    result.directive_text = directive_text
    try:
        outcome = engine.process_directive(directive_text)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Directive failed: {exc}[/red]")
        result.directive_status = "error"
        result.directive_message = str(exc)
        result.notes.append(f"first directive failed: {exc}")
        return

    result.directive_status = getattr(outcome, "status", None)
    message = getattr(outcome, "message", "") or ""
    result.directive_message = message
    # Print the first five lines (the PRD's spec for the demo capture).
    head = "\n".join(message.splitlines()[:5])
    console.print(Panel(head or "(no message returned)", title="CEO response"))
