"""Template, mission-target, and glossary tools.

Split out of ``mcp_server.py`` (ADR-0003). Entries are verbatim moves;
``mcp_server.TOOLS`` concatenates the domain lists back in the original
order — tool ordering is part of the client-visible contract.
"""

from __future__ import annotations

from mcp.types import Tool

TOOLS: list[Tool] = [
    Tool(
        name="kompany_template_list",
        description="List available ready-to-play company templates.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_template_show",
        description="Show one company template by id (returns manifest + rendered mission body).",
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "Template id"},
            },
            "required": ["template_id"],
        },
    ),
    Tool(
        name="kompany_template_apply",
        description="Apply a company template — writes config, ledgers the initial budget, and stages suggested directives as draft projects.",
        inputSchema={
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "description": "Template id"},
                "force": {"type": "boolean", "description": "Re-apply over an existing template", "default": False},
                "override_budget": {"type": "number", "description": "Override the template's default initial budget."},
                "override_directive": {"type": "string", "description": "Replace suggested directives with one custom directive."},
            },
            "required": ["template_id"],
        },
    ),
    # Mission-targets task (05-19) — quantitative onboarding contract.
    Tool(
        name="kompany_targets_show",
        description=(
            "Show the company's founder / team_proposal / agreed targets trio. "
            "Returns the three states + the review approval thread id."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_targets_review",
        description=(
            "Re-run the team feasibility review on the founder's targets. "
            "Creates one approval_request(action_type='target_feasibility') "
            "carrying the team's recommendation. Returns the approval payload."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    # Glossary-and-drift-detection task (05-19) — founder-defined
    # canonical terms + forbidden synonyms.
    Tool(
        name="kompany_glossary_list",
        description="Return every glossary entry (canonical term + forbidden synonyms).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="kompany_glossary_show",
        description="Look up one glossary term (case-insensitive).",
        inputSchema={
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Canonical term to look up"},
            },
            "required": ["term"],
        },
    ),
    Tool(
        name="kompany_glossary_add",
        description="Insert a brand-new glossary term (founder-sourced).",
        inputSchema={
            "type": "object",
            "properties": {
                "term": {"type": "string"},
                "definition": {"type": "string"},
                "forbidden_synonyms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
            },
            "required": ["term", "definition"],
        },
    ),
    Tool(
        name="kompany_glossary_update",
        description="Update an existing glossary term's definition or forbidden synonyms.",
        inputSchema={
            "type": "object",
            "properties": {
                "term": {"type": "string"},
                "definition": {"type": "string"},
                "forbidden_synonyms": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["term"],
        },
    ),
    Tool(
        name="kompany_glossary_remove",
        description="Drop one glossary term. Returns {removed: bool}.",
        inputSchema={
            "type": "object",
            "properties": {
                "term": {"type": "string"},
            },
            "required": ["term"],
        },
    ),
]
