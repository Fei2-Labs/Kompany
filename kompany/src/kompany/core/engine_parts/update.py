"""Engine ops for one-button update (Stage C step 9). Same dict on four surfaces."""

from __future__ import annotations

import threading
from typing import Any


class UpdateMixin:
    def update_status(self) -> dict[str, Any]:
        from kompany.core.updater.pipeline import status

        return status(self)

    def update_check(self) -> dict[str, Any]:
        from kompany.core.updater.pipeline import check_for_update

        return check_for_update(self)

    def update_apply(self, version: str | None = None, *, background: bool = True) -> dict[str, Any]:
        """Start the update. ``background`` returns immediately; poll ``update_status``."""
        from kompany.core.updater.pipeline import apply_update, status

        if not background:
            return apply_update(self, version)
        threading.Thread(target=apply_update, args=(self, version), name="kompany-update", daemon=True).start()
        return {**status(self), "started": True}

    def update_rollback(self) -> dict[str, Any]:
        from kompany.core.updater.pipeline import rollback_update

        return rollback_update(self)

    def update_set_mode(self, mode: str) -> dict[str, Any]:
        """manual | automatic_when_idle — persisted in company_config, audited."""
        if mode not in ("manual", "automatic_when_idle"):
            raise ValueError("mode must be manual or automatic_when_idle")
        self.settings.update_mode = mode
        try:
            self.db.execute(
                """INSERT INTO company_config (key, value, updated_at) VALUES ('update_mode', ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')""", (mode,))
            self.db.commit()
        except Exception:  # noqa: BLE001 — settings still hold it for this process
            pass
        self.audit.record("update.mode_changed", f"Update mode → {mode}", detail={"mode": mode})
        return self.update_status()


__all__ = ["UpdateMixin"]
