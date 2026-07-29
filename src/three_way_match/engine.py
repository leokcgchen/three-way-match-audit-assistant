"""三单匹配核心引擎：供应商 / 金额 / 数量比对与计分。"""

from __future__ import annotations

from typing import List, Literal, Sequence, Tuple

from src.three_way_match.models import (
    MatchResult,
    ThreeWayMatchRequest,
    ThreeWayMatchResponse,
)
from src.utils.supplier_normalize import normalize_supplier_name, suppliers_are_consistent

AMOUNT_TOLERANCE = 0.02  # ±2%
QUANTITY_TOLERANCE = 0.01  # ±1%
AMOUNT_WEIGHT = 40.0
QUANTITY_WEIGHT = 30.0
SUPPLIER_WEIGHT = 30.0


def _spread_ratio(values: Sequence[float]) -> float:
    """相对极差：(max-min)/max(|vals|)，全 0 时视为 0。"""
    nums = [float(v) for v in values]
    peak = max(abs(v) for v in nums)
    if peak == 0:
        return 0.0
    return (max(nums) - min(nums)) / peak


def _exact_equal(values: Sequence[float], eps: float = 1e-9) -> bool:
    nums = [float(v) for v in values]
    return (max(nums) - min(nums)) <= eps


def _compare_supplier(
    order_name: str, receipt_name: str, invoice_name: str
) -> Tuple[MatchResult, bool, float]:
    """
    供应商比对（归一化容错）：
    - 去首尾空格、常见前缀、括号内税号/地址/电话
    - 包含关系或简称视为一致
    """
    o, r, i = order_name.strip(), receipt_name.strip(), invoice_name.strip()
    ok = suppliers_are_consistent(o, r, i)
    if ok:
        display = max((o, r, i), key=len)
        return (
            MatchResult(
                field_name="supplier_name",
                order_value=o,
                receipt_value=r,
                invoice_value=i,
                is_consistent=True,
                diff_description=(
                    None
                    if o == r == i
                    else f"供应商归一化后一致（展示：{display}）"
                ),
            ),
            True,
            SUPPLIER_WEIGHT,
        )
    return (
        MatchResult(
            field_name="supplier_name",
            order_value=o,
            receipt_value=r,
            invoice_value=i,
            is_consistent=False,
            diff_description=(
                f"供应商不一致：订单[{o}] / 入库[{r}] / 发票[{i}]"
                f"（归一化：{normalize_supplier_name(o)} / "
                f"{normalize_supplier_name(r)} / {normalize_supplier_name(i)}）"
            ),
        ),
        False,
        0.0,
    )


def _compare_numeric(
    field_name: str,
    order_value: float,
    receipt_value: float,
    invoice_value: float,
    tolerance: float,
    full_weight: float,
) -> Tuple[MatchResult, bool, bool, float]:
    """
    返回 (比对结果, 是否在容差内, 是否硬失败(超容差), 得分)。

    - 三方完全相等 → 满分，容差内
    - 相对极差 ≤ 容差但非完全相等 → 半分（软差异），容差内
    - 相对极差 > 容差 → 0 分，硬失败
    """
    values = (order_value, receipt_value, invoice_value)
    ratio = _spread_ratio(values)
    if _exact_equal(values):
        return (
            MatchResult(
                field_name=field_name,
                order_value=order_value,
                receipt_value=receipt_value,
                invoice_value=invoice_value,
                is_consistent=True,
                diff_description=None,
            ),
            True,
            False,
            full_weight,
        )
    if ratio <= tolerance:
        half = full_weight / 2.0
        return (
            MatchResult(
                field_name=field_name,
                order_value=order_value,
                receipt_value=receipt_value,
                invoice_value=invoice_value,
                is_consistent=True,
                diff_description=(
                    f"{field_name}存在偏差但在±{tolerance:.0%}容差内"
                    f"（相对极差 {ratio:.2%}）"
                ),
            ),
            True,
            False,
            half,
        )
    return (
        MatchResult(
            field_name=field_name,
            order_value=order_value,
            receipt_value=receipt_value,
            invoice_value=invoice_value,
            is_consistent=False,
            diff_description=(
                f"{field_name}超出±{tolerance:.0%}容差"
                f"（相对极差 {ratio:.2%}）"
            ),
        ),
        False,
        True,
        0.0,
    )


def _decide_status(
    supplier_ok: bool,
    amount_hard_fail: bool,
    quantity_hard_fail: bool,
    score: float,
) -> Literal["PASS", "WARNING", "FAIL"]:
    if not supplier_ok:
        return "FAIL"
    if amount_hard_fail or quantity_hard_fail:
        return "FAIL"
    if score >= 90:
        return "PASS"
    if score >= 70:
        return "WARNING"
    return "FAIL"


def _build_summary(
    status: str, score: float, risks: List[str]
) -> str:
    if status == "PASS":
        return f"三单匹配通过，得分 {score:.0f}"
    if status == "WARNING":
        hint = risks[0] if risks else "存在可接受偏差"
        return f"三单匹配需关注（得分 {score:.0f}）：{hint}"
    if risks:
        return f"三单匹配失败（得分 {score:.0f}）：{risks[0]}"
    return f"三单匹配失败，得分 {score:.0f}"


def run_match(request: ThreeWayMatchRequest) -> ThreeWayMatchResponse:
    """对订单、入库单、发票执行三单匹配，返回标准化结果。"""
    order = request.order
    receipt = request.warehouse_receipt
    invoice = request.invoice

    comparisons: List[MatchResult] = []
    risks: List[str] = []

    supplier_cmp, supplier_ok, supplier_pts = _compare_supplier(
        order.supplier_name, receipt.supplier_name, invoice.supplier_name
    )
    comparisons.append(supplier_cmp)
    if not supplier_ok:
        risks.append(supplier_cmp.diff_description or "供应商不一致")

    amount_cmp, _amount_ok, amount_hard, amount_pts = _compare_numeric(
        "total_amount",
        order.total_amount,
        receipt.total_amount,
        invoice.total_amount,
        AMOUNT_TOLERANCE,
        AMOUNT_WEIGHT,
    )
    comparisons.append(amount_cmp)
    if amount_cmp.diff_description:
        risks.append(amount_cmp.diff_description)

    qty_cmp, _qty_ok, qty_hard, qty_pts = _compare_numeric(
        "quantity",
        order.quantity,
        receipt.quantity,
        invoice.quantity,
        QUANTITY_TOLERANCE,
        QUANTITY_WEIGHT,
    )
    comparisons.append(qty_cmp)
    if qty_cmp.diff_description:
        risks.append(qty_cmp.diff_description)

    # 供应商不一致不计供应商分；其余字段仍记入比对，但总分按规则结算
    score = round(supplier_pts + amount_pts + qty_pts, 2)
    status = _decide_status(supplier_ok, amount_hard, qty_hard, score)
    summary = _build_summary(status, score, risks)

    return ThreeWayMatchResponse(
        order_no=order.order_no,
        overall_status=status,
        match_score=score,
        comparisons=comparisons,
        summary=summary,
        risks=risks,
    )
