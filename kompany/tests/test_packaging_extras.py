"""Packaging extras must be self-sufficient.

The desktop sidecar is built from ``pip install '.[api,desktop]'`` only, so
anything the API needs at import time has to be declared in ``api`` itself,
not arrive transitively through another extra such as ``mcp``.
"""

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _extra(name: str) -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text())
    reqs = data["project"]["optional-dependencies"][name]
    return {re.match(r"[A-Za-z0-9_.-]+", req).group(0).lower() for req in reqs}


def test_api_extra_declares_form_parser() -> None:
    # The dashboard login route uses fastapi.Form, which refuses to register
    # without python-multipart: the sidecar crashed at startup when only
    # `mcp` pulled it in.
    assert "python-multipart" in _extra("api")
