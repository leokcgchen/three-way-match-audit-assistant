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


def _amount_diff_phrase(order_a: float, receipt_a: float, invoice_a: float) -> str:
    """金额差异文案：签收无金额时不报三方 100%，改为订单↔发票。"""
    if float(receipt_a) <= 0:
        if float(order_a) <= 0 or float(invoice_a) <= 0:
            return (
                f"签收未提供金额，且订单/发票金额缺项"
                f"(订单{order_a:g}/发票{invoice_a:g})"
            )
        peak = max(abs(order_a), abs(invoice_a))
        pct = 0.0 if peak == 0 else abs(order_a - invoice_a) / peak * 100.0
        return (
            f"签收未提供金额(金额维未测)，订单↔发票差异{pct:.1f}%"
            f"(订单{order_a:g}/发票{invoice_a:g})"
        )
    return f"金额差异{_spread_pct(order_a, receipt_a, invoice_a):.1f}%"


def _qty_diff_phrase(order_q: float, receipt_q: float, invoice_q: float) -> str:
    """数量差异文案：一侧为 0、另侧有值时优先提示抽取缺项，避免虚报 100%。"""
    vals = [float(order_q), float(receipt_q), float(invoice_q)]
    if any(v == 0 for v in vals) and any(v > 0 for v in vals):
        return (
            f"数量存在缺项或为零(订单{order_q:g}/签收{receipt_q:g}/发票{invoice_q:g}，"
            f"请复核抽取，勿按满额差异解读)"
        )
    return f"数量差异{_spread_pct(order_q, receipt_q, invoice_q):.1f}%"


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


def build_three_way_summary(
    request: ThreeWayMatchRequest,
    match_result: ThreeWayMatchResponse,
) -> str:
    """只描述三单字段勾稽，禁止夹带截止性结论。"""
    amount_part = _amount_diff_phrase(
        request.order.total_amount,
        request.warehouse_receipt.total_amount,
        request.invoice.total_amount,
    )
    qty_part = _qty_diff_phrase(
        request.order.quantity,
        request.warehouse_receipt.quantity,
        request.invoice.quantity,
    )
    decision = getattr(match_result, "decision", None) or match_result.overall_status
    roles = getattr(match_result, "quantity_roles", None) or {}
    if roles:
        from src.three_way_match.phrases import quantity_roles_phrase

        qty_part = quantity_roles_phrase(
            roles.get("ordered_qty", request.order.quantity),
            roles.get("received_qty", request.warehouse_receipt.quantity),
            roles.get("invoiced_qty", request.invoice.quantity),
        )
    return (
        f"三单字段勾稽{_match_verb(match_result.overall_status)}"
        f"（决策 {decision}）："
        f"客户{_supplier_label(match_result.comparisons)}，{amount_part}，"
        f"{qty_part}。"
    )


def build_cutoff_summary(
    request: ThreeWayMatchRequest,
    cutoff_result: Optional[CutoffResponse] = None,
    *,
    cutoff_available: bool = True,
    cutoff_skipped_reason: Optional[str] = None,
) -> str:
    """只描述截止性测试，不引用三单匹配状态。"""
    if not cutoff_available or cutoff_result is None:
        return f"截止性测试未执行：{cutoff_skipped_reason or '资料或服务不可用'}。"
    receipt = request.warehouse_receipt.receipt_date
    posting = request.invoice.posting_date or ""
    expected = cutoff_result.应确认日期 or "-"
    deviation = cutoff_result.偏差天数
    deviation_text = f"{deviation}天" if deviation is not None else "未知"
    if cutoff_result.测试状态 == "PASS":
        return (
            f"截止性测试通过：控制权转移日（签收/验收）{receipt}=应确认{expected}，"
            f"实际入账{posting}，偏差{deviation_text}。"
        )
    issue = cutoff_result.问题描述 or "存在截止性风险"
    verb = "需关注" if cutoff_result.测试状态 == "WARNING" else "未通过"
    return (
        f"截止性测试{verb}：控制权转移日（签收/验收）{receipt}=应确认{expected}，"
        f"实际入账{posting}，偏差{deviation_text}；{issue.rstrip('。')}。"
    )


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
    match_part = build_three_way_summary(request, match_result)
    cutoff_part = build_cutoff_summary(
        request,
        cutoff_result,
        cutoff_available=cutoff_available,
        cutoff_skipped_reason=cutoff_skipped_reason,
    )
    match_only = (not cutoff_available) and ("入账日期缺失" in (cutoff_skipped_reason or ""))
    overall_part = f"综合结论：{_overall_icon(overall_status, match_only=match_only)}"
    return match_part + cutoff_part + overall_part
