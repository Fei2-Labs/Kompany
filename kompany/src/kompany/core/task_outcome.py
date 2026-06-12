"""Legacy task outcome classification (honest-outcome hybrid mode).

Extracted verbatim from ``core/runner.py`` (06-12-daemon-tick-loop PR1)
to keep runner.py under the 500-line cap — no behavior change. The
harness execution path has its own classifier in
``core/harness_execution/outcomes.py``; this one serves the legacy
single ``agent.call`` path.
"""

from __future__ import annotations

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


def classify_outcome(title: str, output: str) -> tuple[str, str]:
    """Classify what really happened in a task (hybrid mode, step A).

    Returns ``(outcome, founder_action)`` where outcome is one of
    ``"completed" | "delivered" | "blocked"``.

    No real integrations are wired yet, so an agent never truly
    *executes* an external action — it produces an asset, and sometimes
    says outright it can't act. We therefore never return ``completed``
    here; that state is reserved for a future real-tool path. We
    distinguish ``blocked`` (the agent flagged missing access) from
    ``delivered`` (a usable asset the founder must act on).
    """
    low = (output or "").lower()
    blocked = any(sig in low for sig in BLOCKED_SIGNALS)

    t = (title or "").lower()
    if any(k in t for k in ("send", "outreach", "dm", "email", "message", "follow up", "follow-up")):
        action = "Send the drafted messages yourself (your email / LinkedIn / DMs) — the team can't send without an integration."
    elif any(k in t for k in ("checkout", "landing page", "stripe", "gumroad", "publish", "page")):
        action = "Publish the asset: paste the copy/links into Stripe/Gumroad or your site."
    elif any(k in t for k in ("call", "book", "schedule", "onboard")):
        action = "Run the calls / scheduling yourself using the script above."
    else:
        action = "Review the asset above and execute it."

    return ("blocked" if blocked else "delivered"), action


__all__ = ["BLOCKED_SIGNALS", "classify_outcome"]
