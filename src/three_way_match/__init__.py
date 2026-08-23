"""三单匹配适配器（订单 / 入库签收 / 发票），独立于外部三单系统。"""

from __future__ import annotations

from typing import Any

from src.three_way_match.models import (
    GoodsReceipt,
    Invoice,
    MatchResult,
    Order,
    ThreeWayMatchRequest,
    ThreeWayMatchResponse,
    WarehouseReceipt,
)

__all__ = [
    "ThreeWayMatcher",
    "merge_overall_status",
    "Order",
    "WarehouseReceipt",
    "GoodsReceipt",
    "Invoice",
    "ThreeWayMatchRequest",
    "MatchResult",
    "ThreeWayMatchResponse",
]


def __getattr__(name: str) -> Any:
    """延迟加载 matcher，避免与 reporting 循环导入。"""
    if name in {"ThreeWayMatcher", "merge_overall_status"}:
        from src.three_way_match.matcher import ThreeWayMatcher, merge_overall_status

        mapping = {
            "ThreeWayMatcher": ThreeWayMatcher,
            "merge_overall_status": merge_overall_status,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
