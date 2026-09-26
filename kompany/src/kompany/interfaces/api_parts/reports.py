"""Founder report routes (09-26-autopilot-reports).

``GET /reports`` lists stored reports, ``GET /reports/latest`` returns the
newest of one period, ``POST /reports/generate`` writes a fresh manual
report (one economy LLM call) and optionally delivers it through the
auto adapter. Logic lives in ``core/founder_report.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kompany.core import founder_report as _fr
from kompany.interfaces.api_parts.deps import get_engine
from kompany.state.founder_reports import PERIODS

router = APIRouter()


class GenerateReportRequest(BaseModel):
    period: str = "manual"
    deliver: bool = False


@router.get("/reports")
def list_reports(period: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    if period is not None and period not in PERIODS:
        raise HTTPException(status_code=400, detail=f"period must be one of {PERIODS}")
    return _fr.reports_list(get_engine(), period=period, limit=limit)


@router.get("/reports/latest")
def latest_report(period: str = "daily") -> dict[str, Any]:
    if period not in PERIODS:
        raise HTTPException(status_code=400, detail=f"period must be one of {PERIODS}")
    row = _fr.report_latest(get_engine(), period)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no {period} report yet")
    return row


@router.get("/reports/{report_id}")
def get_report(report_id: str) -> dict[str, Any]:
    row = get_engine().founder_reports.get(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="report not found")
    return row


@router.post("/reports/generate")
def generate_report(body: GenerateReportRequest | None = None) -> dict[str, Any]:
    body = body or GenerateReportRequest()
    if body.period not in PERIODS:
        raise HTTPException(status_code=400, detail=f"period must be one of {PERIODS}")
    return _fr.generate_report(get_engine(), body.period, deliver=body.deliver)
