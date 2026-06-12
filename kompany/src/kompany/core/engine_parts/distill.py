"""Cross-episode distillation (P1 self-learning).

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations

from typing import Any

from kompany.core.run_context import current_run_id, run_scope



class DistillationMixin:
    # ------------------------------------------------------------------
    # Cross-episode distillation (P1 self-learning)
    # ------------------------------------------------------------------

    def distill(
        self,
        since: Any = None,
        dry_run: bool = False,
        episode_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run cross-episode distillation as CoS.

        Pulls the recent ``project_episodes`` rows, asks CoS to identify
        durable cross-project patterns, and UPSERTs each pattern into
        ``agent_memories`` (``category='experiential'``) keyed by
        ``(agent_role, pattern_key)``.

        Parameters
        ----------
        since:
            A ``timedelta`` controlling the time window. ``None`` uses
            :data:`kompany.agents.cos_distillation.DEFAULT_SINCE` (30 days).
            Ignored when ``episode_ids`` is provided.
        dry_run:
            If ``True``, the LLM call still happens (so the operator can
            inspect what would be written) but no rows are written to
            ``agent_memories`` and no audit event is recorded.
        episode_ids:
            Explicit subset of project ids to distil. Bypasses the
            ``since`` window and the 50-episode cap.
        """
        if current_run_id() is None:
            with run_scope():
                return self._distill_inner(
                    since=since,
                    dry_run=dry_run,
                    episode_ids=episode_ids,
                )
        return self._distill_inner(
            since=since,
            dry_run=dry_run,
            episode_ids=episode_ids,
        )

    def _distill_inner(
        self,
        *,
        since: Any,
        dry_run: bool,
        episode_ids: list[str] | None,
    ) -> dict[str, Any]:
        from datetime import timedelta

        from kompany.agents.cos_distillation import (
            DEFAULT_SINCE,
            MAX_EPISODES_PER_RUN,
            build_episode_summaries,
            filter_inferred_only_patterns,
            filter_patterns,
            select_episode_rows,
        )

        window = since if since is not None else DEFAULT_SINCE
        # Strings/numbers from REST or CLI callers get coerced to timedelta
        # here so the selection helper sees a consistent type.
        if isinstance(window, (int, float)):
            window = timedelta(seconds=float(window))
        if not isinstance(window, timedelta) and window is not None:
            raise ValueError(
                f"since must be a timedelta or numeric seconds, got {type(window).__name__}"
            )

        # ``list`` returns rows in newest-first order with full payload
        # column. We need the payloads to summarize so ``list_episodes``
        # (which strips payload_json) isn't an option here.
        all_rows = self.episodes.list()
        selected = select_episode_rows(
            all_rows,
            episode_ids=episode_ids,
            since=window if not episode_ids else None,
        )

        # Hard cap unless the operator explicitly selected episodes.
        if episode_ids is None and len(selected) > MAX_EPISODES_PER_RUN:
            raise ValueError(
                f"too many episodes in window ({len(selected)} > "
                f"{MAX_EPISODES_PER_RUN}); use --episodes to select a subset"
            )

        run_id = current_run_id()

        # No-input fast path: nothing to learn, nothing to bill for. We
        # still emit an audit event so operators can see the run happened.
        if not selected:
            result = {
                "status": "no_episodes",
                "episodes_in": 0,
                "patterns_out": 0,
                "patterns": [],
                "ai_cost": 0.0,
                "run_id": run_id,
                "dry_run": dry_run,
            }
            self.audit.record(
                "learning.distillation_run",
                "Distillation run produced no patterns (empty episode window)",
                detail={
                    "episodes_in": 0,
                    "patterns_out": 0,
                    "ai_cost": 0.0,
                    "dry_run": dry_run,
                    "run_id": run_id,
                },
            )
            return result

        summaries, parse_failures = build_episode_summaries(selected)
        if not summaries:
            # Every selected row had a malformed payload. Surface this as
            # ``no_episodes`` rather than calling the LLM with nothing.
            self.audit.record(
                "learning.distillation_failed",
                "All selected episodes had malformed payloads",
                detail={
                    "episodes_in": len(selected),
                    "parse_failures": parse_failures,
                    "dry_run": dry_run,
                    "run_id": run_id,
                },
            )
            return {
                "status": "no_parseable_episodes",
                "episodes_in": len(selected),
                "patterns_out": 0,
                "patterns": [],
                "ai_cost": 0.0,
                "run_id": run_id,
                "dry_run": dry_run,
                "parse_failures": parse_failures,
            }

        # Run the LLM call. The CoS agent + the LLMClient wrapper handle
        # run_id propagation, audit events, ledger cost accounting,
        # silent-run detection, and retry on transient failure.
        cos_agent = self.registry.get("cos")
        try:
            # Inject the agreed-target summary so distillation can
            # pattern-match around the company's revenue/customer/
            # deadline shape (mission-targets task 05-19).
            resp = cos_agent.distill(
                summaries,
                targets_summary=self._compose_targets_summary(),
                glossary_summary=self._compose_glossary_summary(),
            )
        except Exception as exc:
            self.audit.record(
                "learning.distillation_failed",
                "CoS LLM call failed during distillation",
                detail={
                    "episodes_in": len(summaries),
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                    "dry_run": dry_run,
                    "run_id": run_id,
                },
            )
            raise

        parsed = resp.parsed
        if parsed is None:
            # ``call_structured`` either parses or raises; defensive only.
            self.audit.record(
                "learning.distillation_failed",
                "CoS distillation returned no parsed output",
                detail={
                    "episodes_in": len(summaries),
                    "dry_run": dry_run,
                    "run_id": run_id,
                },
            )
            raise RuntimeError("CoS distillation returned no parsed output")

        patterns, warnings = filter_patterns(parsed)

        # Evidence-trace guard (task 05-19): drop inferred-only patterns
        # (no ``evidence_episode_ids``) before they pollute
        # ``agent_memories``. Each rejection fires its own audit event so
        # the founder can see "team learned 5 things, 3 were rejected".
        patterns, claim_rejections = filter_inferred_only_patterns(patterns)
        for rejection in claim_rejections:
            self.audit.record(
                event_type="distillation.claim_rejected_inferred_only",
                action="Distillation rejected an inferred-only claim",
                detail={
                    "pattern_key": rejection["pattern_key"],
                    "target_agent_role": rejection["target_agent_role"],
                    "claim_text": rejection["claim_text"],
                    "run_id": run_id,
                    "dry_run": dry_run,
                },
            )

        # Write phase. ``dry_run`` short-circuits all DB writes; the audit
        # event still fires so operators can see who triggered the dry run.
        written: list[dict[str, Any]] = []
        if not dry_run:
            for pattern in patterns:
                action_meta = {
                    "pattern_key": pattern.pattern_key,
                    "confidence": pattern.confidence,
                    "evidence_episode_ids": list(pattern.evidence_episode_ids),
                }
                # Distillation usually emits ``experiential`` patterns; the
                # glossary-and-drift-detection task (05-19) allows CoS to
                # tag a pattern ``glossary_proposal`` when it spots a
                # repeated drift worth canonicalising. The founder then
                # approves the new term via the inbox before it shapes
                # any future agent prompt.
                memory_category = pattern.category or "experiential"
                knowledge_type = (
                    "glossary_proposal"
                    if memory_category == "glossary_proposal"
                    else "experiential"
                )
                upsert = self.memory.upsert_by_pattern_key(
                    agent_role=pattern.target_agent_role,
                    pattern_key=pattern.pattern_key,
                    content=pattern.pattern_summary,
                    metadata=action_meta,
                    category=memory_category,
                    knowledge_type=knowledge_type,
                    run_id=run_id,
                )
                written.append({
                    "agent_role": pattern.target_agent_role,
                    "pattern_key": pattern.pattern_key,
                    "memory_id": upsert["id"],
                    "action": upsert["action"],
                    "confidence": pattern.confidence,
                })

        result_patterns = [
            {
                "target_agent_role": p.target_agent_role,
                "pattern_key": p.pattern_key,
                "pattern_summary": p.pattern_summary,
                "confidence": p.confidence,
                "evidence_episode_ids": list(p.evidence_episode_ids),
            }
            for p in patterns
        ]

        result = {
            "status": "completed",
            "episodes_in": len(summaries),
            "patterns_out": len(patterns),
            "patterns": result_patterns,
            "ai_cost": float(resp.cost_usd),
            "run_id": run_id,
            "dry_run": dry_run,
            "warnings": warnings,
            "writes": written,
            "parse_failures": parse_failures,
            "claims_rejected_inferred_only": claim_rejections,
        }

        self.audit.record(
            "learning.distillation_run",
            "CoS cross-episode distillation completed",
            detail={
                "episodes_in": len(summaries),
                "patterns_out": len(patterns),
                "ai_cost": float(resp.cost_usd),
                "dry_run": dry_run,
                "writes": [
                    {
                        "agent_role": w["agent_role"],
                        "pattern_key": w["pattern_key"],
                        "action": w["action"],
                    }
                    for w in written
                ],
                "warnings": warnings,
                "run_id": run_id,
            },
        )
        return result

