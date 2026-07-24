"""Kompany — Autonomous business operating system for solo founders."""

try:
    # Installed (wheel/sdist) case: read the real installed version from
    # package metadata, so /version reflects what was actually deployed
    # instead of a hand-maintained literal that silently goes stale every
    # release (release.yml only bumps pyproject.toml's [project] version).
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("kompany")
except Exception:  # noqa: BLE001 — e.g. running from source with no dist-info
    __version__ = "0.0.0+unknown"

from kompany.interfaces.sdk import Kompany

__all__ = ["Kompany", "__version__"]
