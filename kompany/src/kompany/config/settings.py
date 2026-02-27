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
        return cls(**overrides)
