"""金额测试：兼容旧接口 + 手册级准确性测试。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from src.amount_test.engine import TECHNICAL_TOLERANCE, recalculate_amount, r2
from src.amount_test.models import AmountAccuracyReport, AmountStatus
from src.amount_test.pricing_extract import merge_pricing_from_documents
from src.amount_test.runner import run_amount_accuracy_test
from src.amount_test.models import LedgerValues

# 旧容差（相对）保留给历史 check_document_amount
TOLERANCE_OK = 0.02
TOLERANCE_WARN = 0.05


class AmountCalcBreakdown(BaseModel):
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    discount_rate: Optional[float] = None
    discount_amount: Optional[float] = None
    tax_rate: Optional[float] = None
    net_amount: Optional[float] = None
    tax_amount: Optional[float] = None
    gross_amount: Optional[float] = None
    formula: str = ""
    amount_unit: str = "元"


class AmountDocCheck(BaseModel):
    role: str
    file_name: str = ""
    book_amount: Optional[float] = None
    recalculated_amount: Optional[float] = None
    deviation_amount: Optional[float] = None
    deviation_pct: Optional[float] = None
    status: AmountStatus = "SKIPPED"
    breakdown: Optional[AmountCalcBreakdown] = None
    issue: str = ""


class AmountTestResult(BaseModel):
    status: AmountStatus
    checks: List[AmountDocCheck] = Field(default_factory=list)
    issue_description: str = ""
    human_readable_summary: str = ""
    accuracy_report: Optional[AmountAccuracyReport] = None
    advisory_candidates: List[Dict[str, Any]] = Field(default_factory=list)


def recalculate_gross_yuan(
    fields: Dict[str, Any],
    *,
    ocr_text: str = "",
) -> Optional[AmountCalcBreakdown]:
    from src.amount_test.pricing_extract import extract_from_fields

    extracted = extract_from_fields(fields, ocr_text)
    qty = extracted.get("quantity")
    price = extracted.get("unit_price_excl_tax")
    if not qty or not price:
        return None
    recalc = recalculate_amount(
        quantity=float(qty),
        unit_price_excl_tax=float(price),
        discount_rate=float(extracted.get("discount_rate") or 0.0),
        vat_rate=float(extracted.get("vat_rate") or 0.0),
    )
    return AmountCalcBreakdown(
        quantity=float(qty),
        unit_price=float(price),
        discount_rate=float(extracted.get("discount_rate") or 0.0) or None,
        discount_amount=recalc.discount_amount_excl_tax,
        tax_rate=float(extracted.get("vat_rate") or 0.0) or None,
        net_amount=recalc.net_amount_excl_tax,
        tax_amount=recalc.vat_amount,
        gross_amount=recalc.gross_amount_incl_tax,
        formula=recalc.formula,
    )


def run_amount_test(
    documents: Sequence[Dict[str, Any]],
    *,
    ledger_amount: Optional[float] = None,
    roles: Sequence[str] = ("order", "invoice"),
    voucher_no: str = "",
    sales_order_no: str = "",
    customer_name: str = "",
    existing_advisory: Optional[List[Dict[str, Any]]] = None,
) -> AmountTestResult:
    """工作流兼容入口：有序时账金额时走手册级准确性测试。"""
    ledger = LedgerValues(
        voucher_no=voucher_no,
        customer_name=customer_name,
        sales_order_no=sales_order_no,
        ledger_debit_total=float(ledger_amount) if ledger_amount is not None else None,
        ledger_credit_total=float(ledger_amount) if ledger_amount is not None else None,
        ledger_ar_debit=float(ledger_amount) if ledger_amount is not None else None,
        amount_basis="GROSS_AMOUNT_INCL_TAX",
    )
    if ledger_amount is not None:
        # 历史：极小值按万元
        led = float(ledger_amount)
        if 0 < led < 5000:
            # 仅当明显像万元口径时放大；手册 Mock 为元，若已是元则不放大
            # 保留旧行为但门槛收紧：金额准确性场景默认已是元
            pass
        report = run_amount_accuracy_test(
            documents=list(documents),
            ledger=ledger,
            business_id=sales_order_no,
            tolerance=TECHNICAL_TOLERANCE,
            existing_advisory=existing_advisory,
        )
        check = AmountDocCheck(
            role="ledger_vs_recalc",
            file_name="序时账",
            book_amount=report.ledger_values.ledger_debit_total,
            recalculated_amount=report.recalculation.gross_amount_incl_tax,
            deviation_amount=report.amount_test.difference_amount,
            deviation_pct=(
                abs(report.amount_test.difference_rate)
                if report.amount_test.difference_rate is not None
                else None
            ),
            status=report.amount_test.test_status,
            issue=report.amount_test.issue_description,
            breakdown=AmountCalcBreakdown(
                quantity=report.source_values.quantity,
                unit_price=report.source_values.unit_price_excl_tax,
                discount_rate=report.source_values.discount_rate,
                tax_rate=report.source_values.vat_rate,
                net_amount=report.recalculation.net_amount_excl_tax,
                tax_amount=report.recalculation.vat_amount,
                gross_amount=report.recalculation.gross_amount_incl_tax,
                formula=report.recalculation.formula,
            ),
        )
        return AmountTestResult(
            status=report.amount_test.test_status,
            checks=[check],
            issue_description=report.amount_test.issue_description,
            human_readable_summary=report.human_readable_summary,
            accuracy_report=report,
            advisory_candidates=list(report.advisory_candidates or []),
        )

    # 无序时账：仅重算订单/发票并做单据内勾稽（绝对容差）
    checks: List[AmountDocCheck] = []
    source, _, warnings, advisory_store = merge_pricing_from_documents(
        documents,
        existing_advisory=existing_advisory,
        business_id=sales_order_no,
    )
    if source.quantity and source.unit_price_excl_tax:
        recalc = recalculate_amount(
            quantity=float(source.quantity),
            unit_price_excl_tax=float(source.unit_price_excl_tax),
            discount_rate=float(source.discount_rate or 0.0),
            vat_rate=float(source.vat_rate or 0.0),
        )
        for item in documents:
            role = str(item.get("doc_type") or "")
            if role not in roles:
                continue
            fields = dict(item.get("fields") or {})
            book = None
            for key in ("totalAmount", "价税合计", "amount"):
                from src.legacy_ocr.amount_resolve import _parse_number

                book = _parse_number(fields.get(key))
                if book:
                    break
            if book is None or recalc.gross_amount_incl_tax is None:
                checks.append(
                    AmountDocCheck(
                        role=role,
                        file_name=str(item.get("file_name") or ""),
                        recalculated_amount=recalc.gross_amount_incl_tax,
                        status="WARNING" if book is None else "SKIPPED",
                        issue="无账面价税合计可对" if book is None else "",
                        breakdown=AmountCalcBreakdown(
                            quantity=source.quantity,
                            unit_price=source.unit_price_excl_tax,
                            discount_rate=source.discount_rate,
                            tax_rate=source.vat_rate,
                            net_amount=recalc.net_amount_excl_tax,
                            tax_amount=recalc.vat_amount,
                            gross_amount=recalc.gross_amount_incl_tax,
                            formula=recalc.formula,
                        ),
                    )
                )
                continue
            diff = r2(float(recalc.gross_amount_incl_tax) - float(book))
            # 单据内比较：重算为权威，与票面勾稽
            status: AmountStatus = "PASS" if abs(diff) <= TECHNICAL_TOLERANCE else "FAIL"
            checks.append(
                AmountDocCheck(
                    role=role,
                    file_name=str(item.get("file_name") or ""),
                    book_amount=r2(float(book)),
                    recalculated_amount=recalc.gross_amount_incl_tax,
                    deviation_amount=diff,
                    deviation_pct=abs(diff) / max(abs(float(book)), 1e-9),
                    status=status,
                    issue=(
                        "重算与票面一致"
                        if status == "PASS"
                        else f"重算与票面差 {diff:+.2f} 元"
                    ),
                    breakdown=AmountCalcBreakdown(
                        quantity=source.quantity,
                        unit_price=source.unit_price_excl_tax,
                        discount_rate=source.discount_rate,
                        tax_rate=source.vat_rate,
                        net_amount=recalc.net_amount_excl_tax,
                        tax_amount=recalc.vat_amount,
                        gross_amount=recalc.gross_amount_incl_tax,
                        formula=recalc.formula,
                    ),
                )
            )
    else:
        checks.append(
            AmountDocCheck(
                role="pricing",
                status="WARNING",
                issue="；".join(warnings) or "缺少数量/单价",
            )
        )

    rank = {"PASS": 0, "SKIPPED": 0, "WARNING": 1, "FAIL": 2}
    overall: AmountStatus = "SKIPPED" if not checks else "PASS"
    for c in checks:
        if c.status == "SKIPPED":
            continue
        if rank.get(c.status, 0) > rank.get(overall, 0):
            overall = c.status  # type: ignore[assignment]
    issues = [c.issue for c in checks if c.status in {"WARNING", "FAIL"} and c.issue]
    return AmountTestResult(
        status=overall,
        checks=checks,
        issue_description="；".join(issues) if issues else "金额重算完成",
        human_readable_summary=f"金额测试 {overall}",
        advisory_candidates=list(advisory_store or []),
    )
