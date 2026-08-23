"""会计期间键：自然月 / 4-4-5 / 仅报告期末边界。

配置来源（优先级高→低）：
  job.calendar_mode / job.fiscal_year_start
  环境 AUDIT_CALENDAR_MODE / AUDIT_FISCAL_YEAR_START
默认 natural_month（与历史 CutoffChecker 一致）。
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Optional, Union

DateLike = Union[date, datetime, str, None]


def _parse_date(value: DateLike) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def resolve_calendar_mode(job: Optional[dict[str, Any]] = None) -> str:
    raw = ""
    if isinstance(job, dict):
        raw = str(job.get("calendar_mode") or (job.get("plan") or {}).get("calendar_mode") or "")
    if not raw.strip():
        raw = os.getenv("AUDIT_CALENDAR_MODE") or "natural_month"
    mode = str(raw).strip().lower().replace("-", "_")
    if mode in {"445", "4_4_5", "fiscal_445", "fiscal445"}:
        return "fiscal_445"
    if mode in {"period_end_only", "report_boundary", "boundary"}:
        return "period_end_only"
    return "natural_month"


def resolve_fiscal_year_start(job: Optional[dict[str, Any]] = None) -> Optional[date]:
    raw = None
    if isinstance(job, dict):
        raw = job.get("fiscal_year_start") or (job.get("plan") or {}).get("fiscal_year_start")
    if raw in (None, ""):
        raw = os.getenv("AUDIT_FISCAL_YEAR_START") or ""
    return _parse_date(raw)


def _fiscal_year_anchor(d: date, fy_start: date) -> date:
    """取包含 d 的财年起点（按周年滚动）。"""
    start = date(d.year, fy_start.month, fy_start.day)
    if d < start:
        start = date(d.year - 1, fy_start.month, fy_start.day)
    return start


def period_key(
    value: DateLike,
    *,
    mode: str = "natural_month",
    fiscal_year_start: Optional[DateLike] = None,
    period_end: Optional[DateLike] = None,
) -> str:
    """返回可比较的期间键。无法解析则空串。"""
    d = _parse_date(value)
    if not d:
        return ""
    m = (mode or "natural_month").lower()
    if m == "period_end_only":
        pe = _parse_date(period_end)
        if pe is None:
            return d.strftime("%Y-%m")
        return "期内" if d <= pe else "期后"
    if m == "fiscal_445":
        fy = _parse_date(fiscal_year_start) or date(d.year, 1, 1)
        anchor = _fiscal_year_anchor(d, fy)
        # 4-4-5：每季 13 周，全年 52 周；第 53 周并入 P12
        days = (d - anchor).days
        if days < 0:
            days = 0
        week = days // 7  # 0-based
        if week >= 52:
            period_idx = 12
        else:
            # 每季：4+4+5 = 13 周 → 3 个期间
            q = week // 13
            rem = week % 13
            if rem < 4:
                slot = 0
            elif rem < 8:
                slot = 1
            else:
                slot = 2
            period_idx = q * 3 + slot + 1
        return f"FY{anchor.year}-P{period_idx:02d}"
    return d.strftime("%Y-%m")


def calendar_from_job(job: Optional[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mode": resolve_calendar_mode(job),
        "fiscal_year_start": (
            resolve_fiscal_year_start(job).isoformat()
            if resolve_fiscal_year_start(job)
            else None
        ),
    }
