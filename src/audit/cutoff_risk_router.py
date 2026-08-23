"""截止测试方向 × 覆盖区间 → 风险路由（交付包模块27 精简落地）。

不替代 CutoffChecker 主判据；仅在结论旁注给出建议跟进程序。
"""

from __future__ import annotations

from typing import Any, Optional


def _bucket_days(deviation_days: Optional[int]) -> str:
    if deviation_days is None:
        return "unknown"
    d = abs(int(deviation_days))
    if d == 0:
        return "d0"
    if d <= 7:
        return "d1_7"
    if d <= 30:
        return "d8_30"
    return "d31_plus"


def route_cutoff_risk(
    *,
    test_status: str,
    deviation_days: Optional[int] = None,
    cross_period_end: bool = False,
    early_recognition: bool = False,
) -> dict[str, Any]:
    """返回 risk_level / interval / direction / recommended_actions。"""
    st = str(test_status or "").upper()
    bucket = _bucket_days(deviation_days)
    if early_recognition or (
        deviation_days is not None and int(deviation_days) < 0 and st == "FAIL"
    ):
        direction = "early"  # 提前确认
    elif st == "FAIL" and deviation_days is not None and int(deviation_days) > 0:
        direction = "late"
    elif cross_period_end:
        direction = "period_end_cross"
    else:
        direction = "in_period"

    if st in {"PASS", "WARNING"} and direction == "in_period":
        risk = "low"
        actions = ["维持样本结论", "无需因日差扩大抽测（同会计期间）"]
    elif cross_period_end or direction == "period_end_cross":
        risk = "high"
        actions = [
            "复核报告期末两侧证据日期",
            "考虑扩大期末前后窗口抽测",
            "与管理层确认是否存在跨期调整",
        ]
    elif direction == "early":
        risk = "high" if bucket == "d31_plus" else "medium"
        actions = [
            "追查控制权转移原件日期",
            "检查是否存在提前开票/提前入账",
            "评估对当期收入的错报影响",
        ]
    elif direction == "late":
        risk = "high" if bucket in {"d8_30", "d31_plus"} else "medium"
        actions = [
            "确认是否应记入上期",
            "检查期后入账的截止调整分录",
            "评估对上期/本期列报影响",
        ]
    else:
        risk = "medium"
        actions = ["人工复核截止证据链"]

    return {
        "risk_level": risk,
        "direction": direction,
        "interval_bucket": bucket,
        "cross_period_end": bool(cross_period_end),
        "recommended_actions": actions,
        "source": "cutoff_risk_router_v1",
    }
