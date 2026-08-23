"""金额重算、三层比较与错报诊断（对齐实施手册 §8 / §9 / §11）。

金额计算统一 Decimal（元），对外仍返回 float（两位）以兼容模型字段。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional, Tuple, Union

from src.amount_test.models import (
    AmountTestDetail,
    Direction,
    IssueType,
    LedgerValues,
    Recalculation,
    SourceValues,
)

TECHNICAL_TOLERANCE = 0.02  # 元；仅覆盖四舍五入，不用审计重要性掩盖差异
_TWOPLACES = Decimal("0.01")
NumberLike = Union[float, int, str, Decimal]


def _d(value: NumberLike) -> Decimal:
    return Decimal(str(value or 0))


def r2(value: NumberLike) -> float:
    """金额两位（银行家以外的四舍五入 HALF_UP）→ float。"""
    return float(_d(value).quantize(_TWOPLACES, rounding=ROUND_HALF_UP))


def recalculate_domestic(
    *,
    quantity: float,
    unit_price_excl_tax: float,
    discount_rate: float = 0.0,
    vat_rate: float = 0.13,
) -> Recalculation:
    d = max(Decimal("0"), min(_d(discount_rate), Decimal("1")))
    t = max(Decimal("0"), _d(vat_rate))
    q = _d(quantity)
    p = _d(unit_price_excl_tax)
    gross_before = r2(q * p)
    net = r2(q * p * (Decimal("1") - d))
    discount_amt = r2(_d(gross_before) - _d(net))
    vat = r2(_d(net) * t)
    gross = r2(_d(net) + _d(vat))
    return Recalculation(
        gross_before_discount_excl_tax=gross_before,
        discount_amount_excl_tax=discount_amt,
        net_amount_excl_tax=net,
        vat_amount=vat,
        gross_amount_incl_tax=gross,
        rounding_rule="LINE_LEVEL_2_DECIMALS_DECIMAL",
        formula="净额=round(数量×单价×(1-折扣率),2)；税额=round(净额×税率,2)；价税合计=净额+税额（Decimal/元）",
        is_export=False,
    )


def recalculate_export(
    *,
    quantity: float,
    unit_price_excl_tax: float,
    discount_rate: float = 0.0,
) -> Recalculation:
    d = max(Decimal("0"), min(_d(discount_rate), Decimal("1")))
    q = _d(quantity)
    p = _d(unit_price_excl_tax)
    gross_before = r2(q * p)
    net = r2(q * p * (Decimal("1") - d))
    discount_amt = r2(_d(gross_before) - _d(net))
    return Recalculation(
        gross_before_discount_excl_tax=gross_before,
        discount_amount_excl_tax=discount_amt,
        net_amount_excl_tax=net,
        vat_amount=0.0,
        gross_amount_incl_tax=net,
        rounding_rule="LINE_LEVEL_2_DECIMALS_DECIMAL",
        formula="出口销售金额=round(数量×单价×(1-折扣率),2)；销项税=0（Decimal/元）",
        is_export=True,
    )


def recalculate_amount(
    *,
    quantity: float,
    unit_price_excl_tax: float,
    discount_rate: float = 0.0,
    vat_rate: float = 0.0,
    force_export: Optional[bool] = None,
) -> Recalculation:
    is_export = (
        bool(force_export) if force_export is not None else float(vat_rate or 0.0) <= 0.0
    )
    if is_export:
        return recalculate_export(
            quantity=quantity,
            unit_price_excl_tax=unit_price_excl_tax,
            discount_rate=discount_rate,
        )
    return recalculate_domestic(
        quantity=quantity,
        unit_price_excl_tax=unit_price_excl_tax,
        discount_rate=discount_rate,
        vat_rate=vat_rate,
    )


def _pick_ledger_gross(ledger: LedgerValues) -> Optional[float]:
    for val in (
        ledger.ledger_debit_total,
        ledger.ledger_credit_total,
        ledger.ledger_ar_debit,
    ):
        if val is not None:
            return float(val)
    return None


def compare_layers(
    *,
    recalc: Recalculation,
    ledger: LedgerValues,
    tolerance: float = TECHNICAL_TOLERANCE,
) -> Dict[str, Any]:
    expected_net = recalc.net_amount_excl_tax
    expected_vat = recalc.vat_amount
    expected_gross = recalc.gross_amount_incl_tax
    ledger_gross = _pick_ledger_gross(ledger)
    out: Dict[str, Any] = {
        "expected_net": expected_net,
        "expected_vat": expected_vat,
        "expected_gross": expected_gross,
        "ledger_gross": ledger_gross,
        "revenue_diff": None,
        "vat_diff": None,
        "gross_diff": None,
        "revenue_ok": None,
        "vat_ok": None,
        "gross_ok": None,
    }
    if ledger_gross is not None and expected_gross is not None:
        gd = r2(ledger_gross - expected_gross)
        out["gross_diff"] = gd
        out["gross_ok"] = abs(gd) <= tolerance
    if ledger.ledger_revenue_credit is not None and expected_net is not None:
        rd = r2(float(ledger.ledger_revenue_credit) - expected_net)
        out["revenue_diff"] = rd
        out["revenue_ok"] = abs(rd) <= tolerance
    if ledger.ledger_output_vat_credit is not None and expected_vat is not None:
        vd = r2(float(ledger.ledger_output_vat_credit) - expected_vat)
        out["vat_diff"] = vd
        out["vat_ok"] = abs(vd) <= tolerance
    return out


def _looks_like_discount_typo(correct: float, implied: float) -> bool:
    if implied <= -0.001 or implied >= 0.5:
        return False
    delta = abs(implied - correct)
    return 0.002 <= delta <= 0.05


def _diagnose_price_or_discount(
    source: SourceValues,
    layers: Dict[str, Any],
) -> Tuple[IssueType, str]:
    """收入与税额同时偏离时，按手册优先用账面隐含单价判断。

    注意：在仅有收入/税额总额时，单价错误与折扣错误数学对偶；
    默认输出单价录入偏差，若隐含折扣显著偏离且更像折扣误录，则标折扣。
    """
    qty = source.quantity
    price = source.unit_price_excl_tax
    disc = float(source.discount_rate or 0.0)
    t = float(source.vat_rate or 0.0)
    lg = layers.get("ledger_gross")
    # 优先使用分列收入
    book_net = layers.get("revenue_diff")
    # revenue_diff = ledger_rev - expected_net; 还原 ledger_rev
    expected_net = layers.get("expected_net")
    ledger_rev = None
    if layers.get("revenue_diff") is not None and expected_net is not None:
        ledger_rev = r2(float(expected_net) + float(layers["revenue_diff"]))
    if ledger_rev is None and lg is not None:
        ledger_rev = r2(float(lg) / (1.0 + t)) if t > 0 else float(lg)

    if not qty or not price or ledger_rev is None:
        return "AMOUNT_ENTRY_ERROR", "收入与税额同时偏离，但无法细分单价/折扣"

    implied_price = ledger_rev / qty / max(1.0 - disc, 1e-9)
    implied_disc = 1.0 - ledger_rev / (qty * price)
    price_rel = abs(implied_price - price) / max(abs(price), 1e-9)

    if disc > 1e-12 and _looks_like_discount_typo(disc, implied_disc):
        # 仅当隐含单价几乎不变时，才认定为「基础单价正确、折扣错误」
        if price_rel <= 0.0001:
            return (
                "COMMERCIAL_DISCOUNT_ERROR",
                f"基础单价一致，隐含折扣 {implied_disc:.4%} 与合同 {disc:.4%} 不一致",
            )

    if price_rel > 1e-9:
        return (
            "UNIT_PRICE_ENTRY_ERROR",
            f"账面隐含不含税单价 {implied_price:.4f} 与合同单价 {price:.4f} 不一致",
        )
    return (
        "COMMERCIAL_DISCOUNT_ERROR",
        "基础单价一致，但折扣率或折扣基数与合同不一致",
    )


def diagnose_issue_type(
    *,
    source: SourceValues,
    recalc: Recalculation,
    ledger: LedgerValues,
    layers: Dict[str, Any],
    tolerance: float = TECHNICAL_TOLERANCE,
) -> Tuple[IssueType, str]:
    gross_diff = layers.get("gross_diff")
    if gross_diff is None:
        return "NONE", "无账面总额可比较"
    if abs(gross_diff) <= tolerance:
        return "NONE", "在技术容差内"

    rev_ok = layers.get("revenue_ok")
    vat_ok = layers.get("vat_ok")
    has_split = rev_ok is not None or vat_ok is not None

    if has_split:
        if rev_ok is True and vat_ok is False:
            return "OUTPUT_VAT_ENTRY_ERROR", "收入金额正确，仅销项税额与重算不一致"
        if rev_ok is False and vat_ok is False:
            return _diagnose_price_or_discount(source, layers)
        if rev_ok is False and vat_ok is True:
            # 出口零税率：税额恒为 0，收入偏差仍属单价/折扣
            if float(source.vat_rate or 0.0) <= 0.0 or float(recalc.vat_amount or 0.0) == 0.0:
                return _diagnose_price_or_discount(source, layers)
            return "AMOUNT_ENTRY_ERROR", "收入不一致但税额一致，可能为口径映射问题"
        if layers.get("gross_ok") is False and rev_ok is True and vat_ok is True:
            return (
                "LEDGER_BASIS_MISMATCH",
                "收入及税额正确但应收总额不一致，疑似汇总/口径映射问题",
            )

    # 汇总账：若调用方按「税额错记」语义填了收入=重算净额、税额=账面-净额，上面已覆盖。
    # 否则仅用总额时，单价/折扣可部分区分；销项税与比例错报对偶，默认走单价/折扣启发式。
    t = float(source.vat_rate or 0.0)
    en = recalc.net_amount_excl_tax
    lg = layers.get("ledger_gross")
    if (
        t > 0
        and en is not None
        and lg is not None
        and ledger.ledger_revenue_credit is None
        and ledger.ledger_output_vat_credit is None
    ):
        # 可选增强：若总额差恰好等于「错税」且与比例单价叙事冲突信号弱，仍优先单价/折扣。
        pass

    return _diagnose_price_or_discount(source, layers)


def build_amount_test_detail(
    *,
    source: SourceValues,
    recalc: Recalculation,
    ledger: LedgerValues,
    tolerance: float = TECHNICAL_TOLERANCE,
) -> AmountTestDetail:
    layers = compare_layers(recalc=recalc, ledger=ledger, tolerance=tolerance)
    lg = layers.get("ledger_gross")
    eg = layers.get("expected_gross")
    if lg is None or eg is None:
        return AmountTestDetail(
            test_status="WARNING",
            risk_level="证据不足",
            issue_type="NONE",
            issue_description="缺少账面金额或无法重算价税合计",
            technical_tolerance=tolerance,
            layer_diffs=layers,
        )

    diff = r2(float(lg) - float(eg))
    rate = diff / float(eg) if abs(float(eg)) > 1e-9 else None
    if abs(diff) <= tolerance:
        return AmountTestDetail(
            test_status="PASS",
            risk_level="无异常",
            issue_type="NONE",
            difference_amount=diff,
            difference_rate=rate,
            direction="NONE",
            issue_description="重算不含税金额、税额及含税总额与账面对应口径一致",
            technical_tolerance=tolerance,
            layer_diffs=layers,
        )

    issue_type, why = diagnose_issue_type(
        source=source,
        recalc=recalc,
        ledger=ledger,
        layers=layers,
        tolerance=tolerance,
    )
    direction: Direction = "BOOK_OVERSTATED" if diff > 0 else "BOOK_UNDERSTATED"
    dir_cn = "多记" if diff > 0 else "少记"
    type_cn = {
        "UNIT_PRICE_ENTRY_ERROR": "单价录入偏差",
        "COMMERCIAL_DISCOUNT_ERROR": "商业折扣计算偏差",
        "OUTPUT_VAT_ENTRY_ERROR": "销项税额计算偏差",
        "AMOUNT_ENTRY_ERROR": "金额计算偏差",
        "LEDGER_BASIS_MISMATCH": "金额口径映射问题",
    }.get(issue_type, "金额差异")
    return AmountTestDetail(
        test_status="FAIL",
        risk_level="明确金额差异-应调整",
        issue_type=issue_type,
        difference_amount=diff,
        difference_rate=rate,
        direction=direction,
        issue_description=(
            f"账面价税合计较原始单据重算金额{dir_cn}{abs(diff):.2f}元"
            f"（{type_cn}）。{why}"
        ),
        technical_tolerance=tolerance,
        layer_diffs=layers,
    )


def enrich_ledger_split_for_vat_hypothesis(
    ledger: LedgerValues,
    *,
    expected_net: float,
) -> LedgerValues:
    """将汇总总额按「收入锁定为重算净额、税额=总额-净额」展开，供销项税诊断。"""
    gross = _pick_ledger_gross(ledger)
    if gross is None:
        return ledger
    data = ledger.model_dump()
    data["ledger_revenue_credit"] = r2(expected_net)
    data["ledger_output_vat_credit"] = r2(float(gross) - expected_net)
    return LedgerValues(**data)


def enrich_ledger_split_proportional(
    ledger: LedgerValues,
    *,
    vat_rate: float,
) -> LedgerValues:
    """将汇总总额按税率比例拆成收入+销项税。"""
    gross = _pick_ledger_gross(ledger)
    if gross is None:
        return ledger
    t = float(vat_rate or 0.0)
    if t <= 0:
        data = ledger.model_dump()
        data["ledger_revenue_credit"] = r2(float(gross))
        data["ledger_output_vat_credit"] = 0.0
        return LedgerValues(**data)
    net = r2(float(gross) / (1.0 + t))
    vat = r2(float(gross) - net)
    data = ledger.model_dump()
    data["ledger_revenue_credit"] = net
    data["ledger_output_vat_credit"] = vat
    return LedgerValues(**data)
