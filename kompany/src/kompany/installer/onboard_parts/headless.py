"""Headless onboarding entry points (REST + Tauri shell) — no typer/rich.

Split out of ``onboard.py`` (ADR-0003). Tests monkeypatch helpers like
``kompany.installer.onboard._ping_llm`` on the ``onboard`` module, so
every patch-sensitive helper is resolved through ``_onboard()`` at
call time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from kompany.installer.onboard_parts.common import (
    PROVIDER_ENV_VARS,
    PROVIDER_VAULT_KEYS,
    SUPPORTED_PROVIDERS,
    OnboardError,
    OnboardResult,
)


def _onboard():
    """Late import of the facade module so monkeypatches apply."""
    from kompany.installer import onboard

    return onboard


def onboard_headless(
    data_dir: Path | str,
    provider: str,
    api_key: str,
    template_id: str,
    directive: str | None = None,
    base_url: str | None = None,
    *,
    initial_budget: float | None = None,
    revenue_target: float | None = None,
    customer_target: int | None = None,
    deadline: str | None = None,
    glossary_overrides: dict[str, str] | None = None,
    engine_factory: Callable[[], Any] | None = None,
) -> OnboardResult:
    """Run a fully-headless onboarding pass.

    Pure function with no typer / rich dependency — callable from the
    REST endpoint, the Tauri shell, or any test. The wizard's branching
    UX (prompts, retry/abort, idempotency dialog) collapses to a clean
    contract:

    * If the data dir already contains a usable ``kompany.db``, we treat
      that as a reuse: no overwrite, no re-ping. Returned result has
      ``status='reused'``.
    * Otherwise (fresh or partial), we apply the supplied template and
      stash the API key in the vault.
    * Any failure raises :class:`OnboardError` with a code the caller
      can render verbatim.

    The :class:`OnboardResult` returned matches the CLI's contract so
    tests can assert on the same dataclass either way.
    """
    if not provider:
        raise OnboardError("missing_provider", "provider is required")
    if provider not in SUPPORTED_PROVIDERS:
        raise OnboardError(
            "unknown_provider",
            f"unknown provider {provider!r}; "
            f"choose one of: {', '.join(SUPPORTED_PROVIDERS)}",
        )
    if not api_key:
        raise OnboardError("missing_api_key", "api_key is required")
    if not template_id:
        raise OnboardError("missing_template", "template_id is required")
    if provider == "custom" and not (base_url and base_url.strip()):
        raise OnboardError(
            "missing_base_url",
            "custom provider requires a base_url (OpenAI-compatible endpoint)",
        )

    data_dir = Path(data_dir).expanduser().resolve()
    ok, msg = _onboard()._ensure_data_dir(data_dir)
    if not ok:
        raise OnboardError("data_dir_error", msg)

    # Idempotency: if the DB exists with a template applied, reuse.
    state = _onboard()._existing_install_state(data_dir)
    reused = bool(state["db_exists"]) and not state["partial"]

    # The downstream engine reads ``KOMPANY_DATA_DIR`` from the
    # environment, exactly like the CLI wizard. Save/restore so we don't
    # leak across long-running REST processes.
    prior_env = os.environ.get("KOMPANY_DATA_DIR")
    os.environ["KOMPANY_DATA_DIR"] = str(data_dir)

    result = OnboardResult(
        status="reused" if reused else "completed",
        data_dir=data_dir,
    )
    try:
        if engine_factory is not None:
            engine = engine_factory()
        else:
            from kompany.core.engine import KompanyEngine

            engine = KompanyEngine()

        # ----- Provider + API key + ping ---------------------------------
        result.provider = provider
        vault_field = PROVIDER_VAULT_KEYS.get(provider)

        if reused:
            result.api_key_storage = "reused"
            result.ping_status = "skipped"
        else:
            storage = "env"
            if vault_field and getattr(engine.settings, "vault_key", ""):
                try:
                    engine.credentials.set(vault_field, api_key)
                    storage = "vault"
                except Exception as exc:  # noqa: BLE001
                    result.notes.append(f"vault write failed ({exc}); using process env only")
            else:
                result.notes.append(
                    f"vault key not configured; set {PROVIDER_ENV_VARS.get(provider, '?')} "
                    "in your environment for persistent storage"
                )
            if vault_field:
                setattr(engine.settings, vault_field, api_key)
            # For custom provider, also persist base_url alongside the API key.
            if provider == "custom" and base_url:
                base_url_value = base_url.strip()
                if getattr(engine.settings, "vault_key", ""):
                    try:
                        engine.credentials.set("custom_base_url", base_url_value)
                    except Exception as exc:  # noqa: BLE001
                        result.notes.append(
                            f"custom_base_url vault write failed ({exc}); using process env only"
                        )
                setattr(engine.settings, "custom_base_url", base_url_value)
            result.api_key_storage = storage

            # For custom provider, the model name must be discovered from
            # the endpoint's /models index; calling _ping_llm without a
            # model_override falls through to "custom-unset" and the
            # upstream rejects with a misleading 503 / invalid-request.
            # Mirrors the discovery the standalone /onboarding/ping path
            # in interfaces/api.py already does.
            model_override: str | None = None
            if provider == "custom" and base_url:
                try:
                    available = _onboard()._list_custom_models(base_url.strip(), api_key)
                    model_override = _onboard()._pick_latest_custom_model(available)
                except Exception as exc:  # noqa: BLE001
                    result.notes.append(
                        f"custom model discovery failed ({exc}); ping will use default"
                    )
                # Override the engine's three model tiers so every
                # downstream agent call routes through the custom
                # endpoint instead of falling through to the Anthropic
                # default model names (which LLMClient routes to the
                # Anthropic SDK and which would auth-fail with the
                # custom-provider key). Use the same picked model for
                # all three tiers — the custom endpoint's pricing tier
                # is opaque to us, so we don't pretend to differentiate.
                if model_override:
                    engine.settings.model_apex = model_override
                    engine.settings.model_primary = model_override
                    engine.settings.model_economy = model_override
                    # Persist so future engine boots (Tauri sidecar
                    # restart, REST process recycle) re-apply the same
                    # tier override. Engine.__init__ reads this and
                    # overrides settings before any LLM call wires up.
                    try:
                        engine.db.execute(
                            """INSERT INTO company_config (key, value, updated_at)
                               VALUES (?, ?, datetime('now'))
                               ON CONFLICT(key) DO UPDATE SET
                                 value = excluded.value,
                                 updated_at = datetime('now')""",
                            ("custom_model_picked", model_override),
                        )
                    except Exception as exc:  # noqa: BLE001
                        result.notes.append(
                            f"custom_model_picked persist failed ({exc}); "
                            "ping will work this session but tier overrides "
                            "are lost on engine restart"
                        )
            ok, detail = _onboard()._ping_llm(
                provider,
                api_key,
                settings_factory=lambda: engine.settings,
                model_override=model_override,
            )
            if ok and detail == "skipped_test_mode":
                result.ping_status = "skipped_test_mode"
            elif ok:
                result.ping_status = "ok"
            else:
                # Surface as an error so the wizard form can retry.
                raise OnboardError("ping_failed", f"{provider} ping failed: {detail}")

        # ----- Model source (06-11-harness-execution-leg PR5b) -----------
        # The headless API-key path maps to a ``custom_api`` source;
        # zero-key subscription sources are an interactive/Settings flow.
        if not reused:
            setter = getattr(engine, "set_model_source", None)
            if setter is not None:
                try:
                    setter({"kind": "custom_api"})
                    result.model_source_kind = "custom_api"
                except Exception as exc:  # noqa: BLE001 — editable later in Settings
                    result.notes.append(f"model source not saved: {exc}")

        # ----- Template apply -------------------------------------------
        # Mission-targets task (05-19): the four override knobs flow
        # through to ``engine.apply_template`` so the founder's
        # explicit numbers beat the template manifest's presets.
        # Priority (set in ``Templates.apply``): override > manifest >
        # unset (0.0 / None).
        if reused:
            applied = engine.templates.is_applied()
            result.template_id = applied or template_id
        else:
            try:
                applied_id = engine.templates.is_applied()
                if applied_id == template_id and not any(
                    v is not None
                    for v in (
                        initial_budget,
                        revenue_target,
                        customer_target,
                        deadline,
                    )
                ):
                    result.template_id = template_id
                else:
                    # Use ``apply_template`` with the four overrides. The
                    # engine routes them through Templates.apply, which
                    # persists the founder-state targets snapshot.
                    apply_kwargs: dict[str, Any] = {
                        "force": applied_id is not None,
                    }
                    if initial_budget is not None:
                        apply_kwargs["override_budget"] = float(initial_budget)
                    if revenue_target is not None:
                        apply_kwargs["override_revenue_target"] = float(revenue_target)
                    if customer_target is not None:
                        apply_kwargs["override_customer_target"] = int(customer_target)
                    if deadline is not None and deadline != "":
                        apply_kwargs["override_deadline"] = str(deadline)
                    engine.apply_template(template_id, **apply_kwargs)
                    result.template_id = template_id
            except ValueError as exc:
                raise OnboardError("template_error", str(exc)) from exc

        # ----- Glossary overrides (onboard-v2 task 05-19) ----------------
        # Founder-edited definitions are applied AFTER the template's
        # bulk_install so they overlay the template defaults. Forbidden-
        # synonym lists are preserved (we only mutate the definition).
        # Unknown terms (founder typed a new term in the inline editor)
        # are appended with ``added_by='founder'`` and empty forbidden
        # list. Failures are non-fatal: onboarding still succeeds.
        if not reused and glossary_overrides:
            try:
                glossary_svc = getattr(engine, "glossary", None)
                if glossary_svc is not None:
                    for term, definition in glossary_overrides.items():
                        if not isinstance(term, str) or not isinstance(definition, str):
                            continue
                        term = term.strip()
                        definition = definition.strip()
                        if not term or not definition:
                            continue
                        existing = glossary_svc.get(term)
                        if existing is not None:
                            glossary_svc.update(term, definition=definition)
                        else:
                            glossary_svc.add(
                                term=term,
                                definition=definition,
                                added_by="founder",
                            )
            except Exception as exc:  # noqa: BLE001
                result.notes.append(f"glossary overrides skipped: {exc}")

        # ----- Kick off team target feasibility review -------------------
        # Non-blocking: failure here is informational — onboarding still
        # succeeds. The review writes one approval_request the founder
        # can act on later via ``kompany target review`` /
        # ``/approvals/<id>/approve``.
        if not reused:
            try:
                review_payload = engine.run_target_feasibility_review()
            except Exception as exc:  # noqa: BLE001
                result.notes.append(f"target feasibility review skipped: {exc}")
            else:
                if review_payload and isinstance(review_payload, dict):
                    result.targets_review_id = review_payload.get("id")

        # ----- First directive (optional) -------------------------------
        if directive and directive.strip():
            result.directive_text = directive
            try:
                outcome = engine.process_directive(directive)
            except Exception as exc:  # noqa: BLE001
                result.directive_status = "error"
                result.directive_message = str(exc)
                result.notes.append(f"first directive failed: {exc}")
            else:
                result.directive_status = getattr(outcome, "status", None)
                result.directive_message = getattr(outcome, "message", "") or ""

        return result
    finally:
        if prior_env is None:
            os.environ.pop("KOMPANY_DATA_DIR", None)
        else:
            os.environ["KOMPANY_DATA_DIR"] = prior_env


def is_onboarded(data_dir: Path | str) -> dict[str, Any]:
    """Return a snapshot describing whether onboarding has been done.

    Read-only — safe to call from a REST handler on every request. The
    function inspects ``kompany.db`` directly (no engine spin-up) so a
    fresh install can answer this in microseconds.
    """
    data_dir = Path(data_dir).expanduser()
    state = _onboard()._existing_install_state(data_dir)
    if not state["db_exists"] or state["partial"]:
        return {"onboarded": False, "template_id": None, "provider": None}

    template_id = state.get("template_id")
    provider: str | None = None
    if state.get("has_vault_rows"):
        import sqlite3

        try:
            conn = sqlite3.connect(str(data_dir / "kompany.db"))
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT name FROM credential_vault"
                ).fetchall()
            finally:
                conn.close()
            stored = {row["name"] for row in rows}
            for prov, vault_key in PROVIDER_VAULT_KEYS.items():
                if vault_key in stored:
                    provider = prov
                    break
        except sqlite3.Error:
            provider = None

    onboarded = template_id is not None
    return {
        "onboarded": bool(onboarded),
        "template_id": template_id,
        "provider": provider,
    }
