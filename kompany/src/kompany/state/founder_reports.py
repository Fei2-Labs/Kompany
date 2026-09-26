"""Founder report store — one row per generated periodic report.

Autopilot (09-26-autopilot-reports): the ticker writes a short daily and a
deeper weekly report so a founder who never opens the board still knows
what the company did. The row keeps the deterministic data snapshot
(``data_json``) next to the narrative so the numbers can be re-read
without another LLM call, and the delivery outcome (``delivery_json``)
so "was it actually sent" is answerable from the table.

Reports are T0 internal artifacts: writing one needs no approval.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from kompany.state.database import Database

PERIODS: tuple[str, ...] = ("daily", "weekly", "manual")


class FounderReportStore:
    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        *,
        period: str,
        period_start: str,
        period_end: str,
        narrative: str,
        data: dict[str, Any],
        cost: float = 0.0,
        delivery: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if period not in PERIODS:
            raise ValueError(f"invalid period {period!r}; expected one of {PERIODS}")
        rid = f"rpt-{uuid.uuid4().hex[:12]}"
        self.db.execute(
            """INSERT INTO founder_reports
               (id, period, period_start, period_end, narrative, data_json,
                delivery_json, cost)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rid,
                period,
                period_start,
                period_end,
                narrative,
                json.dumps(data, default=str),
                json.dumps(delivery or [], default=str),
                float(cost or 0.0),
            ),
        )
        self.db.commit()
        return self.get(rid) or {}

    def set_delivery(self, report_id: str, delivery: list[dict[str, Any]]) -> None:
        self.db.execute(
            "UPDATE founder_reports SET delivery_json = ? WHERE id = ?",
            (json.dumps(delivery, default=str), report_id),
        )
        self.db.commit()

    def get(self, report_id: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM founder_reports WHERE id = ?", (report_id,)
        ).fetchone()
        return self._row(row) if row else None

    def latest(self, period: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM founder_reports WHERE period = ? "
            "ORDER BY generated_at DESC, rowid DESC LIMIT 1",
            (period,),
        ).fetchone()
        return self._row(row) if row else None

    def list(self, period: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        sql = "SELECT * FROM founder_reports"
        params: list[Any] = []
        if period:
            sql += " WHERE period = ?"
            params.append(period)
        sql += " ORDER BY generated_at DESC, rowid DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [self._row(r) for r in self.db.execute(sql, tuple(params)).fetchall()]

    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        d = dict(r)
        for col, key in (("data_json", "data"), ("delivery_json", "delivery")):
            try:
                d[key] = json.loads(d.pop(col) or ("{}" if key == "data" else "[]"))
            except (TypeError, ValueError):
                d[key] = {} if key == "data" else []
        return d


__all__ = ["FounderReportStore", "PERIODS"]
