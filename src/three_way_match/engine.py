"""三单匹配核心引擎：客户名称 / 金额 / 数量比对（销售收入）。

实务口径（不比日期）：
- 要比：是否同一笔（业务号捆绑在 audit_trace）、购方名称、价税合计、数量。
- 不要比：订单日 / 开票日 / 签收日 / 入账日是否同一天。日期只进截止性。
- 签收单常无金额、发票行数量常缺：缺的一方记未测，不得拿 0 去打 FAIL。

放行哲学（对齐 v1.1）：
- 硬失败 → HOLD_REVIEW（overall FAIL）；容差内软偏差 → PASS_WITH_WARNING；
  硬规则全过 → AUTO_PASS。禁止用总分/相似度打分放行。
"""

from __future__ import annotations

from typing import List, Literal, Optional, Sequence, Tuple

from src.three_way_match.models import (
    MatchResult,
    ThreeWayMatchRequest,
    ThreeWayMatchResponse,
)
from src.three_way_match.phrases import amount_roles_phrase, quantity_roles_phrase
from src.utils.supplier_normalize import normalize_supplier_name, suppliers_are_consistent

AMOUNT_TOLERANCE = 0.02  # ±2%
QUANTITY_TOLERANCE = 0.01  # ±1%

Decision = Literal["AUTO_PASS", "HOLD_REVIEW", "PASS_WITH_WARNING", "NOT_APPLICABLE"]

SLOT_REASONS = {
    "total_amount": (
        "金额槽取价税合计（票面含税总额）；不用税率反推或猜测。"
        "签收无金额时该维未测，仅勾稽订单与发票。"
    ),
    "quantity": (
        "数量分三角色对照：订单数量、签收/验收数量、发票开票数量；"
        "缺的一方记未测，不用 0 打 FAIL。"
    ),
    "supplier_name": (
        "纸面购方/客户名称勾稽（归一化容错）；"
        "不以名称当主体主键，也不用相似度打分放行。"
    ),
}

ERP_REVIEW = {
    "status": "UNAVAILABLE",
    "note": (
        "未接公司 ERP 过账/审批权威源；纸面三单结论不冒充已过账。"
        "缺 ERP 时相关状态规则应为 HOLD/未测，属正确终态。"
    ),
}


def _spread_ratio(values: Sequence[float]) -> float:
    nums = [float(v) for v in values]
    peak = max(abs(v) for v in nums)
    if peak == 0:
        return 0.0
    return (max(nums) - min(nums)) / peak


def _exact_equal(values: Sequence[float], eps: float = 1e-9) -> bool:
    nums = [float(v) for v in values]
    return (max(nums) - min(nums)) <= eps


def _explain_supplier(*, ok: bool, order_name: str, receipt_name: str, invoice_name: str) -> str:
    if not all(str(x or "").strip() for x in (order_name, receipt_name, invoice_name)):
        return "购方名称缺项，无法完成纸面名称勾稽（资料不足，不得用「未知」冒充）。"
    if ok:
        return "订单/签收/发票购方名称经归一化后一致。"
    return (
        f"购方名称不一致：订单 {order_name!r} / 签收 {receipt_name!r} / 发票 {invoice_name!r}"
        f"（归一化 {normalize_supplier_name(order_name)} / "
        f"{normalize_supplier_name(receipt_name)} / "
        f"{normalize_supplier_name(invoice_name)}）。"
    )


def _explain_amount(
    *,
    consistent: bool,
    hard: bool,
    soft: bool,
    order_value: float,
    receipt_value: float,
    invoice_value: float,
    ratio: float,
    receipt_missing: bool,
) -> str:
    roles = amount_roles_phrase(order_value, receipt_value, invoice_value)
    if receipt_missing:
        base = "签收未提供金额，仅勾稽订单与发票金额（签收金额维未测）。"
    else:
        base = "金额按订单、签收/验收、发票三方相对极差勾稽。"
    if hard:
        return f"{base}超出±{AMOUNT_TOLERANCE:.0%}容差（极差 {ratio:.2%}）；{roles}。"
    if soft:
        return f"{base}存在偏差但在±{AMOUNT_TOLERANCE:.0%}内（极差 {ratio:.2%}）；{roles}。"
    if consistent:
        return f"{base}一致；{roles}。"
    return f"{base}{roles}。"


def _explain_quantity(
    *,
    consistent: bool,
    hard: bool,
    soft: bool,
    order_value: float,
    receipt_value: float,
    invoice_value: float,
    ratio: float,
    present_n: int,
) -> str:
    roles = quantity_roles_phrase(order_value, receipt_value, invoice_value)
    if present_n < 2:
        return f"数量不足两方有值，本维未测（不作为三单失败）；{roles}。"
    if hard:
        return (
            f"数量超出±{QUANTITY_TOLERANCE:.0%}容差（相对极差 {ratio:.2%}）；"
            f"{roles}。"
        )
    if soft:
        return (
            f"数量存在偏差但在±{QUANTITY_TOLERANCE:.0%}内（相对极差 {ratio:.2%}）；"
            f"{roles}。"
        )
    if consistent:
        return f"订单、签收/验收、发票开票数量勾稽一致；{roles}。"
    return f"数量勾稽；{roles}。"


def _compare_supplier(
    order_name: str, receipt_name: str, invoice_name: str
) -> Tuple[MatchResult, bool]:
    names = [
        str(order_name or "").strip(),
        str(receipt_name or "").strip(),
        str(invoice_name or "").strip(),
    ]
    if not all(names):
        return (
            MatchResult(
                field_name="supplier_name",
                order_value=order_name,
                receipt_value=receipt_name,
                invoice_value=invoice_name,
                is_consistent=False,
                diff_description="客户名称缺失，无法完成名称勾稽（资料不足）",
                auditor_explain=_explain_supplier(
                    ok=False,
                    order_name=order_name,
                    receipt_name=receipt_name,
                    invoice_name=invoice_name,
                ),
                pick_reason=SLOT_REASONS["supplier_name"],
            ),
            False,
        )
    ok = suppliers_are_consistent(order_name, receipt_name, invoice_name)
    return (
        MatchResult(
            field_name="supplier_name",
            order_value=order_name,
            receipt_value=receipt_name,
            invoice_value=invoice_name,
            is_consistent=ok,
            diff_description=(
                None
                if ok
                else (
                    f"客户名称不一致：订单/签收/发票分别为 "
                    f"{order_name!r} / {receipt_name!r} / {invoice_name!r}"
                    f"（归一化：{normalize_supplier_name(order_name)} / "
                    f"{normalize_supplier_name(receipt_name)} / "
                    f"{normalize_supplier_name(invoice_name)}）"
                )
            ),
            auditor_explain=_explain_supplier(
                ok=ok,
                order_name=order_name,
                receipt_name=receipt_name,
                invoice_name=invoice_name,
            ),
            pick_reason=SLOT_REASONS["supplier_name"],
        ),
        ok,
    )


def _compare_numeric(
    field_name: str,
    order_value: float,
    receipt_value: float,
    invoice_value: float,
    tolerance: float,
    *,
    explain_fn,
    pick_reason: str,
) -> Tuple[MatchResult, bool, bool, bool]:
    """返回 (比对, 容差内, 硬失败, 软偏差)。"""
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
                auditor_explain=explain_fn(
                    consistent=True,
                    hard=False,
                    soft=False,
                    order_value=order_value,
                    receipt_value=receipt_value,
                    invoice_value=invoice_value,
                    ratio=ratio,
                ),
                pick_reason=pick_reason,
            ),
            True,
            False,
            False,
        )
    if ratio <= tolerance:
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
                auditor_explain=explain_fn(
                    consistent=True,
                    hard=False,
                    soft=True,
                    order_value=order_value,
                    receipt_value=receipt_value,
                    invoice_value=invoice_value,
                    ratio=ratio,
                ),
                pick_reason=pick_reason,
            ),
            True,
            False,
            True,
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
            auditor_explain=explain_fn(
                consistent=False,
                hard=True,
                soft=False,
                order_value=order_value,
                receipt_value=receipt_value,
                invoice_value=invoice_value,
                ratio=ratio,
            ),
            pick_reason=pick_reason,
        ),
        False,
        True,
        False,
    )


def _compare_amount(
    order_value: float,
    receipt_value: float,
    invoice_value: float,
) -> Tuple[MatchResult, bool, bool, bool]:
    def _amt_explain(**kw):
        return _explain_amount(receipt_missing=False, **kw)

    if float(receipt_value) <= 0:
        if float(order_value) <= 0 or float(invoice_value) <= 0:
            return (
                MatchResult(
                    field_name="total_amount",
                    order_value=order_value,
                    receipt_value=receipt_value,
                    invoice_value=invoice_value,
                    is_consistent=False,
                    diff_description=(
                        "签收未提供金额；且订单或发票金额缺失/为零，无法完成金额勾稽"
                    ),
                    auditor_explain=_explain_amount(
                        consistent=False,
                        hard=True,
                        soft=False,
                        order_value=order_value,
                        receipt_value=receipt_value,
                        invoice_value=invoice_value,
                        ratio=1.0,
                        receipt_missing=True,
                    ),
                    pick_reason=SLOT_REASONS["total_amount"],
                ),
                False,
                True,
                False,
            )
        pair = (float(order_value), float(invoice_value))
        ratio = _spread_ratio(pair)
        note = "签收单未提供金额，金额仅勾稽订单↔发票（签收金额维未测/不适用）"
        if _exact_equal(pair) or ratio <= AMOUNT_TOLERANCE:
            soft = not _exact_equal(pair)
            desc = note
            if soft:
                desc = (
                    f"{note}；订单/发票相对极差 {ratio:.2%}"
                    f"（在±{AMOUNT_TOLERANCE:.0%}内）"
                )
            return (
                MatchResult(
                    field_name="total_amount",
                    order_value=order_value,
                    receipt_value=receipt_value,
                    invoice_value=invoice_value,
                    is_consistent=True,
                    diff_description=desc,
                    auditor_explain=_explain_amount(
                        consistent=True,
                        hard=False,
                        soft=soft,
                        order_value=order_value,
                        receipt_value=receipt_value,
                        invoice_value=invoice_value,
                        ratio=ratio,
                        receipt_missing=True,
                    ),
                    pick_reason=SLOT_REASONS["total_amount"],
                ),
                True,
                False,
                soft,
            )
        return (
            MatchResult(
                field_name="total_amount",
                order_value=order_value,
                receipt_value=receipt_value,
                invoice_value=invoice_value,
                is_consistent=False,
                diff_description=(
                    f"{note}；订单↔发票超出±{AMOUNT_TOLERANCE:.0%}容差"
                    f"（相对极差 {ratio:.2%}）"
                ),
                auditor_explain=_explain_amount(
                    consistent=False,
                    hard=True,
                    soft=False,
                    order_value=order_value,
                    receipt_value=receipt_value,
                    invoice_value=invoice_value,
                    ratio=ratio,
                    receipt_missing=True,
                ),
                pick_reason=SLOT_REASONS["total_amount"],
            ),
            False,
            True,
            False,
        )

    return _compare_numeric(
        "total_amount",
        order_value,
        receipt_value,
        invoice_value,
        AMOUNT_TOLERANCE,
        explain_fn=_amt_explain,
        pick_reason=SLOT_REASONS["total_amount"],
    )


def _present_nums(*pairs: tuple[str, float]) -> dict[str, float]:
    return {name: float(val) for name, val in pairs if float(val) > 0}


def _compare_quantity(
    order_value: float,
    receipt_value: float,
    invoice_value: float,
) -> Tuple[MatchResult, bool, bool, bool]:
    present = _present_nums(
        ("order", order_value),
        ("receipt", receipt_value),
        ("invoice", invoice_value),
    )

    def _qty_explain(**kw):
        return _explain_quantity(present_n=len(present), **kw)

    if len(present) < 2:
        return (
            MatchResult(
                field_name="quantity",
                order_value=order_value,
                receipt_value=receipt_value,
                invoice_value=invoice_value,
                is_consistent=True,
                diff_description="数量不足两方有值，本维未测（不作为三单失败）",
                auditor_explain=_explain_quantity(
                    consistent=True,
                    hard=False,
                    soft=False,
                    order_value=order_value,
                    receipt_value=receipt_value,
                    invoice_value=invoice_value,
                    ratio=0.0,
                    present_n=len(present),
                ),
                pick_reason=SLOT_REASONS["quantity"],
            ),
            True,
            False,
            False,
        )
    if len(present) == 2:
        vals = tuple(present.values())
        ratio = _spread_ratio(vals)
        names = "↔".join(present.keys())
        note = f"仅勾稽{names}（缺的一方数量维未测）"
        if _exact_equal(vals) or ratio <= QUANTITY_TOLERANCE:
            soft = not _exact_equal(vals)
            desc = note
            if soft:
                desc = f"{note}；相对极差 {ratio:.2%}（在±{QUANTITY_TOLERANCE:.0%}内）"
            return (
                MatchResult(
                    field_name="quantity",
                    order_value=order_value,
                    receipt_value=receipt_value,
                    invoice_value=invoice_value,
                    is_consistent=True,
                    diff_description=desc,
                    auditor_explain=_explain_quantity(
                        consistent=True,
                        hard=False,
                        soft=soft,
                        order_value=order_value,
                        receipt_value=receipt_value,
                        invoice_value=invoice_value,
                        ratio=ratio,
                        present_n=2,
                    ),
                    pick_reason=SLOT_REASONS["quantity"],
                ),
                True,
                False,
                soft,
            )
        return (
            MatchResult(
                field_name="quantity",
                order_value=order_value,
                receipt_value=receipt_value,
                invoice_value=invoice_value,
                is_consistent=False,
                diff_description=(
                    f"{note}；超出±{QUANTITY_TOLERANCE:.0%}容差（相对极差 {ratio:.2%}）"
                ),
                auditor_explain=_explain_quantity(
                    consistent=False,
                    hard=True,
                    soft=False,
                    order_value=order_value,
                    receipt_value=receipt_value,
                    invoice_value=invoice_value,
                    ratio=ratio,
                    present_n=2,
                ),
                pick_reason=SLOT_REASONS["quantity"],
            ),
            False,
            True,
            False,
        )
    return _compare_numeric(
        "quantity",
        order_value,
        receipt_value,
        invoice_value,
        QUANTITY_TOLERANCE,
        explain_fn=_qty_explain,
        pick_reason=SLOT_REASONS["quantity"],
    )


def _decide(
    *,
    supplier_ok: bool,
    amount_hard: bool,
    quantity_hard: bool,
    soft_warnings: List[str],
    risks: List[str],
) -> Tuple[Literal["PASS", "WARNING", "FAIL"], Decision, Optional[str], List[str]]:
    reasons: List[str] = []
    if not supplier_ok:
        reasons.append("购方名称不一致或缺失")
        reasons.extend(risks[:2])
        return "FAIL", "HOLD_REVIEW", "PAPER_FIELD", reasons
    if amount_hard or quantity_hard:
        if amount_hard:
            reasons.append("金额超出容差")
        if quantity_hard:
            reasons.append("数量超出容差")
        reasons.extend(risks[:2])
        return "FAIL", "HOLD_REVIEW", "PAPER_FIELD", reasons
    if soft_warnings:
        reasons.append("硬规则通过，存在容差内偏差")
        reasons.extend(soft_warnings[:3])
        return "WARNING", "PASS_WITH_WARNING", None, reasons
    reasons.append("纸面字段勾稽通过")
    return "PASS", "AUTO_PASS", None, reasons


def _build_summary(status: str, decision: str, risks: List[str]) -> str:
    if status == "PASS":
        return f"三单匹配通过（{decision}）"
    if status == "WARNING":
        hint = risks[0] if risks else "存在可接受偏差"
        return f"三单匹配需关注（{decision}）：{hint}"
    if risks:
        return f"三单匹配待复核（{decision}）：{risks[0]}"
    return f"三单匹配待复核（{decision}）"


def run_match(request: ThreeWayMatchRequest) -> ThreeWayMatchResponse:
    """对订单、签收单、发票执行三单匹配，返回标准化结果。"""
    order = request.order
    receipt = request.warehouse_receipt
    invoice = request.invoice

    comparisons: List[MatchResult] = []
    risks: List[str] = []
    soft_warnings: List[str] = []

    supplier_cmp, supplier_ok = _compare_supplier(
        order.supplier_name, receipt.supplier_name, invoice.supplier_name
    )
    comparisons.append(supplier_cmp)
    if not supplier_ok:
        risks.append(supplier_cmp.diff_description or "客户名称不一致")

    amount_cmp, _amount_ok, amount_hard, amount_soft = _compare_amount(
        order.total_amount,
        receipt.total_amount,
        invoice.total_amount,
    )
    comparisons.append(amount_cmp)
    if amount_hard or not amount_cmp.is_consistent:
        risks.append(amount_cmp.diff_description or "金额不一致")
    elif amount_soft:
        soft_warnings.append(amount_cmp.diff_description or "金额容差内偏差")

    qty_cmp, _qty_ok, qty_hard, qty_soft = _compare_quantity(
        order.quantity,
        receipt.quantity,
        invoice.quantity,
    )
    comparisons.append(qty_cmp)
    if qty_hard or not qty_cmp.is_consistent:
        risks.append(qty_cmp.diff_description or "数量不一致")
    elif qty_soft:
        soft_warnings.append(qty_cmp.diff_description or "数量容差内偏差")

    status, decision, hold_code, decision_reasons = _decide(
        supplier_ok=supplier_ok,
        amount_hard=amount_hard,
        quantity_hard=qty_hard,
        soft_warnings=soft_warnings,
        risks=risks,
    )
    summary = _build_summary(status, decision, risks)

    return ThreeWayMatchResponse(
        order_no=order.order_no,
        overall_status=status,
        match_score=0.0,
        comparisons=comparisons,
        summary=summary,
        risks=risks,
        decision=decision,
        decision_reasons=decision_reasons,
        hold_reason_code=hold_code,
        quantity_roles={
            "ordered_qty": float(order.quantity),
            "received_qty": float(receipt.quantity),
            "invoiced_qty": float(invoice.quantity),
        },
        slot_reasons=dict(SLOT_REASONS),
        erp_review=dict(ERP_REVIEW),
    )
