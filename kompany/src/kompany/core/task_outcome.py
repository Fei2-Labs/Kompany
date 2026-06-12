"""Legacy task outcome classification (honest-outcome hybrid mode).

Extracted from ``core/runner.py`` (06-12-daemon-tick-loop PR1) to keep
runner.py under the 500-line cap. The harness execution path has its
own classifier in ``core/harness_execution/outcomes.py``; this one
serves the legacy single ``agent.call`` path.

Doctrine (#13, full-autonomy reframe): the founder has exactly three
jobs — connect accounts, approve money, decide escalations. A blocked
or delivered task must therefore NEVER ask the founder to do the labor
("send it yourself"). It names the MISSING CAPABILITY instead:

* outreach intent + email integration CONNECTED → don't block at all:
  file a ``tool_action`` draft card (``engine.propose_action``) so the
  founder only APPROVES and the team sends for real.
* capability missing → "connect X in Settings so the team can act".
"""

from __future__ import annotations

import re
from typing import Any

# Phrases an agent uses when it produced a plan/asset but could not
# actually perform an external action (no integration/credentials).
BLOCKED_SIGNALS = (
    "cannot truthfully confirm",
    "do not have access",
    "don't have access",
    "no access to your",
    "unable to send",
    "i cannot send",
    "i can't send",
    "do not have the ability",
    "cannot actually",
    "i am unable to",
    "without access to your",
    "requires access to your",
    "need access to your",
    "i have not sent",
    "i did not send",
    "have not actually",
)

# Task-title signals for an outreach/send intent — the one case the
# propose-instead-of-block hook covers in v1 (email.send exists).
SEND_INTENT_KEYWORDS = (
    "send", "outreach", "dm", "email", "message", "follow up", "follow-up",
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def classify_outcome(
    title: str, output: str, *, email_connected: bool = False
) -> tuple[str, str]:
    """Classify what really happened in a task (hybrid mode, step A).

    Returns ``(outcome, founder_action)`` where outcome is one of
    ``"completed" | "delivered" | "blocked" | "propose_email"``.

    ``founder_action`` follows the three-jobs doctrine: it names a
    connection or approval, never founder labor. ``propose_email`` is
    returned when the task looks like outreach, the email integration is
    connected, and the output contains a recipient address — the caller
    (``resolve_outcome``) turns it into a ``tool_action`` draft card.
    """
    low = (output or "").lower()
    blocked = any(sig in low for sig in BLOCKED_SIGNALS)

    t = (title or "").lower()
    send_intent = any(k in t for k in SEND_INTENT_KEYWORDS)
    if send_intent and email_connected and _EMAIL_RE.search(output or ""):
        return "propose_email", (
            "Approve the drafted email in your inbox — the team sends it "
            "the moment you approve."
        )
    if send_intent:
        action = (
            "Connect an email account in Settings (Integrations) so the "
            "team can send these for you — no send capability is "
            "connected yet."
        )
    elif any(k in t for k in ("checkout", "landing page", "stripe", "gumroad", "publish", "page")):
        action = (
            "Connect a publishing integration (Stripe / Gumroad / your "
            "site) in Settings so the team can publish this — it stays "
            "staged here until then."
        )
    elif any(k in t for k in ("call", "book", "schedule", "onboard")):
        action = (
            "Connect a calendar/scheduling integration in Settings so the "
            "team can book these — the script is ready and waiting."
        )
    else:
        action = (
            "Review the asset — any external action it needs will be "
            "proposed to your inbox once the matching account is "
            "connected."
        )

    return ("blocked" if blocked else "delivered"), action


def _email_send_connected(engine: Any) -> bool:
    """True when a registered ``email.send`` provider has credentials."""
    try:
        from kompany.core.tool_actions import _integration_connected, tool_registry

        entry = tool_registry(engine).get("email.send")
        if not entry:
            return False
        return any(
            _integration_connected(engine, integ)
            for integ in entry["integrations"]
        )
    except Exception:  # noqa: BLE001 — minimal engines (tests) lack vault
        return False


def resolve_outcome(engine: Any, task: Any, project: Any, output: str) -> tuple[str, str]:
    """Classify AND apply the propose-instead-of-block hook (#13).

    When the classifier says ``propose_email``, file a ``tool_action``
    draft card via ``engine.propose_action`` (email.send: recipient from
    the output, subject from the task title, body = the drafted output)
    and land the task as ``delivered`` with an approve-framed action.
    Any failure to file the card falls back to the plain classification
    with ``email_connected=False`` — never crash the runner over a card.
    """
    outcome, action = classify_outcome(
        task.title, output, email_connected=_email_send_connected(engine)
    )
    if outcome != "propose_email":
        return outcome, action
    try:
        recipient = _EMAIL_RE.search(output or "").group(0)  # type: ignore[union-attr]
        engine.propose_action(
            "email.send",
            {"to": recipient, "subject": task.title, "body": output},
            summary=f"Send drafted email for task: {task.title}",
            requested_by=task.assigned_agent,
            directive_id=getattr(project, "triggers_directive_id", None),
            project_id=project.id,
            task_id=task.id,
            reason="Outreach task with email connected — proposed instead of blocking (#13).",
        )
        return "delivered", action
    except Exception:  # noqa: BLE001 — card filing must not kill the run
        return classify_outcome(task.title, output, email_connected=False)


__all__ = ["BLOCKED_SIGNALS", "SEND_INTENT_KEYWORDS", "classify_outcome", "resolve_outcome"]
