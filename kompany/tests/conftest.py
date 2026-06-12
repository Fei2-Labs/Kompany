"""Shared test fixtures.

Environment isolation: ``KompanySettings.load(None)`` resolves the
founder's real ``<data_dir>/config.yaml`` (06-12 fix), so without this
guard the suite would read whatever ``~/.kompany/config.yaml`` or an
exported ``KOMPANY_DATA_DIR`` happens to contain on the developer's
machine — flaky, and a test could accidentally touch real company data.
Every test gets a throwaway data dir unless it explicitly overrides.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_kompany_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMPANY_DATA_DIR", str(tmp_path / "kompany-data"))
