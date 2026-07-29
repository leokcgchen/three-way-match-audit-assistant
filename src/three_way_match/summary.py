"""三单匹配自然语言摘要生成。"""

from __future__ import annotations

from typing import Optional, Sequence

from src.models.schemas import CutoffResponse
from src.three_way_match.models import (
    MatchResult,
    ThreeWayMatchRequest,
    ThreeWayMatchResponse,
)


def _spread_pct(a: float, b: float, c: float) -> float:
    vals = [float(a), float(b), float(c)]
    peak = max(abs(v) for v in vals)
    if peak == 0:
        return 0.0
    return (max(vals) - min(vals)) / peak * 100.0


def _supplier_label(comparisons: Sequence[MatchResult]) -> str:
    for item in comparisons:
        if item.field_name == "supplier_name":
            return "一致" if item.is_consistent else "不一致"
    return "未知"


def _match_verb(status: str) -> str:
    if status == "PASS":
        return "通过"
    if status == "WARNING":
        return "需关注"
    return "未通过"


def _overall_icon(status: str, *, match_only: bool = False) -> str:
    if status == "PASS":
        return "✅ PASS（仅三单匹配）" if match_only else "✅ PASS"
    if status == "WARNING":
        return "⚠️ WARNING"
    return "❌ FAIL"


def build_human_readable_summary(
    request: ThreeWayMatchRequest,
    match_result: ThreeWayMatchResponse,
    *,
    overall_status: str,
    cutoff_result: Optional[CutoffResponse] = None,
    cutoff_available: bool = True,
    cutoff_skipped_reason: Optional[str] = None,
) -> str:
    """生成三单匹配 + 截止性 + 综合结论的自然语言摘要。"""
    amount_pct = _spread_pct(
        request.order.total_amount,
        request.warehouse_receipt.total_amount,
        request.invoice.total_amount,
    )
    qty_pct = _spread_pct(
        request.order.quantity,
        request.warehouse_receipt.quantity,
        request.invoice.quantity,
    )
    supplier = _supplier_label(match_result.comparisons)
    score = match_result.match_score
    if float(score).is_integer():
        score_text = f"{int(score)}"
    else:
        score_text = f"{score:g}"

    match_part = (
        f"三单匹配{_match_verb(match_result.overall_status)}："
        f"供应商{supplier}，金额差异{amount_pct:.1f}%，"
        f"数量差异{qty_pct:.1f}%，匹配得分{score_text}分。"
    )

    if not cutoff_available:
        reason = cutoff_skipped_reason or "截止性测试不可用"
        cutoff_part = f"截止性测试未执行：{reason}。"
        match_only = "入账日期缺失" in reason
        overall_part = f"综合结论：{_overall_icon(overall_status, match_only=match_only)}"
        return match_part + cutoff_part + overall_part

    assert cutoff_result is not None
    receipt = request.warehouse_receipt.receipt_date
    posting = request.invoice.posting_date or ""
    expected = cutoff_result.应确认日期 or "-"
    deviation = cutoff_result.偏差天数
    deviation_text = (
        f"{deviation}天" if deviation is not None else "未知"
    )
    cutoff_status = cutoff_result.测试状态

    if cutoff_status == "PASS":
        cutoff_part = (
            f"截止性测试通过：控制权转移日（签收/验收）{receipt}="
            f"应确认{expected}，实际入账{posting}，偏差{deviation_text}，"
            f"收入确认期间合规（付款账期不参与截止判断）。"
        )
    elif cutoff_status == "WARNING":
        cutoff_part = (
            f"截止性测试需关注：控制权转移日（签收/验收）{receipt}="
            f"应确认{expected}，实际入账{posting}，偏差{deviation_text}。"
            f"{cutoff_result.问题描述}"
        )
        if not cutoff_part.endswith("。"):
            cutoff_part += "。"
    else:
        issue = cutoff_result.问题描述 or "存在截止性风险"
        cutoff_part = (
            f"截止性测试未通过：控制权转移日（签收/验收）{receipt}="
            f"应确认{expected}，实际入账{posting}，{issue}"
        )
        if not cutoff_part.endswith("。"):
            cutoff_part += "。"

    overall_part = f"综合结论：{_overall_icon(overall_status)}"
    return match_part + cutoff_part + overall_part
