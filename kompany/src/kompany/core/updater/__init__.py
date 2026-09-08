"""One-button update (Stage C step 9: update UX + step 4: updater).

The engine updates itself from signed GitHub Releases — never from a git
checkout. ``check`` compares the installed Core/Pro versions with the
latest release; ``apply`` downloads the wheels, verifies sha256 (and the
build-provenance attestation when ``gh`` is present), installs them into a
fresh venv under ``<data_dir>/releases/<version>/``, backs the database up,
flips the ``releases/current`` symlink and exits so the supervisor restarts
the new version. The first boot after a switch runs the doctor and rolls
back to the previous release when it fails.

Modes: ``manual`` (default — the app only shows that an update exists) and
``automatic_when_idle`` (the ticker applies it when no agent is working).
"""

from kompany.core.updater.pipeline import apply_update, check_for_update, rollback_update, verify_after_restart

__all__ = ["apply_update", "check_for_update", "rollback_update", "verify_after_restart"]
