"""Kompany configuration management."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings

from kompany.config.model_source import ModelSource


class KompanySettings(BaseSettings):
    """Settings loaded from env vars, then YAML defaults."""

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    glm_api_key: str = Field(default="", alias="GLM_API_KEY")
    kimi_api_key: str = Field(default="", alias="KIMI_API_KEY")
    custom_api_key: str = Field(default="", alias="CUSTOM_LLM_API_KEY")
    custom_base_url: str = Field(default="", alias="CUSTOM_LLM_BASE_URL")
    # Optional web-search provider key for the in-loop web_search tool
    # (06-16-agentic-chat-engine P1). Tavily. When unset, web_search
    # returns a graceful "unconfigured" observation — never required.
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    telegram_allowed_chat_ids: str = Field(default="", alias="TELEGRAM_ALLOWED_CHAT_IDS")
    mobile_remote_token: str = Field(default="", alias="MOBILE_REMOTE_TOKEN")
    # Browser intake hook (issue #23). Falls back to mobile_remote_token
    # when unset so founders with mobile remote configured need no extra step.
    intake_token: str = Field(default="", alias="INTAKE_TOKEN")
    web_dashboard_token: str = Field(default="", alias="WEB_DASHBOARD_TOKEN")
    dashboard_session_ttl_seconds: int = Field(
        default=12 * 60 * 60,
        alias="DASHBOARD_SESSION_TTL_SECONDS",
    )
    vault_key: str = Field(default="", alias="KOMPANY_VAULT_KEY")
    credential_broker_endpoint: str = Field(
        default="",
        alias="KOMPANY_CREDENTIAL_BROKER_ENDPOINT",
    )
    credential_broker_token: str = Field(
        default="",
        alias="KOMPANY_CREDENTIAL_BROKER_TOKEN",
    )
    credential_broker_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=60,
        alias="KOMPANY_CREDENTIAL_BROKER_TIMEOUT_SECONDS",
    )
    vault_keychain_service: str = Field(default="kompany", alias="KOMPANY_VAULT_KEYCHAIN_SERVICE")
    vault_keychain_account: str = Field(default="vault-master-key", alias="KOMPANY_VAULT_KEYCHAIN_ACCOUNT")
    remote_replay_ttl_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        alias="REMOTE_REPLAY_TTL_SECONDS",
    )

    data_dir: Path = Field(default=Path("~/.kompany").expanduser())
    company_name: str = ""
    company_goal: str = ""
    company_stage: str = "solo"
    company_time_horizon: str = ""
    company_exclusions: str = ""
    currency: str = "EUR"

    # Founder profile + rules (#6/#7). Source of truth is the
    # ``company_config`` DB rows (core/founder_config.py); the engine
    # mirrors them here at boot/set so agents — which only see settings
    # — can build the founder-context prompt block without a DB handle.
    founder_profile: dict | None = None
    founder_rules: dict | None = None

    # Model tiers
    model_apex: str = "claude-opus-4-20250514"
    model_primary: str = "claude-sonnet-4-20250514"
    model_economy: str = "claude-haiku-4-20250414"

    # Active ModelSource (06-11-harness-execution-leg PR3). MVP keeps a
    # single active source. ``None`` preserves pre-PR3 behavior exactly:
    # api billing for everything, built-in PRICING table, no monthly fee.
    model_source: ModelSource | None = None

    # Harness execution feature flag (06-11-harness-execution-leg PR4).
    # Default ON per the PRD DoD — but the harness path only activates
    # when a ``model_source`` is configured too; ``model_source=None``
    # always means the legacy single-call path regardless of this flag.
    # Flip to False to force the single-call fallback for all tasks.
    harness_execution_enabled: bool = True

    # Harness permission routing (06-11-harness-execution-leg PR5, PRD
    # D5): claude-vehicle sessions route permission prompts through the
    # ``kompany_permission_gate`` MCP tool into the founder approval
    # inbox. False restores plain ``--permission-mode`` behavior.
    harness_permission_routing: bool = True

    # NativeRunner vehicle (issue #20, 06-12-native-runner). When ON and
    # the active ModelSource is custom_api, harness sessions run the
    # Kompany-owned loop instead of the opencode CLI. Default OFF — the
    # rented vehicles stay the default until native proves itself.
    native_runner_enabled: bool = False

    # Envelope overdraw (founder investment model). When ON, the harness
    # executor's pre-run envelope guard does NOT park a task whose project
    # envelope is exhausted — the task runs, token cost is booked to the
    # ledger as usual (treasury goes more negative), and the deficit is
    # expected to be offset by future revenue. Default OFF preserves the
    # hard-cap semantics (an empty envelope parks the task and proposes a
    # top-up approval). Founders running on a negative-balance investment
    # model turn this on; founders enforcing per-project budget discipline
    # leave it off.
    allow_envelope_overdraw: bool = False

    # Agentic CEO chat (06-16-agentic-chat-engine P2). When ON, a real
    # ``answer``/``execute`` request in the board chat runs the upgraded
    # NativeRunner loop AS the CEO (persona system prompt + in-loop tools +
    # streaming + inline approval gate) instead of a single ``ceo.answer()``
    # /handler call. Requires a native-tool-capable model (Anthropic /
    # OpenAI-compatible); the engine silently falls back to the legacy
    # single-call path when the model can't do native tool_use, so the
    # board contract is never broken. Default OFF — the single-call path
    # stays the default until the agentic chat proves itself.
    agentic_chat_enabled: bool = False
    # Per-chat-session caps for the agentic loop (cost-visibility + safety).
    agentic_chat_budget_cap_usd: float = 0.50
    agentic_chat_max_turns: int = 16

    # Learned-skill / memory compounding (06-16-agentic-chat-engine P5).
    # After a SUCCESSFUL agentic chat the engine distills "what worked" into
    # a reusable skill (SOP + trigger words) via one cheap LLM call, stored
    # in ``agent_skills`` and retrieved by trigger words on later similar
    # requests. Default ON but gated: only crystallizes on success, dedupes
    # against existing skills, and skips trivial chats below the tool-use
    # floor (so a one-tool answer never makes a skill). Flip OFF to disable.
    skill_crystallization_enabled: bool = True
    # Minimum distinct tool calls in a session before it is worth
    # crystallizing — keeps trivial one-shot chats out of the skill tree.
    skill_crystallize_min_tools: int = 2
    # Max skills injected into the chat system prompt as "relevant past
    # skills" (token-bounded retrieval).
    skill_retrieve_limit: int = 3
    # History compression for the agentic loop (GenericAgent ~30K-context
    # trick): when the running transcript grows past this many turns, older
    # turns are folded into a single rolling summary so long sessions stay
    # token-bounded; the latest working checkpoint is preserved intact. 0
    # disables compression.
    agentic_history_compress_after_turns: int = 8

    # External MCP servers the in-loop MCP client connects to (06-16 P4).
    # A list of dicts: {name, transport: "stdio"|"sse", command/args/env or
    # url, read_only?: bool, read_only_tools?: [str]}. Each server's tools
    # are registered as ``mcp__<server>__<tool>`` in the chat registry —
    # EXTERNAL_ACTION (gated) by default unless marked read-only. Unreachable
    # servers are skipped with a health note; never required. Configured via
    # YAML (env can't carry a list cleanly).
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)

    # Browser CDP endpoint for the agentic loop's browser tools. The founder
    # runs a real browser (Brave/Edge/Chrome) with --remote-debugging-port=N
    # and a dedicated --user-data-dir so the agent reuses the logged-in
    # profile (e.g. LinkedIn sessions). Default 9223 matches the Kompany
    # desktop convention; VPS deployments with a different port (e.g. 9335
    # for the linkedin-growth Brave instance) override via env or YAML.
    browser_cdp_endpoint: str = Field(
        default="http://127.0.0.1:9223",
        alias="KOMPANY_BROWSER_CDP_ENDPOINT",
    )

    # Remote backup config block (07-14 cloud-deploy-backup-restore step 5).
    # Raw dict — parsed by RemoteBackupConfig.from_dict in state/remote_backup.
    # YAML example:
    #   remote_backup:
    #     endpoint_url: https://<acct>.r2.cloudflarestorage.com
    #     bucket: kompany-backups
    #     access_key_id: ...
    #     secret_access_key: ...
    #     passphrase: ...
    #     retain: 7
    remote_backup: dict[str, Any] = Field(default_factory=dict)

    # Daemon tick loop (06-12-daemon-tick-loop PR1): wake interval of the
    # autonomous ticker, and the advance-work gate (PRD D3 step 3 — at
    # most one pending task of one active project per tick). Flip
    # ``daemon_auto_execute`` to False to keep ticking (heartbeat,
    # housekeeping, recording) without autonomous task execution.
    tick_interval_seconds: int = 300
    daemon_auto_execute: bool = True

    # Self-update pipeline (06-12-self-update-pipeline PRD D5). Code work
    # is heavier than the task default ($0.50/30) — the dedicated session
    # cap is $2 / 40 turns. ``self_update_test_cmd`` runs inside the
    # clone's ``kompany/`` directory with PYTHONPATH pinned to the
    # clone's ``src`` (PRD D4).
    self_update_budget_cap_usd: float = 2.0
    self_update_max_turns: int = 40
    self_update_test_cmd: str = "python -m pytest tests/ -q"

    # Anima persona layer (06-12-anima-persona). ``anima_enabled``
    # registers the emotion + diary tick intents; ``anima_diary_enabled``
    # gates ONLY the daily economy-tier diary call (emotion stays pure
    # code and free). Both flags off → ticker actions list unchanged.
    anima_enabled: bool = True
    anima_diary_enabled: bool = True

    # Bidirectional channels (06-12-channels). ``anima_outbox_enabled``
    # gates the diary → outbox-draft hook (PRD D3, default OFF — drafts
    # only, never auto-posted). Email-in (PRD D4) polls IMAP inside the
    # ticker every ``email_poll_every_ticks`` ticks; the password lives
    # in the credential vault under ``email_imap_password``, never here.
    anima_outbox_enabled: bool = False
    email_imap_host: str = Field(default="", alias="KOMPANY_EMAIL_IMAP_HOST")
    email_imap_user: str = Field(default="", alias="KOMPANY_EMAIL_IMAP_USER")
    email_imap_folder: str = Field(
        default="INBOX", alias="KOMPANY_EMAIL_IMAP_FOLDER"
    )
    email_poll_every_ticks: int = 12

    # ``extra="ignore"`` is critical: a founder's machine may have any
    # number of unrelated env vars or .env entries (e.g. SWEDEAPI_*
    # custom-provider keys from earlier testing). Without this, engine
    # boot crashes with ``Extra inputs are not permitted`` and the UI
    # gets a 500 on /status + /targets, which then silently hides the
    # team-review proposal card. Diagnosed 2026-05-26.
    model_config = {
        "env_prefix": "KOMPANY_",
        "env_file": ".env",
        "extra": "ignore",
    }

    def get_model_for_tier(self, tier: str) -> str:
        return {
            "apex": self.model_apex,
            "primary": self.model_primary,
            "economy": self.model_economy,
        }.get(tier, self.model_primary)

    def fallback_model_pool(self) -> list[str]:
        """Distinct tier models used as the ADR-0005 model-fallback pool.

        When a call's primary model is unavailable, the LLM client retries
        each of these (skipping the one already tried) so a single-model
        outage can't stall a lane-worker. Order: apex → primary → economy.
        """
        pool: list[str] = []
        for m in (self.model_apex, self.model_primary, self.model_economy):
            if m and m not in pool:
                pool.append(m)
        return pool

    def get_api_key_for_provider(self, provider: str) -> str:
        """Return the API key for a given provider name."""
        return {
            "anthropic": self.anthropic_api_key,
            # The claude-code provider shells out to the local `claude`
            # CLI, which carries its own subscription auth. The sentinel
            # keeps empty-key validation paths green without implying a
            # real credential exists.
            "claude_code": "no-key-required",
            # Same deal for the generic CLI providers (issue #18): the
            # local `codex` / `opencode` binaries carry their own saved
            # login (ChatGPT subscription / opencode auth).
            "codex_cli": "no-key-required",
            "opencode_cli": "no-key-required",
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "glm": self.glm_api_key,
            "kimi": self.kimi_api_key,
            "custom": self.custom_api_key,
        }.get(provider, "")

    @classmethod
    def load(cls, config_path: str | None = None) -> "KompanySettings":
        # No explicit config → the founder's persisted ``<data_dir>/
        # config.yaml`` IS the config file (full parse, not just the
        # model_source fallback below). Without this, ``KompanyEngine()``
        # boots (sidecar, daemon, MCP) silently ignored models / tick /
        # flag settings the founder had saved. Live-verification finding,
        # 06-12-daemon-tick-loop.
        # Workspace registry (issue #15): when KOMPANY_DATA_DIR is NOT
        # set, the active workspace from ~/.kompany-workspaces.json
        # supplies the data dir. Env always bypasses the registry; a
        # ``data_dir`` key inside the chosen workspace's config.yaml
        # still applies after (issue #21 ordering unchanged):
        # env > YAML data_dir > active workspace > ~/.kompany default.
        from kompany.config import workspaces as _workspaces

        overrides: dict[str, Any] = {}
        ws_dir = _workspaces.active_data_dir()
        if ws_dir is not None:
            overrides["data_dir"] = ws_dir
        if config_path is None:
            base_dir = ws_dir if ws_dir is not None else cls().data_dir
            candidate = base_dir / "config.yaml"
            if candidate.exists():
                config_path = str(candidate)
        data: dict[str, Any] = {}
        if config_path and Path(config_path).exists():
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
            company = data.get("company", {})
            # update(), not assignment — the workspace data_dir override
            # above must survive the YAML parse (issue #15).
            overrides.update({
                "company_name": company.get("name", ""),
                "company_goal": company.get("goal", ""),
                "company_stage": company.get("stage", "solo"),
                "company_time_horizon": company.get("time_horizon", ""),
                "company_exclusions": company.get("exclusions", ""),
                "currency": company.get("currency", "EUR"),
            })
            # Model tier overrides from YAML
            models = data.get("models", {})
            if "apex" in models:
                overrides["model_apex"] = models["apex"]
            if "primary" in models:
                overrides["model_primary"] = models["primary"]
            if "economy" in models:
                overrides["model_economy"] = models["economy"]
            # Custom LLM endpoint from YAML
            custom = data.get("custom_llm", {})
            if "api_key" in custom:
                overrides["custom_api_key"] = custom["api_key"]
            if "base_url" in custom:
                overrides["custom_base_url"] = custom["base_url"]
            # Active model source from YAML (kind, billing_mode,
            # monthly_fee_usd, price_overrides). Validation errors
            # surface at load time — a misconfigured source must not
            # silently fall back to api billing.
            source = data.get("model_source")
            if source:
                overrides["model_source"] = ModelSource.model_validate(source)
            # Harness execution flags (06-11-harness-execution-leg).
            for flag in (
                "harness_execution_enabled",
                "harness_permission_routing",
                "native_runner_enabled",
                "allow_envelope_overdraw",
            ):
                if flag in data:
                    overrides[flag] = bool(data[flag])
            # Daemon tick loop settings (06-12-daemon-tick-loop PR1).
            if "tick_interval_seconds" in data:
                overrides["tick_interval_seconds"] = int(data["tick_interval_seconds"])
            if "daemon_auto_execute" in data:
                overrides["daemon_auto_execute"] = bool(data["daemon_auto_execute"])
            # Anima persona flags (06-12-anima-persona).
            for flag in ("anima_enabled", "anima_diary_enabled"):
                if flag in data:
                    overrides[flag] = bool(data[flag])
            # Bidirectional channels (06-12-channels).
            if "anima_outbox_enabled" in data:
                overrides["anima_outbox_enabled"] = bool(
                    data["anima_outbox_enabled"]
                )
            for key in ("email_imap_host", "email_imap_user", "email_imap_folder"):
                if key in data:
                    overrides[key] = str(data[key])
            if "email_poll_every_ticks" in data:
                overrides["email_poll_every_ticks"] = int(
                    data["email_poll_every_ticks"]
                )
            # data_dir from YAML (issue #21): silently ignoring this key
            # caused a real production-contamination incident — an
            # isolated config's data_dir was dropped and the engine wrote
            # to ~/.kompany. Honor it (env KOMPANY_DATA_DIR still wins,
            # pydantic-settings env precedence is unchanged).
            if (
                "data_dir" in data
                and data["data_dir"]
                and not os.environ.get("KOMPANY_DATA_DIR")
            ):
                # Init kwargs beat env in pydantic-settings, so guard
                # explicitly: KOMPANY_DATA_DIR remains the strongest
                # override (the daemon plist and all interfaces rely on it).
                overrides["data_dir"] = Path(str(data["data_dir"])).expanduser()
            # Self-update pipeline knobs (06-12-self-update-pipeline).
            for key in (
                "self_update_budget_cap_usd",
                "self_update_max_turns",
                "self_update_test_cmd",
            ):
                if key in data:
                    overrides[key] = data[key]
            # Self-update pipeline settings (06-12-self-update-pipeline).
            if "self_update_budget_cap_usd" in data:
                overrides["self_update_budget_cap_usd"] = float(
                    data["self_update_budget_cap_usd"]
                )
            if "self_update_max_turns" in data:
                overrides["self_update_max_turns"] = int(
                    data["self_update_max_turns"]
                )
            if "self_update_test_cmd" in data:
                overrides["self_update_test_cmd"] = str(
                    data["self_update_test_cmd"]
                )
            # Remote backup config block (07-14 step 5).
            if "remote_backup" in data and isinstance(data["remote_backup"], dict):
                overrides["remote_backup"] = data["remote_backup"]
        settings = cls(**overrides)
        # ModelSource fallback (06-11-harness-execution-leg PR5b): the
        # founder surfaces persist the active source to
        # ``<data_dir>/config.yaml`` (model_source_ops). Engines usually
        # boot with ``config_path=None``, so read that file back here —
        # an explicit config that already set ``model_source`` wins.
        if settings.model_source is None and "model_source" not in data:
            default_cfg = settings.data_dir / "config.yaml"
            if default_cfg.exists():
                try:
                    extra = yaml.safe_load(default_cfg.read_text()) or {}
                except (OSError, yaml.YAMLError):
                    extra = {}
                source = extra.get("model_source") if isinstance(extra, dict) else None
                if source:
                    # Validation errors surface at load time — a
                    # misconfigured source must not silently fall back
                    # to api billing (same contract as the explicit
                    # config path above).
                    settings.model_source = ModelSource.model_validate(source)
        return settings
