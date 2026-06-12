---
description: Send a directive to your Kompany — the autonomous business OS
argument-hint: "[directive in natural language]"
---

Route the founder's directive through the Kompany engine.

Founder mental model (never violate):
- **Mission integrity** — never downgrade the mission. Budget short? The company creates a revenue project to fund it. "We can't afford it" is not an answer.
- **AI costs are real costs** — every LLM call books to the company ledger; the balance can go negative, which raises the revenue target, never cancels the mission.
- **Virtual time** — one completed task = one virtual day; runway counts virtual days, so a paused company loses nothing.
- **Approval inbox** — money, irreversible actions, and account connections wait for the founder; everything else runs autonomously.

Steps:
1. Send the directive via the `kompany_directive` MCP tool: `$ARGUMENTS`. If no company exists yet (tool reports uninitialized), tell the user to run `kompany onboard` first.
2. If the result is `clarify`, relay the CEO's question and continue the same `session_id`. If `gated`, show the plan + cost estimate and ask for GO (`kompany_channel_go`).
3. Report the outcome including the AI cost incurred and the balance impact. For status questions use `kompany_status`, projects via `kompany_projects`, ledger via `kompany_ledger`.

Prefer the typed `kompany_*` MCP tools over shelling out to the CLI.
