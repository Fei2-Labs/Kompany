"""Company init + project budget envelopes.

Extracted verbatim from core/engine.py (ADR-0003 split).
"""

from __future__ import annotations


from kompany.state.models import LedgerCategory



class CompanyLifecycleMixin:
    def initialize_company(
        self,
        name: str,
        capital: float,
        goal: str = "",
        time_horizon: str = "",
        exclusions: str = "",
    ) -> None:
        """Initialize a new Kompany with starting capital."""
        self.settings.company_name = name
        self.settings.company_goal = goal
        self.settings.company_stage = "solo"
        self.settings.company_time_horizon = time_horizon
        self.settings.company_exclusions = exclusions
        for key, value in [
            ("company_name", name),
            ("company_goal", goal),
            ("company_stage", "solo"),
            ("company_time_horizon", time_horizon),
            ("company_exclusions", exclusions),
        ]:
            self.db.execute(
                """INSERT INTO company_config (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value, updated_at = excluded.updated_at""",
                (key, value),
            )
        self.db.commit()
        # Record initial capital
        if capital > 0:
            self.ledger.record(
                amount=capital,
                description=f"Initial capital for {name}",
                category=LedgerCategory.INCOME,
                approved_by="master",
            )

    # ------------------------------------------------------------------
    # Per-project budget envelopes
    #
    # A project's budget is an EARMARK of the company treasury, not a
    # transfer: funding moves nothing in the ledger (total assets stay
    # consolidated), it just reserves headroom. Spending against a
    # project writes a project-tagged ledger expense, which shrinks the
    # company balance and the envelope at the same time.
    # ------------------------------------------------------------------

    def project_budget(self, project_id: str) -> dict:
        """Funded / spent / remaining for one project's envelope."""
        project = self.projects.get(project_id)
        if project is None:
            raise ValueError(f"Unknown project {project_id!r}")
        spent = self.ledger.spent_for_project(project_id)
        return {
            "project_id": project.id,
            "name": project.name,
            "funded": project.funded_amount,
            "spent": spent,
            "remaining": project.funded_amount - spent,
        }

    def unallocated_treasury(self) -> float:
        """Company balance minus every active project's unspent envelope."""
        balance = self.ledger.get_balance()
        reserved = 0.0
        for project in self.projects.list_active():
            spent = self.ledger.spent_for_project(project.id)
            reserved += max(0.0, project.funded_amount - spent)
        return balance - reserved

    def fund_project(self, project_id: str, amount: float) -> dict:
        """Earmark treasury into a project's envelope.

        Rejects allocations that would promise more than the company
        actually holds (sum of unspent envelopes must stay <= balance).
        """
        if amount <= 0:
            raise ValueError("funding amount must be > 0")
        if self.projects.get(project_id) is None:
            raise ValueError(f"Unknown project {project_id!r}")
        free = self.unallocated_treasury()
        if amount > free:
            raise ValueError(
                f"Insufficient unallocated treasury: requested €{amount:.2f}, "
                f"free €{free:.2f} (balance minus active envelopes)"
            )
        self.projects.add_funding(project_id, amount)
        self.audit.record(
            "project.funded",
            f"Earmarked €{amount:.2f} into project envelope",
            detail={"project_id": project_id, "amount": amount},
        )
        return self.project_budget(project_id)

    def record_project_expense(
        self,
        project_id: str,
        amount: float,
        description: str,
        approved_by: str = "master",
    ) -> dict:
        """Record a real expense against a project's envelope, gated.

        Refuses to overdraw the envelope — per-project budgets are
        isolated even though all cash sits in one consolidated ledger.
        """
        if amount <= 0:
            raise ValueError("expense amount must be > 0")
        budget = self.project_budget(project_id)
        if amount > budget["remaining"]:
            raise ValueError(
                f"Envelope overdraw: project {project_id} has "
                f"€{budget['remaining']:.2f} remaining, expense is €{amount:.2f}"
            )
        self.ledger.record(
            amount=-abs(amount),
            description=description,
            category=LedgerCategory.EXPENSE,
            project_id=project_id,
            approved_by=approved_by,
        )
        return self.project_budget(project_id)

    def abandon_project(self, project_id: str, reason: str = "") -> dict:
        """Abandon a plan (#10) — the single logic home for all surfaces.

        Effects (idempotent on an already-terminal project):
        * project → ``cancelled`` (terminal)
        * unfinished tasks (pending/active) → ``cancelled``
        * pending/snoozed approval cards tied to the project → withdrawn
        * the unspent envelope is RELEASED back to the treasury. Funding
          is an EARMARK (``unallocated_treasury`` reserves only ACTIVE
          projects), so leaving ``active`` releases it implicitly — no
          ledger row is written (a refund row would double-count cash
          that never left the balance). The released amount is audited.
        * AI cost already spent is real and stays in the ledger.
        """
        row = self.db.execute(
            "SELECT id, status FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Project '{project_id}' not found")
        current = row["status"]
        reason = reason or "founder abandoned the plan"
        if current in ("completed", "cancelled", "failed"):
            return {
                "id": project_id, "status": current,
                "previous_status": current, "cancelled": False,
                "tasks_stopped": 0, "approvals_withdrawn": 0,
                "envelope_released": 0.0, "note": "already terminal",
            }

        # Envelope release amount BEFORE flipping status (audit truth).
        spent = self.ledger.spent_for_project(project_id)
        project = self.projects.get(project_id)
        funded = project.funded_amount if project else 0.0
        released = max(0.0, funded - spent)

        self.db.execute(
            "UPDATE projects SET status = 'cancelled', "
            "updated_at = datetime('now') WHERE id = ?", (project_id,),
        )
        stopped = self.db.execute(
            "UPDATE tasks SET status = 'cancelled', "
            "updated_at = datetime('now') "
            "WHERE project_id = ? AND status IN ('pending', 'active')",
            (project_id,),
        ).rowcount
        self.db.commit()

        # Withdraw open inbox cards for this project — a dead plan must
        # not keep asking the founder for money/decisions.
        withdrawn = 0
        for req in self.approvals.list_for_project(project_id):
            if req.status.value in ("pending", "snoozed"):
                self.approvals.cancel(
                    req.id,
                    reason=f"project abandoned: {reason}",
                    by_type="system",
                )
                withdrawn += 1

        self.audit.record(
            "project.cancelled",
            f"Founder abandoned the plan ({reason})",
            detail={
                "project_id": project_id, "previous_status": current,
                "tasks_stopped": stopped, "approvals_withdrawn": withdrawn,
                "envelope_released": released, "reason": reason,
            },
            project_id=project_id,
        )
        return {
            "id": project_id, "status": "cancelled",
            "previous_status": current, "cancelled": True,
            "tasks_stopped": stopped, "approvals_withdrawn": withdrawn,
            "envelope_released": released,
        }

