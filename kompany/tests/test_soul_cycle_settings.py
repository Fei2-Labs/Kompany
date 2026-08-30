from __future__ import annotations

from kompany.config.settings import KompanySettings


def test_settings_loads_soul_cycle_overrides(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "soul_cycle_overrides:\n"
        "  linkedin_growth:\n"
        "    scheduler_mode: native\n"
        "    max_external_proposals_per_cycle: 1\n"
    )

    settings = KompanySettings.load(str(config))

    assert settings.soul_cycle_overrides == {
        "linkedin_growth": {
            "scheduler_mode": "native",
            "max_external_proposals_per_cycle": 1,
        }
    }
