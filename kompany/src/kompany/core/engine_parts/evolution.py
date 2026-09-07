"""Engine ops for the artifact-evolution lane (08-29 R2). Same dict on
CLI / REST / MCP / SDK; logic lives in ``core/artifact_evolution``."""

from __future__ import annotations

from typing import Any


class ArtifactEvolutionMixin:
    """Requires ``self.artifact_proposals``, ``settings``, ``llm``, ``audit``, ``doctor``."""

    def evolution_propose(self, kind: str, target: str, instruction: str) -> dict[str, Any]:
        """Propose → validate → commit → doctor → auto-revert. Returns the proposal row."""
        from kompany.core.artifact_evolution.pipeline import propose_artifact_evolution
        from kompany.state.artifact_proposals import KINDS

        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        text = (instruction or "").strip()
        if not text:
            raise ValueError("instruction must be a non-empty string")
        return propose_artifact_evolution(self, kind, target, text)

    def evolution_list(self, limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
        return self.artifact_proposals.list(limit=limit, status=status)

    def evolution_show(self, proposal_id: str) -> dict[str, Any] | None:
        return self.artifact_proposals.get(proposal_id)

    def evolution_revert(self, proposal_id: str, reason: str = "founder revert") -> dict[str, Any] | None:
        from kompany.core.artifact_evolution.pipeline import revert_artifact_proposal

        return revert_artifact_proposal(self, proposal_id, reason)

    def evolution_status(self) -> dict[str, Any]:
        """Workspace state + lane budget for the founder audit view."""
        from kompany.core.artifact_evolution.workspace import ArtifactWorkspace

        ws = ArtifactWorkspace(self.settings.data_dir)
        cap = float(getattr(self.settings, "artifact_evolution_daily_cap_usd", 2.0))
        spent = self.artifact_proposals.spent_today_usd()
        return {
            "enabled": bool(getattr(self.settings, "artifact_evolution_enabled", True)),
            "daily_cap_usd": cap, "spent_today_usd": spent, "remaining_today_usd": max(0.0, cap - spent),
            "model_tier": str(getattr(self.settings, "artifact_evolution_model_tier", "economy")),
            "workspace": ws.status(), "recent_commits": ws.log(10) if ws.exists() else [],
            "proposals": self.artifact_proposals.list(limit=10),
        }


__all__ = ["ArtifactEvolutionMixin"]
