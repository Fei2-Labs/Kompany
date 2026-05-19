"""Run-id context for Kompany's single-process multi-agent runtime.

Every directive that enters :class:`KompanyEngine` gets a fresh ``run_id``
(formatted ``r_<ulid>``). State writes during that directive — audit log,
agent memories, decisions, tasks, ledger, approval requests, LLM costs —
read the active id from :func:`current_run_id` and persist it alongside
each row.

The module is intentionally tiny: standard-library ``contextvars`` plus a
self-contained Crockford base32 ULID generator so this works without a
network install of ``python-ulid``. The ULID format keeps the id
lexicographically sortable by time and human-recognisable in logs.

Outside an active :func:`run_scope` block, :func:`current_run_id` returns
``None`` rather than raising — CLI bootstrap paths, backup scripts, and
test helpers that touch state stores without a directive are valid
callers, and their writes simply leave the ``run_id`` column ``NULL``.
"""

from __future__ import annotations

import contextvars
import os
import re
import secrets
import time
from contextlib import contextmanager
from typing import Iterator

# Crockford's base32 alphabet (no I, L, O, U).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Public regex for code that wants to validate values it receives. Matches
# the Pydantic pattern used by ``05-18-episode-schema-freeze``.
RUN_ID_PATTERN = r"^r_[0-9A-HJKMNP-TV-Z]{26}$"
_RUN_ID_RE = re.compile(RUN_ID_PATTERN)

_run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "kompany_run_id", default=None
)
_parent_run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "kompany_parent_run_id", default=None
)


def _encode_ulid() -> str:
    """Generate a 26-character Crockford base32 ULID body.

    First 10 chars encode 48 bits of millisecond timestamp; remaining 16
    chars encode 80 bits of cryptographically random data. This matches
    the on-the-wire form ``python-ulid`` would produce and satisfies
    ``RUN_ID_PATTERN``.
    """
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_bits = int.from_bytes(secrets.token_bytes(10), "big")
    value = (ts_ms << 80) | rand_bits

    # 26 base32 chars = 130 bits; ULID uses 128, so the top two bits are
    # always zero. We encode high-to-low so the timestamp ends up at the
    # front, keeping ids sortable.
    chars = []
    for shift in range(125, -1, -5):
        chars.append(_CROCKFORD[(value >> shift) & 0x1F])
    return "".join(chars)


def new_run_id() -> str:
    """Return a freshly generated ``r_<ulid>`` identifier."""
    return f"r_{_encode_ulid()}"


def is_valid_run_id(value: str | None) -> bool:
    """Return True if ``value`` is a syntactically valid run id."""
    return isinstance(value, str) and bool(_RUN_ID_RE.match(value))


def current_run_id() -> str | None:
    """Return the run id active in this context, or ``None``.

    Never raises. Code paths that run outside a directive (CLI init,
    backup scripts, ad-hoc store access in tests) get ``None`` and the
    corresponding DB column stays ``NULL``.
    """
    return _run_id_var.get()


def parent_run_id() -> str | None:
    """Return the parent run id of the active scope, or ``None``."""
    return _parent_run_id_var.get()


def set_run_id(run_id: str | None) -> contextvars.Token:
    """Set the run id for the current context. Prefer :func:`run_scope`.

    Returns the ``Token`` so callers that really need manual lifecycle
    (e.g. background workers that span multiple awaits) can reset it.
    """
    if run_id is not None and not is_valid_run_id(run_id):
        raise ValueError(
            f"run_id must match {RUN_ID_PATTERN!r}, got {run_id!r}"
        )
    return _run_id_var.set(run_id)


def reset_run_id(token: contextvars.Token) -> None:
    """Reset the run id ContextVar using a token from :func:`set_run_id`."""
    _run_id_var.reset(token)


@contextmanager
def run_scope(
    run_id: str | None = None,
    parent: str | None = None,
) -> Iterator[str]:
    """Activate a run id for the duration of a ``with`` block.

    Usage::

        with run_scope() as rid:                  # auto-generate
            engine.process_directive(text)

        with run_scope(new_run_id(), parent=current_run_id()) as child:
            ...  # child run nested inside its parent

    If ``run_id`` is None a fresh one is generated. ``parent`` defaults to
    whatever was active when the scope opens, so nesting "just works":

        with run_scope() as outer:
            with run_scope() as inner:
                assert parent_run_id() == outer

    The yielded value is the resolved run id so callers can stash it
    without re-querying.
    """
    rid = run_id or new_run_id()
    if not is_valid_run_id(rid):
        raise ValueError(
            f"run_id must match {RUN_ID_PATTERN!r}, got {rid!r}"
        )
    resolved_parent = parent if parent is not None else _run_id_var.get()
    rid_token = _run_id_var.set(rid)
    parent_token = _parent_run_id_var.set(resolved_parent)
    try:
        yield rid
    finally:
        _parent_run_id_var.reset(parent_token)
        _run_id_var.reset(rid_token)


__all__ = [
    "RUN_ID_PATTERN",
    "current_run_id",
    "is_valid_run_id",
    "new_run_id",
    "parent_run_id",
    "reset_run_id",
    "run_scope",
    "set_run_id",
]
