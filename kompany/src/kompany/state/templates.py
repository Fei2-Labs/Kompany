"""Company template service: list / show / apply.

Templates live as filesystem directories under
``kompany/src/kompany/templates/<id>/`` and ship with the PyPI wheel via
``package_data`` in ``pyproject.toml``. The service uses
:mod:`importlib.resources` so it reads files correctly whether the package
is installed from a wheel, run from a checkout, or zipped into an
executable bundle.

A template is **applied** by:

1. Writing ``company_config`` keys (``template_id``, ``mission``,
   ``initial_budget``, ``enabled_agents``).
2. Recording the ``initial_budget`` as a ledger ``INCOME`` entry so the
   player sees the cash on day 1.
3. Creating one ``status='draft'`` project per suggested directive (or
   one project for ``override_directive`` when supplied).
4. Writing a ``company.template_applied`` audit event.

Re-applying the same template is rejected unless ``force=True`` is set;
the second call then overwrites the config keys, ledgers another
``initial_budget`` row, and creates fresh draft projects.
"""

from __future__ import annotations

import importlib.resources
import json
from typing import Any

from kompany.state.audit import AuditLog
from kompany.state.database import Database
from kompany.state.ledger import Ledger
from kompany.state.models import LedgerCategory, Project, ProjectType
from kompany.state.projects import Projects
from kompany.state.templates_model import CompanyTemplate, TemplateApplyResult


# Resource root inside the ``kompany`` package. Using importlib.resources
# means the same path works for editable installs, wheel installs, and
# zipped distributions — never use ``__file__`` here.
_PACKAGE = "kompany"
_TEMPLATES_DIR = "templates"
_COMMUNITY_DIR = "community"


class TemplateNotFound(LookupError):
    """Raised when ``show`` / ``apply`` cannot locate a template id."""


class TemplateAlreadyApplied(RuntimeError):
    """Raised by ``apply`` when ``company_config['template_id']`` is already
    set and ``force=False``."""


class Templates:
    """Service over filesystem-packaged company templates."""

    def __init__(
        self,
        db: Database,
        ledger: Ledger,
        projects: Projects,
        audit: AuditLog,
    ):
        self.db = db
        self.ledger = ledger
        self.projects = projects
        self.audit = audit

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_templates(self) -> list[CompanyTemplate]:
        """Return every valid template found under the built-in directory
        and the optional ``community/`` subdirectory.

        Templates whose ``manifest.json`` fails to parse are silently
        skipped so a single corrupt community PR doesn't break ``list``
        for everyone else; corrupt manifests are surfaced through
        ``show`` instead.
        """
        results: list[CompanyTemplate] = []
        seen_ids: set[str] = set()
        for entry in self._iter_template_dirs():
            try:
                tpl = self._load_manifest(entry)
            except Exception:
                continue
            if tpl.id in seen_ids:
                continue
            seen_ids.add(tpl.id)
            results.append(tpl)
        # Stable order: built-ins first (in discovery order), community
        # by id. We sort the full list by id for predictable CLI output.
        results.sort(key=lambda t: t.id)
        return results

    def show(self, template_id: str) -> CompanyTemplate:
        """Return one template by id, raising :class:`TemplateNotFound` if
        the directory exists but the manifest can't be parsed, or if the
        id is unknown."""
        for entry in self._iter_template_dirs():
            if entry.name != template_id:
                continue
            return self._load_manifest(entry)
        raise TemplateNotFound(
            f"template not found: {template_id!r}. "
            f"Run `kompany template list` to see available ids."
        )

    def show_with_mission(self, template_id: str) -> tuple[CompanyTemplate, str]:
        """Return ``(template, mission_md_text)`` — convenience for callers
        that want both the structured manifest and the rendered mission
        body in one read."""
        tpl = self.show(template_id)
        mission = self._read_mission(template_id, tpl)
        return tpl, mission

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def is_applied(self) -> str | None:
        """Return the currently-applied template id, or ``None`` if no
        template has been applied yet."""
        row = self.db.execute(
            "SELECT value FROM company_config WHERE key = ?",
            ("template_id",),
        ).fetchone()
        return row["value"] if row else None

    def apply(
        self,
        template_id: str,
        force: bool = False,
        override_budget: float | None = None,
        override_directive: str | None = None,
        override_revenue_target: float | None = None,
        override_customer_target: int | None = None,
        override_deadline: str | None = None,
    ) -> TemplateApplyResult:
        """Apply a template to the current company.

        Idempotent only with ``force=True``: calling twice without it
        raises :class:`TemplateAlreadyApplied` so the player can't
        accidentally double-fund the ledger or pollute their inbox.
        """
        existing = self.is_applied()
        if existing is not None and not force:
            raise TemplateAlreadyApplied(
                f"template '{existing}' is already applied. "
                f"Pass force=True to overwrite."
            )

        tpl, mission_md = self.show_with_mission(template_id)

        budget = (
            float(override_budget)
            if override_budget is not None
            else float(tpl.initial_budget)
        )
        revenue_target = (
            float(override_revenue_target)
            if override_revenue_target is not None
            else float(tpl.revenue_target or 0.0)
        )
        customer_target: int | None
        if override_customer_target is not None:
            customer_target = int(override_customer_target)
        else:
            customer_target = tpl.customer_target

        # ------------------------------------------------------------------
        # 1. company_config
        # ------------------------------------------------------------------
        self._write_config("template_id", tpl.id)
        self._write_config("mission", mission_md)
        self._write_config("mission_title", tpl.mission_title)
        self._write_config("initial_budget", str(budget))
        self._write_config("enabled_agents", json.dumps(tpl.enabled_agents))
        self._write_config("rpg_theme", tpl.rpg_theme)
        if tpl.agent_config_overrides:
            self._write_config(
                "agent_config_overrides",
                json.dumps(tpl.agent_config_overrides),
            )
        self.db.commit()

        # Persist the founder-state targets snapshot. We do this in the
        # template service (rather than in the engine) so any caller —
        # including direct ``Templates.apply`` invocations — writes a
        # consistent baseline that ``targets.get_targets`` can read.
        from kompany.state.targets import CompanyTargets, set_targets

        founder_targets = CompanyTargets(
            initial_budget=budget,
            revenue_target=revenue_target,
            customer_target=customer_target,
            deadline=override_deadline or None,
            source="founder",
        )
        set_targets(self.db, founder_targets)

        # Install the template-shipped glossary entries (additive — never
        # clobbers terms the founder may have curated by hand between
        # template applications). Glossary-and-drift-detection task 05-19.
        glossary_installed = 0
        if tpl.glossary:
            from kompany.state.glossary import GlossaryService

            glossary_installed = GlossaryService(self.db).bulk_install_from_template(
                [g.model_dump(mode="python") for g in tpl.glossary]
            )

        # ------------------------------------------------------------------
        # 2. Ledger — initial capital
        # ------------------------------------------------------------------
        if budget > 0:
            self.ledger.record(
                amount=budget,
                description=(
                    f"Initial capital (template: {tpl.id})"
                    + (" [force]" if force and existing else "")
                ),
                category=LedgerCategory.INCOME,
                approved_by="master",
            )

        # ------------------------------------------------------------------
        # 3. Suggested directives → draft projects
        # ------------------------------------------------------------------
        directives: list[str]
        if override_directive:
            directives = [override_directive]
        else:
            directives = list(tpl.suggested_directives)

        project_ids: list[str] = []
        for directive_text in directives:
            project = Project(
                name=directive_text[:120],
                type=ProjectType.OPERATIONAL,
                plan={
                    "mission": mission_md,
                    "mission_title": tpl.mission_title,
                    "suggested_directive": directive_text,
                    "template_id": tpl.id,
                },
                assigned_agents=list(tpl.enabled_agents),
            )
            # ``ProjectStatus`` enum doesn't have a ``DRAFT`` member —
            # template directives live before the normal lifecycle. We
            # bypass ``Projects.create`` and write the raw 'draft' string
            # via :meth:`_insert_draft_project`.
            self._insert_draft_project(project, directive_text)
            project_ids.append(project.id)

        # ------------------------------------------------------------------
        # 4. Audit
        # ------------------------------------------------------------------
        self.audit.record(
            event_type="company.template_applied",
            action=f"Applied company template '{tpl.id}'",
            detail={
                "template_id": tpl.id,
                "name": tpl.name,
                "mission_title": tpl.mission_title,
                "initial_budget": budget,
                "revenue_target": revenue_target,
                "customer_target": customer_target,
                "deadline": override_deadline or None,
                "enabled_agents": tpl.enabled_agents,
                "project_ids": project_ids,
                "force": bool(force and existing),
                "override_budget": override_budget,
                "override_directive": override_directive,
                "glossary_terms_installed": glossary_installed,
            },
        )

        return TemplateApplyResult(
            template_id=tpl.id,
            name=tpl.name,
            mission=mission_md,
            initial_budget=budget,
            enabled_agents=tpl.enabled_agents,
            project_ids=project_ids,
            force=bool(force and existing),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _iter_template_dirs(self) -> list[Any]:
        """Walk the packaged ``templates/`` directory and yield each
        template's own directory traversable.

        Order: built-in templates first (alphabetical by id thanks to the
        sorted scan in ``list_templates``), then community templates.
        The ``community/`` directory is optional — missing it is fine.
        """
        root = importlib.resources.files(_PACKAGE).joinpath(_TEMPLATES_DIR)
        results: list[Any] = []
        try:
            children = list(root.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            return results
        for child in children:
            # Built-in template directory must contain a manifest.json
            if child.is_dir() and child.name != _COMMUNITY_DIR:
                if child.joinpath("manifest.json").is_file():
                    results.append(child)
        community_root = root.joinpath(_COMMUNITY_DIR)
        try:
            community_exists = community_root.is_dir()
        except Exception:
            community_exists = False
        if community_exists:
            try:
                for child in community_root.iterdir():
                    if (
                        child.is_dir()
                        and child.joinpath("manifest.json").is_file()
                    ):
                        results.append(child)
            except (FileNotFoundError, NotADirectoryError):
                pass
        return results

    def _load_manifest(self, template_dir: Any) -> CompanyTemplate:
        """Parse ``<template_dir>/manifest.json`` into a CompanyTemplate."""
        manifest_path = template_dir.joinpath("manifest.json")
        raw = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return CompanyTemplate.model_validate(data)

    def _read_mission(
        self,
        template_id: str,
        tpl: CompanyTemplate,
    ) -> str:
        """Read the mission markdown body for a template.

        The path is **relative to the template directory** (per the
        CompanyTemplate contract). We tolerate the path being missing by
        falling back to the mission_title — this matters for community
        templates that may forget to ship the .md file.
        """
        for entry in self._iter_template_dirs():
            if entry.name != template_id:
                continue
            mission_file = entry.joinpath(tpl.mission_md_path)
            try:
                if mission_file.is_file():
                    return mission_file.read_text(encoding="utf-8")
            except (FileNotFoundError, NotADirectoryError):
                pass
            break
        return f"# {tpl.mission_title}\n"

    def _write_config(self, key: str, value: str) -> None:
        self.db.execute(
            """INSERT INTO company_config (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = datetime('now')""",
            (key, value),
        )

    def _insert_draft_project(self, project: Project, directive_text: str) -> None:
        """Insert a project row with literal ``status='draft'``.

        ``ProjectStatus`` enum doesn't include ``DRAFT`` (template
        directives are pre-approval, not in the normal lifecycle), so we
        bypass ``Projects.create`` to write the raw string. Everything
        else uses the same schema as the regular insert.
        """
        self.db.execute(
            """INSERT INTO projects
               (id, name, type, status, target_amount, funded_amount,
                triggers_directive_id, plan, assigned_agents)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project.id,
                project.name,
                project.type.value,
                "draft",
                project.target_amount,
                project.funded_amount,
                project.triggers_directive_id,
                json.dumps(project.plan),
                json.dumps(project.assigned_agents),
            ),
        )
        self.db.commit()

    def get_applied_summary(self) -> dict[str, Any] | None:
        """Return a small dict summarizing the currently-applied template
        for callers (CLI ``init``/``onboard``, dashboard) — or ``None`` if
        nothing is applied yet."""
        template_id = self.is_applied()
        if template_id is None:
            return None
        try:
            tpl = self.show(template_id)
        except TemplateNotFound:
            return {"template_id": template_id, "status": "missing_manifest"}
        return {
            "template_id": tpl.id,
            "name": tpl.name,
            "mission_title": tpl.mission_title,
            "initial_budget": tpl.initial_budget,
            "enabled_agents": tpl.enabled_agents,
        }
