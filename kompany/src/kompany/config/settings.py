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
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    telegram_allowed_chat_ids: str = Field(default="", alias="TELEGRAM_ALLOWED_CHAT_IDS")
    mobile_remote_token: str = Field(default="", alias="MOBILE_REMOTE_TOKEN")
    web_dashboard_token: str = Field(default="", alias="WEB_DASHBOARD_TOKEN")
    dashboard_session_ttl_seconds: int = Field(
        default=12 * 60 * 60,
        alias="DASHBOARD_SESSION_TTL_SECONDS",
    )
    vault_key: str = Field(default="", alias="KOMPANY_VAULT_KEY")
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

    # Daemon tick loop (06-12-daemon-tick-loop PR1): wake interval of the
    # autonomous ticker, and the advance-work gate (PRD D3 step 3 — at
    # most one pending task of one active project per tick). Flip
    # ``daemon_auto_execute`` to False to keep ticking (heartbeat,
    # housekeeping, recording) without autonomous task execution.
    tick_interval_seconds: int = 300
    daemon_auto_execute: bool = True

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

    def get_api_key_for_provider(self, provider: str) -> str:
        """Return the API key for a given provider name."""
        return {
            "anthropic": self.anthropic_api_key,
            # The claude-code provider shells out to the local `claude`
            # CLI, which carries its own subscription auth. The sentinel
            # keeps empty-key validation paths green without implying a
            # real credential exists.
            "claude_code": "no-key-required",
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
        if config_path is None:
            candidate = cls().data_dir / "config.yaml"
            if candidate.exists():
                config_path = str(candidate)
        overrides: dict[str, Any] = {}
        data: dict[str, Any] = {}
        if config_path and Path(config_path).exists():
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
            company = data.get("company", {})
            overrides = {
                "company_name": company.get("name", ""),
                "company_goal": company.get("goal", ""),
                "company_stage": company.get("stage", "solo"),
                "company_time_horizon": company.get("time_horizon", ""),
                "company_exclusions": company.get("exclusions", ""),
                "currency": company.get("currency", "EUR"),
            }
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
            ):
                if flag in data:
                    overrides[flag] = bool(data[flag])
            # Daemon tick loop settings (06-12-daemon-tick-loop PR1).
            if "tick_interval_seconds" in data:
                overrides["tick_interval_seconds"] = int(data["tick_interval_seconds"])
            if "daemon_auto_execute" in data:
                overrides["daemon_auto_execute"] = bool(data["daemon_auto_execute"])
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
