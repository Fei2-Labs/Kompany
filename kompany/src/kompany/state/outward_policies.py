"""Per-action-CLASS outward pre-authorization policy store (ADR-0008 #2).

Sibling of :mod:`kompany.state.tool_authorization`. One row per outward
action *class* (coarse granularity — NOT per-channel, per the founder
decision), each set to ``'auto'`` (the outward lane may execute it
unattended) or ``'gated'`` (parked for ``kompany approve``).

The engine ships the safe default: **every class is ``'gated'``**. The
operating project flips specific classes to ``'auto'`` via the CLI /
config. Hard floors that override the stored mode (spend, founder
identity, cost over cap) are enforced in :mod:`kompany.core.outward_policy`
— never baked into this table.
"""

from __future__ import annotations

from kompany.state.database import Database
from kompany.state.models import OutwardActionPolicy

# Valid persisted modes. The hard floors in core/outward_policy.py may
# still downgrade an 'auto' row to gated at resolve time.
MODE_AUTO = "auto"
MODE_GATED = "gated"
VALID_MODES = frozenset({MODE_AUTO, MODE_GATED})

# The outward action classes the engine knows about (mirrors / extends the
# deliverables.DeliverableClass concept). ALL default to 'gated' — safe.
#
# * ai_voice_post        — posts in the AI's own voice (the candidate for auto)
# * external_communication — emails / replies / DMs to customers & leads
# * published_content     — blog / social / press / newsletter / landing copy
# * product_launch        — ship / go-live / public release
# * founder_identity      — acting AS the human founder (HARD FLOOR: always gated)
# * spend                 — money-moving outward actions (HARD FLOOR: always gated)
DEFAULT_ACTION_CLASSES: tuple[str, ...] = (
    "ai_voice_post",
    "external_communication",
    "published_content",
    "product_launch",
    "founder_identity",
    "spend",
)

_DEFAULT_REASON = "engine default — safe until the founder opts a class into auto"


class OutwardActionPolicyStore:
    """SQLite-backed per-action-class outward pre-authorization registry."""

    def __init__(self, db: Database):
        self.db = db
        self.seed_defaults()

    def seed_defaults(self) -> None:
        """Insert any missing action class at the safe default ``'gated'``.

        Idempotent: ``INSERT OR IGNORE`` never downgrades a class the
        founder has already flipped to ``'auto'``.
        """
        for action_class in DEFAULT_ACTION_CLASSES:
            self.db.execute(
                """INSERT OR IGNORE INTO outward_action_policies
                   (action_class, mode, reason)
                   VALUES (?, ?, ?)""",
                (action_class, MODE_GATED, _DEFAULT_REASON),
            )
        self.db.commit()

    def get(self, action_class: str) -> str:
        """Return the stored mode for a class, or ``'gated'`` if unknown.

        Defensive default: an unrecognised / unseeded class is treated as
        ``'gated'`` so a typo or a newly-introduced class never ships
        unattended.
        """
        row = self.db.execute(
            "SELECT mode FROM outward_action_policies WHERE action_class = ?",
            (action_class,),
        ).fetchone()
        if row is None:
            return MODE_GATED
        mode = (row["mode"] or "").strip().lower()
        return mode if mode in VALID_MODES else MODE_GATED

    def get_policy(self, action_class: str) -> OutwardActionPolicy | None:
        """Return the typed policy row, or ``None`` when absent."""
        row = self.db.execute(
            "SELECT * FROM outward_action_policies WHERE action_class = ?",
            (action_class,),
        ).fetchone()
        return self._row_to_policy(row) if row else None

    def set(
        self, action_class: str, mode: str, reason: str = ""
    ) -> OutwardActionPolicy:
        """Set a class to ``'auto'`` or ``'gated'``. Raises on a bad mode."""
        normalized = (mode or "").strip().lower()
        if normalized not in VALID_MODES:
            raise ValueError(
                f"mode must be 'auto' or 'gated', got {mode!r}"
            )
        self.db.execute(
            """INSERT INTO outward_action_policies
               (action_class, mode, reason, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(action_class) DO UPDATE SET
                 mode = excluded.mode,
                 reason = excluded.reason,
                 updated_at = datetime('now')""",
            (action_class, normalized, reason),
        )
        self.db.commit()
        policy = self.get_policy(action_class)
        if policy is None:
            raise RuntimeError("outward action policy was not persisted")
        return policy

    def list(self) -> list[OutwardActionPolicy]:
        """All policies, ordered by action class."""
        rows = self.db.execute(
            "SELECT * FROM outward_action_policies ORDER BY action_class"
        ).fetchall()
        return [self._row_to_policy(row) for row in rows]

    @staticmethod
    def _row_to_policy(row) -> OutwardActionPolicy:
        return OutwardActionPolicy(
            action_class=row["action_class"],
            mode=row["mode"],
            reason=row["reason"] or "",
            updated_at=row["updated_at"],
        )


__all__ = [
    "DEFAULT_ACTION_CLASSES",
    "MODE_AUTO",
    "MODE_GATED",
    "VALID_MODES",
    "OutwardActionPolicyStore",
]
