"""Kompany configuration management."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


class KompanySettings(BaseSettings):
    """Settings loaded from env vars, then YAML defaults."""

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    glm_api_key: str = Field(default="", alias="GLM_API_KEY")
    kimi_api_key: str = Field(default="", alias="KIMI_API_KEY")
    custom_api_key: str = Field(default="", alias="CUSTOM_LLM_API_KEY")
    custom_base_url: str = Field(default="", alias="CUSTOM_LLM_BASE_URL")

    data_dir: Path = Field(default=Path("~/.kompany").expanduser())
    company_name: str = ""
    company_product: str = ""
    company_stage: str = "solo"
    currency: str = "EUR"

    # Model tiers
    model_apex: str = "claude-opus-4-20250514"
    model_primary: str = "claude-sonnet-4-20250514"
    model_economy: str = "claude-haiku-4-20250414"

    model_config = {"env_prefix": "KOMPANY_", "env_file": ".env"}

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
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "glm": self.glm_api_key,
            "kimi": self.kimi_api_key,
            "custom": self.custom_api_key,
        }.get(provider, "")

    @classmethod
    def load(cls, config_path: str | None = None) -> "KompanySettings":
        overrides: dict[str, Any] = {}
        if config_path and Path(config_path).exists():
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
            company = data.get("company", {})
            overrides = {
                "company_name": company.get("name", ""),
                "company_product": company.get("product", ""),
                "company_stage": company.get("stage", "solo"),
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
        return cls(**overrides)
