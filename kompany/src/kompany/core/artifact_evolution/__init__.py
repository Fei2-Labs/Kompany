"""Artifact evolution lane (08-29 self-evolution loop).

Second self-evolution lane next to ``core/self_update`` (which changes
engine *code* through a founder-approved PR). This lane changes
*artifacts* — soul YAML, workflow YAML, plugin scaffolds — inside a
workspace-local git repo at ``<data_dir>/artifacts/``. Restart is the
reload boundary; every change is a commit; ``git revert`` is the undo.

PR1 ships the workspace + loader path + the doctor self-test gate.
Proposal/apply/revert (R2) and plugin incubation (R4) build on it.
"""

from kompany.core.artifact_evolution.workspace import ArtifactWorkspace, workspace_root

__all__ = ["ArtifactWorkspace", "workspace_root"]
