"""Tests for Pro Template discovery via plugin entry points.

The built-in Templates service must surface Pro plugin Templates next to
Core's filesystem-packaged ones with no special handling at call sites.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _make_pro_template_dir(tmp_path: Path, template_id: str) -> Path:
    d = tmp_path / template_id
    d.mkdir()
    manifest = {
        "id": template_id,
        "name": "Pro SaaS Starter",
        "mission_title": "Build SaaS Pro",
        "mission_md_path": "mission.md",
        "initial_budget": 7500.0,
        "revenue_target": 30000.0,
        "customer_target": 100,
        "enabled_agents": [
            "ceo", "cfo", "cto", "cpo", "cmo", "cro",
            "coo", "csa", "ciso", "cos", "cv",
        ],
        "agent_config_overrides": {},
        "suggested_directives": ["Define ICP", "Launch landing page"],
        "rpg_theme": "modern_loft_office",
        "glossary": [],
    }
    (d / "manifest.json").write_text(json.dumps(manifest))
    (d / "mission.md").write_text("# Pro SaaS Starter\nDeep playbook.\n")
    return d


def test_pro_templates_appear_in_list(monkeypatch, tmp_path, kompany_engine):
    """A Pro plugin's Template surfaces in Templates.list_templates()."""
    from kompany.plugins.contract import Template

    pro_dir = _make_pro_template_dir(tmp_path, "saas-pro-starter")
    manifest_path = pro_dir / "manifest.json"

    class ProTemplate(Template):
        template_id = "saas-pro-starter"
        display_name = "Pro SaaS Starter"

        def __init__(self):
            self.manifest_path = manifest_path

    def fake_discover():
        return {
            "workflow": [], "soul": [], "integration": [],
            "template": [ProTemplate()],
            "tool": [],
        }

    monkeypatch.setattr("kompany.plugins.loader.discover", fake_discover)

    templates = kompany_engine.templates.list_templates()
    ids = {t.id for t in templates}
    assert "saas-pro-starter" in ids
    # built-in templates still present
    assert "saas-startup" in ids


def test_pro_template_show_returns_manifest(monkeypatch, tmp_path, kompany_engine):
    from kompany.plugins.contract import Template

    pro_dir = _make_pro_template_dir(tmp_path, "ecom-pro-starter")
    manifest_path = pro_dir / "manifest.json"

    class ProTemplate(Template):
        template_id = "ecom-pro-starter"

        def __init__(self):
            self.manifest_path = manifest_path

    monkeypatch.setattr(
        "kompany.plugins.loader.discover",
        lambda: {"workflow": [], "soul": [], "integration": [],
                 "template": [ProTemplate()], "tool": []},
    )

    tpl = kompany_engine.templates.show("ecom-pro-starter")
    assert tpl.id == "ecom-pro-starter"
    assert tpl.initial_budget == 7500.0


def test_pro_template_with_missing_manifest_path_skipped(monkeypatch, kompany_engine):
    """A Pro plugin without manifest_path must not break discovery."""
    from kompany.plugins.contract import Template

    class HalfBakedTemplate(Template):
        template_id = "broken"
        # manifest_path intentionally None

    monkeypatch.setattr(
        "kompany.plugins.loader.discover",
        lambda: {"workflow": [], "soul": [], "integration": [],
                 "template": [HalfBakedTemplate()], "tool": []},
    )

    # Core templates still come through; broken plugin silently dropped.
    templates = kompany_engine.templates.list_templates()
    ids = {t.id for t in templates}
    assert "broken" not in ids
    assert "saas-startup" in ids


def test_loader_failure_does_not_break_list(monkeypatch, kompany_engine):
    """If loader.discover() itself raises, list_templates() still returns Core."""
    def boom():
        raise RuntimeError("simulated loader failure")

    monkeypatch.setattr("kompany.plugins.loader.discover", boom)

    templates = kompany_engine.templates.list_templates()
    ids = {t.id for t in templates}
    assert "saas-startup" in ids  # Core unaffected


@pytest.fixture
def kompany_engine():
    """Minimal Templates service fixture.

    list_templates / show / _iter_template_dirs do not touch the DB,
    ledger, projects, or audit — they only read filesystem manifests
    plus plugin entry points. Pass placeholders to keep the test fast.
    """
    from kompany.state.templates import Templates

    class _Engine:
        pass

    engine = _Engine()
    engine.templates = Templates(db=None, ledger=None, projects=None, audit=None)
    return engine
