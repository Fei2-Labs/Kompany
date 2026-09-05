"""Self-update, model-source, CLI detection, and founder profile/rules tools.

Split out of ``mcp_server.py`` (ADR-0003). Entries are verbatim moves;
``mcp_server.TOOLS`` concatenates the domain lists back in the original
order — tool ordering is part of the client-visible contract.
"""

from __future__ import annotations

from mcp.types import Tool

TOOLS: list[Tool] = [
    # Self-update pipeline (06-12-self-update-pipeline PR2).
    Tool(
        name="kompany_self_update_propose",
        description=(
            "Governed self-update: run a harness session in the dedicated "
            "repo clone, enforce the T3 tier guard on the real diff, run "
            "tests, and file a self_update_proposal approval card. The "
            "merge stays human (founder approves, branch is pushed, PR "
            "opened best-effort)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "What to change and why (plain language)",
                },
            },
            "required": ["instruction"],
        },
    ),
    Tool(
        name="kompany_self_update_list",
        description="Recent self-update proposals, newest first.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
    ),
    Tool(
        name="kompany_self_update_role",
        description=(
            "Installation role (customer/contributor/maintainer) and whether approving a "
            "self-update proposal pushes a branch + opens a PR or only exports a patch. Read-only."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_self_update_show",
        description="One self-update proposal by id.",
        inputSchema={
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}},
            "required": ["proposal_id"],
        },
    ),
    # ModelSource founder surface (06-11-harness-execution-leg PR5b).
    Tool(
        name="kompany_model_source_show",
        description=(
            "Show the active model source (kind, billing_mode, monthly fee) "
            "or null when none is configured (legacy per-token billing)."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_model_source_set",
        description=(
            "Set the active model source. kind: custom_api | "
            "claude_subscription | openai_subscription. Subscription kinds "
            "require monthly_fee_usd. Pass clear=true to remove the source "
            "(legacy per-token billing). The execution loop is derived by "
            "the engine — there is no loop/vehicle input."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "custom_api | claude_subscription | openai_subscription"},
                "billing_mode": {"type": "string", "description": "api | subscription (defaults from kind)"},
                "monthly_fee_usd": {"type": "number", "description": "Required for subscription billing"},
                "price_overrides": {"type": "object", "description": "model -> [input_usd, output_usd] per million tokens"},
                "clear": {"type": "boolean", "description": "Remove the source (legacy billing)", "default": False},
            },
        },
    ),
    Tool(
        name="kompany_detect_clis",
        description=(
            "Probe PATH for agent CLIs (claude / codex / opencode) that "
            "unlock zero-key model sources. Returns {cli: {found, path, "
            "version, source_kind}}."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    # Founder profile + rules (#6/#7).
    Tool(
        name="kompany_founder_profile_show",
        description=(
            "Show the founder profile (address, comms_style, language, "
            "working_hours, timezone, risk_tolerance) or null when unset."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_founder_profile_set",
        description=(
            "Merge-set the founder profile (partial fields merge over the "
            "stored profile). Pass clear=true to remove it."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "How to address the founder"},
                "pronouns": {"type": "string"},
                "comms_style": {"type": "string", "description": "Preferred tone, e.g. 'terse, direct'"},
                "language": {"type": "string", "description": "e.g. zh / en"},
                "working_hours": {"type": "string"},
                "timezone": {"type": "string"},
                "risk_tolerance": {"type": "string"},
                "clear": {"type": "boolean", "description": "Remove the profile", "default": False},
            },
        },
    ),
    Tool(
        name="kompany_founder_rules_show",
        description=(
            "Show the founder rules ({hard, soft}) or null when unset. "
            "Hard rules are enforced (proposal filter + execution gate); "
            "soft is best-effort prompt text."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_founder_rules_set",
        description=(
            "Merge-set the founder rules. hard: list of {kind, match, "
            "action} (kind: exclude_capability | budget_cap | "
            "forbid_paid_category); soft: free-text preferences. Pass "
            "clear=true to remove all rules."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "hard": {"type": "array", "items": {"type": "object"}, "description": "[{kind, match, action}]"},
                "soft": {"type": "string", "description": "Free-text preferences"},
                "clear": {"type": "boolean", "description": "Remove all founder rules", "default": False},
            },
        },
    ),
]
