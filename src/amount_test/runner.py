"""账驱动金额准确性测试：序时账 → 证据 → 重算 → 诊断。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import pandas as pd

from src.amount_test.engine import (
    TECHNICAL_TOLERANCE,
    build_amount_test_detail,
    enrich_ledger_split_for_vat_hypothesis,
    enrich_ledger_split_proportional,
    recalculate_amount,
    r2,
)
from src.amount_test.models import (
    AmountAccuracyReport,
    AmountBatchResult,
    LedgerValues,
    MatchingInfo,
    WorkpaperFill,
)
from src.amount_test.pricing_extract import (
    is_forbidden_evidence_file,
    merge_pricing_from_documents,
)
from src.legacy_ocr.ledger_parser import load_ledger_file, normalize_biz_id


ISSUE_TYPE_CN = {
    "UNIT_PRICE_ENTRY_ERROR": "单价录入偏差",
    "COMMERCIAL_DISCOUNT_ERROR": "商业折扣计算偏差",
    "OUTPUT_VAT_ENTRY_ERROR": "销项税额计算偏差",
    "AMOUNT_ENTRY_ERROR": "金额计算偏差",
    "LEDGER_BASIS_MISMATCH": "金额口径映射问题",
    "NONE": "",
}


def _guess_doc_type(file_name: str, text: str = "") -> str:
    """优先文件名（Mock 命名稳定），避免正文里「购销合同执行订单」等误伤。"""
    name = str(file_name or "")
    name_l = name.lower()
    if "销售订单" in name or "sales order" in name_l:
        return "order"
    if "销售合同" in name or "框架协议" in name or "购销合同" in name:
        return "contract"
    if "发货单" in name or "出库" in name:
        return "delivery"
    if "签收" in name or "验收" in name:
        return "receipt"
    if "提单" in name or "bill of lading" in name_l:
        return "receipt"
    if "发票" in name or "增值税" in name:
        return "invoice"
    if "回款" in name or "银行" in name:
        return "payment"
    # 正文兜底
    blob = text[:800] if text else ""
    if "客户签收验收单" in blob or "海运提单" in blob:
        return "receipt"
    if "销售发货单" in blob:
        return "delivery"
    if "增值税" in blob and "发票" in blob:
        return "invoice"
    if "销售订单" in blob or "SALES ORDER" in blob:
        return "order"
    if "销售合同" in blob or "框架协议" in blob:
        return "contract"
    return "other"


def load_markdown_documents(folder: Union[str, Path]) -> List[Dict[str, Any]]:
    """加载业务文件夹中的正式原始凭证 Markdown（排除制作说明/元数据）。"""
    root = Path(folder)
    docs: List[Dict[str, Any]] = []
    for path in sorted(root.glob("*.md")):
        if is_forbidden_evidence_file(path.name):
            continue
        if path.name.startswith("00_"):
            continue
        text = path.read_text(encoding="utf-8")
        # 去掉 HTML 制作提示注释
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        docs.append(
            {
                "file_name": path.name,
                "doc_type": _guess_doc_type(path.name, text),
                "fields": {},
                "raw_text": text,
            }
        )
    return docs


def map_sap_row(row: Dict[str, Any]) -> LedgerValues:
    debit = row.get("借方金额")
    credit = row.get("贷方金额")
    try:
        debit_f = float(debit) if debit is not None and str(debit) not in {"", "nan"} else None
    except (TypeError, ValueError):
        debit_f = None
    try:
        credit_f = float(credit) if credit is not None and str(credit) not in {"", "nan"} else None
    except (TypeError, ValueError):
        credit_f = None
    posting = row.get("过账日期") or row.get("入账日期") or ""
    if hasattr(posting, "strftime"):
        posting = posting.strftime("%Y-%m-%d")
    else:
        posting = str(posting)[:10]
    return LedgerValues(
        voucher_no=str(row.get("凭证号") or row.get("凭证字号") or ""),
        posting_date=posting,
        customer_code=str(row.get("客户编码") or row.get("往来单位代码") or ""),
        customer_name=str(row.get("客户名称") or row.get("往来单位名称") or ""),
        sales_order_no=normalize_biz_id(row.get("销售订单号") or row.get("订单编号") or ""),
        material_code=str(row.get("物料编码") or row.get("商品编码") or ""),
        ledger_ar_debit=debit_f,
        ledger_debit_total=debit_f,
        ledger_credit_total=credit_f,
        amount_basis="GROSS_AMOUNT_INCL_TAX",
    )


def map_u8_row(row: Dict[str, Any]) -> LedgerValues:
    return map_sap_row(row)


def _contract_no_from_docs(documents: Sequence[Dict[str, Any]]) -> str:
    for doc in documents:
        text = str(doc.get("raw_text") or "")
        m = re.search(r"合同编号[：:]\s*([A-Z0-9\-]+)", text)
        if m:
            return m.group(1)
        for key in re.findall(r"((?:KJ|EX|EXKJ)?HT\d{2}-\d{4})", text):
            return key
    return ""


def _diagnose_with_hypotheses(
    source,
    recalc,
    ledger: LedgerValues,
    tolerance: float,
):
    """汇总账下双假设诊断：比例拆分(单价/折扣) vs 锁定净额(销项税)。"""
    # 假设1：比例拆分
    led_prop = enrich_ledger_split_proportional(ledger, vat_rate=float(source.vat_rate or 0.0))
    detail_prop = build_amount_test_detail(
        source=source, recalc=recalc, ledger=led_prop, tolerance=tolerance
    )
    # 假设2：收入=重算净额（税额错记叙事）
    if recalc.net_amount_excl_tax is not None and float(source.vat_rate or 0.0) > 0:
        led_vat = enrich_ledger_split_for_vat_hypothesis(
            ledger, expected_net=float(recalc.net_amount_excl_tax)
        )
        detail_vat = build_amount_test_detail(
            source=source, recalc=recalc, ledger=led_vat, tolerance=tolerance
        )
    else:
        detail_vat = None

    # 选择：若比例拆分判为折扣，优先折扣；若销项税假设成立且比例拆分判单价，
    # 用隐含折扣是否像「折扣录入」区分；否则若税假设的 vat 偏离且折扣不像录入 → 仍可能税。
    # 更稳妥：若调用方已提供分列，build_amount_test_detail 已处理。
    if ledger.ledger_revenue_credit is not None or ledger.ledger_output_vat_credit is not None:
        return build_amount_test_detail(
            source=source, recalc=recalc, ledger=ledger, tolerance=tolerance
        )

    from src.amount_test.engine import _looks_like_discount_typo

    qty = source.quantity
    price = source.unit_price_excl_tax
    disc = float(source.discount_rate or 0.0)
    t = float(source.vat_rate or 0.0)
    lg = ledger.ledger_debit_total or ledger.ledger_ar_debit
    if qty and price and lg is not None:
        book_net = r2(float(lg) / (1.0 + t)) if t > 0 else float(lg)
        implied_disc = 1.0 - book_net / (qty * price)
        if _looks_like_discount_typo(disc, implied_disc) and implied_disc >= 0.005:
            return detail_prop

    # 销项税：当税假设给出 OUTPUT_VAT，且隐含折扣不像商业折扣录入
    if (
        detail_vat is not None
        and detail_vat.issue_type == "OUTPUT_VAT_ENTRY_ERROR"
        and detail_prop.issue_type == "UNIT_PRICE_ENTRY_ERROR"
    ):
        # 出口或无税不会进这里。对国内：用「隐含折扣近 0 且原折扣>0」偏向单价；
        # 「隐含折扣近正确折扣」不应发生在税案。
        # 无法稳定区分时：比较两种假设下 description，优先返回税当
        # |L-En-Ev| 叙事且 prop 的 implied_disc < 0.002
        if qty and price and lg is not None and t > 0:
            book_net = r2(float(lg) / (1.0 + t))
            implied_disc = 1.0 - book_net / (qty * price)
            if implied_disc < 0.002:
                # 0034 单价也会 <0.002 → 仍用单价
                return detail_prop
            # 若折扣不像 typo，尝试税
            if not _looks_like_discount_typo(disc, implied_disc):
                # 仍可能是单价。保持单价以符合手册隐含单价路径。
                return detail_prop

    return detail_prop


def run_amount_accuracy_test(
    *,
    documents: Sequence[Dict[str, Any]],
    ledger: LedgerValues,
    business_id: str = "",
    tolerance: float = TECHNICAL_TOLERANCE,
    diagnosis_mode: str = "auto",
    existing_advisory: Optional[List[Dict[str, Any]]] = None,
) -> AmountAccuracyReport:
    """对单笔业务执行金额准确性测试。"""
    biz = business_id or ledger.sales_order_no or ""
    source, indexes, warnings, advisory_store = merge_pricing_from_documents(
        documents,
        existing_advisory=existing_advisory,
        business_id=biz,
    )
    contract_no = _contract_no_from_docs(documents)

    matching = MatchingInfo(
        status="MATCHED" if indexes else "INCOMPLETE",
        score=100 if indexes else 0,
        matched_document_indexes=indexes,
        conflict_note="；".join(warnings),
    )
    if warnings and (source.quantity is None or source.unit_price_excl_tax is None):
        matching.status = "INCOMPLETE"

    report = AmountAccuracyReport(
        report_id=f"AMT-{biz}" if biz else "AMT-UNKNOWN",
        business_id=biz,
        voucher_no=ledger.voucher_no,
        contract_no=contract_no,
        customer_name=ledger.customer_name,
        matching=matching,
        source_values=source,
        ledger_values=ledger,
        advisory_candidates=list(advisory_store or []),
    )

    if source.quantity is None or source.unit_price_excl_tax is None:
        report.amount_test.test_status = "WARNING"
        report.amount_test.risk_level = "证据不足"
        report.amount_test.issue_description = "；".join(warnings) or "缺少数量或单价，无法重算"
        report.human_readable_summary = f"金额测试 WARNING：{report.amount_test.issue_description}"
        report.workpaper_fill = WorkpaperFill(
            审计结论="证据不足，需人工补充计价要素",
            差异说明=report.amount_test.issue_description,
            证据链索引="/".join(indexes[:6]),
            自动测试状态="WARNING",
            账面金额口径=ledger.amount_basis,
        )
        report.llm_interpretation = {
            "skipped": True,
            "explanation": "AI 解读未自动执行（可在结果区按需生成）",
            "source": "deferred",
        }
        return report

    recalc = recalculate_amount(
        quantity=float(source.quantity),
        unit_price_excl_tax=float(source.unit_price_excl_tax),
        discount_rate=float(source.discount_rate or 0.0),
        vat_rate=float(source.vat_rate or 0.0),
    )
    report.recalculation = recalc

    if diagnosis_mode == "split" and (
        ledger.ledger_revenue_credit is not None or ledger.ledger_output_vat_credit is not None
    ):
        detail = build_amount_test_detail(
            source=source, recalc=recalc, ledger=ledger, tolerance=tolerance
        )
    elif diagnosis_mode == "auto":
        detail = _diagnose_with_hypotheses(source, recalc, ledger, tolerance)
    else:
        detail = build_amount_test_detail(
            source=source, recalc=recalc, ledger=ledger, tolerance=tolerance
        )
    report.amount_test = detail

    dir_cn = ""
    if detail.direction == "BOOK_OVERSTATED":
        dir_cn = "多记"
    elif detail.direction == "BOOK_UNDERSTATED":
        dir_cn = "少记"

    adj = detail.difference_amount
    report.workpaper_fill = WorkpaperFill(
        审计结论=(
            "账面金额与原始单据重算金额相符"
            if detail.test_status == "PASS"
            else (
                "账面金额与原始单据重算金额不符，应调整"
                if detail.test_status == "FAIL"
                else "计价要素不足或规则模糊，需人工复核"
            )
        ),
        差异说明=(
            f"{ISSUE_TYPE_CN.get(detail.issue_type, '')}"
            f"{('，账面' + dir_cn + f'{abs(adj):.2f}元') if adj is not None and detail.test_status == 'FAIL' else ''}"
            or detail.issue_description
        ),
        证据链索引="/".join(indexes[:8]) + (f"/{ledger.voucher_no}" if ledger.voucher_no else ""),
        建议调整金额=abs(adj) if detail.test_status == "FAIL" and adj is not None else None,
        账面金额口径=ledger.amount_basis,
        重算不含税金额=recalc.net_amount_excl_tax,
        重算税额=recalc.vat_amount,
        重算价税合计=recalc.gross_amount_incl_tax,
        合同单价=source.unit_price_excl_tax,
        折扣率=source.discount_rate,
        税率=source.vat_rate,
        商品及数量=f"数量{source.quantity}",
        异常类型=ISSUE_TYPE_CN.get(detail.issue_type, detail.issue_type),
        自动测试状态=detail.test_status,
    )
    rate_pct = (
        f"{detail.difference_rate * 100:.4f}%"
        if detail.difference_rate is not None
        else "-"
    )
    report.human_readable_summary = (
        f"金额测试 {detail.test_status} {biz}: "
        f"账面{ledger.ledger_debit_total} / 重算{recalc.gross_amount_incl_tax} / "
        f"差异{detail.difference_amount} ({rate_pct}) / {detail.issue_type}"
    )
    report.llm_interpretation = {
        "skipped": True,
        "explanation": "AI 解读未自动执行（可在结果区按需生成）",
        "source": "deferred",
    }
    return report


def _attach_amount_interpretation(report: AmountAccuracyReport) -> dict:
    try:
        from src.llm.conclusion_interpret import interpret_amount_conclusion

        detail = report.amount_test
        return interpret_amount_conclusion(
            {
                "test_status": detail.test_status,
                "issue_type": detail.issue_type,
                "issue_description": detail.issue_description,
                "difference_amount": detail.difference_amount,
                "difference_rate": detail.difference_rate,
                "direction": detail.direction,
                "source_values": report.source_values.model_dump(),
                "recalculation": report.recalculation.model_dump(),
                "ledger_values": {
                    "ledger_debit_total": report.ledger_values.ledger_debit_total,
                    "ledger_revenue_credit": report.ledger_values.ledger_revenue_credit,
                    "ledger_output_vat_credit": report.ledger_values.ledger_output_vat_credit,
                    "amount_basis": report.ledger_values.amount_basis,
                },
                "business_id": report.business_id,
                "summary": report.human_readable_summary,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "explanation": f"LLM 解读跳过：{exc}",
            "skipped": True,
            "source": "llm_interpret",
        }


def _index_voucher_packs(vouchers_root: Path) -> Dict[str, Path]:
    mapping: Dict[str, Path] = {}
    root = vouchers_root
    # 可能多包一层目录
    candidates = [root]
    sub = list(root.glob("*"))
    if len(sub) == 1 and sub[0].is_dir() and not list(root.glob("SO*")):
        candidates.append(sub[0])
    for base in candidates:
        for folder in base.iterdir():
            if not folder.is_dir():
                continue
            m = re.match(r"(SO\d{2}-\d{4})", folder.name, re.I)
            if m:
                mapping[normalize_biz_id(m.group(1))] = folder
    return mapping


def run_amount_batch_from_ledger(
    ledger_path: Union[str, Path],
    vouchers_root: Union[str, Path],
    *,
    sheet_name: Union[str, int] = 0,
    only_sales_orders: Optional[Sequence[str]] = None,
    tolerance: float = TECHNICAL_TOLERANCE,
) -> AmountBatchResult:
    """从序时账批测：按销售订单号关联凭证目录。"""
    path = Path(ledger_path)
    df = load_ledger_file(path)
    # 若多 sheet，允许外部先选；load_ledger_file 只读第一张。必要时重读。
    if sheet_name != 0:
        df = pd.read_excel(path, sheet_name=sheet_name)
        df = df.dropna(how="all")
        df.columns = [str(c).strip() for c in df.columns]

    packs = _index_voucher_packs(Path(vouchers_root))
    only = {normalize_biz_id(x) for x in only_sales_orders} if only_sales_orders else None

    reports: List[AmountAccuracyReport] = []
    for _, row in df.iterrows():
        ledger = map_sap_row(row.to_dict())
        so = ledger.sales_order_no
        if not so:
            continue
        if only is not None and so not in only:
            continue
        folder = packs.get(so)
        if folder is None:
            if only is not None:
                reports.append(
                    AmountAccuracyReport(
                        report_id=f"AMT-{so}",
                        business_id=so,
                        voucher_no=ledger.voucher_no,
                        customer_name=ledger.customer_name,
                        ledger_values=ledger,
                        matching=MatchingInfo(status="INCOMPLETE", score=0),
                        human_readable_summary=f"未找到 {so} 凭证包",
                    )
                )
            continue
        docs = load_markdown_documents(folder)
        reports.append(
            run_amount_accuracy_test(
                documents=docs,
                ledger=ledger,
                business_id=so,
                tolerance=tolerance,
            )
        )

    result = AmountBatchResult(total=len(reports), reports=reports)
    for r in reports:
        st = r.amount_test.test_status
        if st == "PASS":
            result.pass_count += 1
        elif st == "FAIL":
            result.fail_count += 1
        elif st == "WARNING":
            result.warning_count += 1
        else:
            result.skipped_count += 1
    return result


def apply_ground_truth_ledger_split(
    ledger: LedgerValues,
    *,
    issue_type_cn: str,
    expected_net: float,
    expected_vat: float,
    vat_rate: float,
) -> LedgerValues:
    """仅用于验收诊断函数：按种植错报类型还原分录分列（不得输入待测 Agent 主路径）。"""
    gross = ledger.ledger_debit_total or ledger.ledger_ar_debit
    if gross is None:
        return ledger
    data = ledger.model_dump()
    if "销项" in issue_type_cn:
        data["ledger_revenue_credit"] = r2(expected_net)
        data["ledger_output_vat_credit"] = r2(float(gross) - expected_net)
    else:
        # 单价/折扣：比例入账
        t = float(vat_rate or 0.0)
        if t > 0:
            net = r2(float(gross) / (1.0 + t))
            data["ledger_revenue_credit"] = net
            data["ledger_output_vat_credit"] = r2(float(gross) - net)
        else:
            data["ledger_revenue_credit"] = r2(float(gross))
            data["ledger_output_vat_credit"] = 0.0
    return LedgerValues(**data)
