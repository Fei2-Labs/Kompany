"""Tests for the ModelSource settings model (06-11-harness-execution-leg PR3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kompany.config.model_source import ModelSource
from kompany.config.settings import KompanySettings


# ---------------------------------------------------------------------
# billing_mode derivation + validation
# ---------------------------------------------------------------------


def test_custom_api_defaults_to_api_billing():
    source = ModelSource(kind="custom_api")
    assert source.billing_mode == "api"
    assert source.monthly_fee_usd is None


def test_claude_subscription_defaults_to_subscription_billing():
    source = ModelSource(kind="claude_subscription", monthly_fee_usd=20.0)
    assert source.billing_mode == "subscription"


def test_openai_subscription_defaults_to_subscription_billing():
    source = ModelSource(kind="openai_subscription", monthly_fee_usd=20.0)
    assert source.billing_mode == "subscription"


def test_subscription_requires_monthly_fee():
    with pytest.raises(ValidationError, match="monthly_fee_usd"):
        ModelSource(kind="claude_subscription")


def test_negative_monthly_fee_rejected():
    with pytest.raises(ValidationError, match="monthly_fee_usd"):
        ModelSource(kind="claude_subscription", monthly_fee_usd=-1.0)


def test_billing_mode_overridable_to_api_without_fee():
    # A subscription-kind source forced to per-token billing (e.g. trial
    # month on an API key) needs no monthly fee.
    source = ModelSource(kind="claude_subscription", billing_mode="api")
    assert source.billing_mode == "api"


def test_billing_mode_overridable_to_subscription_requires_fee():
    with pytest.raises(ValidationError, match="monthly_fee_usd"):
        ModelSource(kind="custom_api", billing_mode="subscription")
    source = ModelSource(
        kind="custom_api", billing_mode="subscription", monthly_fee_usd=99.0
    )
    assert source.billing_mode == "subscription"


def test_unknown_kind_rejected():
    with pytest.raises(ValidationError):
        ModelSource(kind="kiro_subscription", monthly_fee_usd=10.0)


# ---------------------------------------------------------------------
# vehicle derivation (PRD D1 — computed, never configured)
# ---------------------------------------------------------------------


def test_vehicle_derivation():
    assert (
        ModelSource(kind="claude_subscription", monthly_fee_usd=20.0).vehicle
        == "claude_code"
    )
    assert (
        ModelSource(kind="openai_subscription", monthly_fee_usd=20.0).vehicle
        == "codex"
    )
    assert ModelSource(kind="custom_api").vehicle == "opencode"


def test_vehicle_is_not_a_field():
    # ``vehicle`` must stay a computed property — it can never be set
    # from founder-facing config; an inbound "vehicle" key is ignored.
    assert "vehicle" not in ModelSource.model_fields
    source = ModelSource.model_validate(
        {"kind": "custom_api", "vehicle": "claude_code"}
    )
    assert source.vehicle == "opencode"


# ---------------------------------------------------------------------
# subscription CLI routing check
# ---------------------------------------------------------------------


def test_claude_subscription_covers_claude_code_models():
    source = ModelSource(kind="claude_subscription", monthly_fee_usd=20.0)
    assert source.is_subscription_call("claude-code:sonnet") is True
    assert source.is_subscription_call("claude-code:opus") is True
    # API-routed models are NOT on the subscription, even for this source.
    assert source.is_subscription_call("claude-sonnet-4-20250514") is False
    assert source.is_subscription_call("gpt-4o") is False


def test_api_mode_source_covers_nothing():
    source = ModelSource(kind="claude_subscription", billing_mode="api")
    assert source.is_subscription_call("claude-code:sonnet") is False


def test_openai_subscription_has_no_single_call_cli_yet():
    source = ModelSource(kind="openai_subscription", monthly_fee_usd=20.0)
    assert source.is_subscription_call("gpt-4o") is False
    assert source.is_subscription_call("o3") is False


# ---------------------------------------------------------------------
# KompanySettings integration + backward compat
# ---------------------------------------------------------------------


def test_settings_default_has_no_model_source(monkeypatch):
    monkeypatch.delenv("KOMPANY_MODEL_SOURCE", raising=False)
    settings = KompanySettings()
    assert settings.model_source is None


def test_settings_load_yaml_model_source(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
company:
  name: TestCo
model_source:
  kind: claude_subscription
  monthly_fee_usd: 20.0
  price_overrides:
    my-private-model: [1.0, 2.0]
"""
    )
    settings = KompanySettings.load(str(config))
    assert settings.model_source is not None
    assert settings.model_source.kind == "claude_subscription"
    assert settings.model_source.billing_mode == "subscription"
    assert settings.model_source.monthly_fee_usd == 20.0
    assert settings.model_source.price_overrides == {
        "my-private-model": (1.0, 2.0)
    }


def test_settings_load_yaml_without_model_source(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("company:\n  name: TestCo\n")
    settings = KompanySettings.load(str(config))
    assert settings.model_source is None


def test_settings_load_honors_data_dir_from_yaml(tmp_path, monkeypatch):
    """Issue #21: a YAML data_dir was silently ignored (production
    contamination incident 2026-06-12). Env override still wins."""
    monkeypatch.delenv("KOMPANY_DATA_DIR", raising=False)
    cfg = tmp_path / "config.yaml"
    target = tmp_path / "isolated-data"
    cfg.write_text(f"data_dir: {target}\n")
    from kompany.config.settings import KompanySettings

    s = KompanySettings.load(str(cfg))
    assert s.data_dir == target

    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "env-wins"))
    s2 = KompanySettings.load(str(cfg))
    assert s2.data_dir == tmp_path / "env-wins"


def test_settings_load_self_update_knobs_from_yaml(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "self_update_budget_cap_usd: 5.5\n"
        "self_update_max_turns: 99\n"
        "self_update_test_cmd: pytest -k smoke\n"
    )
    from kompany.config.settings import KompanySettings

    s = KompanySettings.load(str(cfg))
    assert s.self_update_budget_cap_usd == 5.5
    assert s.self_update_max_turns == 99
    assert s.self_update_test_cmd == "pytest -k smoke"
