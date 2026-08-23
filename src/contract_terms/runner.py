"""账驱动合同条款清晰性测试。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import pandas as pd

from src.contract_terms.models import (
    ClauseEvidence,
    ContractClarityBatchResult,
    ContractClarityIssue,
    ContractClarityReport,
    ContractTestResultBlock,
)
from src.contract_terms.rules import evaluate_all_clarity_rules, primary_issue
from src.contract_terms.dimension_status import PERF_DIMENSION, build_dimension_statuses
from src.legacy_ocr.ledger_parser import load_ledger_file, normalize_biz_id

# 四维主测；规则已命中的维不再开给 LLM，未命中维仍可补漏
CLARITY_CORE_DIMENSIONS = (
    "交易对价",
    "支付条款",
    PERF_DIMENSION,
    "运输及控制权转移",
)


def _uncovered_clarity_dimensions(issues: Sequence[ContractClarityIssue]) -> List[str]:
    covered = {
        str(it.dimension or "").strip()
        for it in issues
        if str(it.dimension or "").strip() in CLARITY_CORE_DIMENSIONS
    }
    return [d for d in CLARITY_CORE_DIMENSIONS if d not in covered]


def _strip_html_comment(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text or "", flags=re.S)


def _guess_doc_type(file_name: str) -> str:
    name = str(file_name or "")
    if "销售合同" in name or "框架协议" in name:
        return "contract"
    if "销售订单" in name:
        return "order"
    if "发货" in name:
        return "delivery"
    if "签收" in name or "验收" in name or "提单" in name:
        return "receipt"
    if "发票" in name:
        return "invoice"
    if "回款" in name or "银行" in name:
        return "payment"
    return "other"


def load_formal_documents(business_folder: Union[str, Path]) -> List[Dict[str, Any]]:
    """只加载 01_正式单据（禁止 02_内部资料）。"""
    root = Path(business_folder)
    formal = root / "01_正式单据"
    if not formal.is_dir():
        # 兼容扁平目录
        formal = root
    docs: List[Dict[str, Any]] = []
    for path in sorted(formal.glob("*.md")):
        if "内部" in path.name or path.name.startswith("00_"):
            continue
        text = _strip_html_comment(path.read_text(encoding="utf-8"))
        docs.append(
            {
                "file_name": path.name,
                "doc_type": _guess_doc_type(path.name),
                "fields": {},
                "raw_text": text,
            }
        )
    return docs


def _contract_text_and_id(documents: Sequence[Dict[str, Any]]) -> tuple[str, str, str]:
    contract = next(
        (d for d in documents if d.get("doc_type") == "contract"),
        None,
    )
    if contract is None:
        return "", "", ""
    text = str(contract.get("raw_text") or "")
    name = str(contract.get("file_name") or "")
    m = re.search(r"((?:KJ|EX|EXKJ)?HT\d{2}-\d{4})", name + "\n" + text[:2000])
    cid = m.group(1) if m else ""
    # 订单也可辅助：框架约定价格以订单为准
    order = next((d for d in documents if d.get("doc_type") == "order"), None)
    order_text = str(order.get("raw_text") or "") if order else ""
    # 字段摘要并入评估：OCR 丢段时仍可用抽取到的条款原文触发歧义规则；
    # 不得用「干净字段」盖掉正文歧义（正文优先保留）。
    field_bits: List[str] = []
    for doc in (contract, order):
        if not doc:
            continue
        fields = dict(doc.get("fields") or {})
        for k in (
            "paymentTerms",
            "settlementTerms",
            "controlTransferTerms",
            "transportTerms",
            "performanceObligations",
            "remarks",
        ):
            v = fields.get(k)
            if v:
                field_bits.append(str(v))
    # 对价清晰性：合同+订单一并评估（手册允许框架+确认订单形成清晰价格）
    blob = text + "\n" + order_text
    if field_bits:
        blob = blob + "\n" + "\n".join(field_bits)
    return blob, cid, name


def _clause_no(excerpt: str, full_text: str) -> Optional[str]:
    if not excerpt:
        return None
    # 向前找最近「第X条」
    idx = full_text.find(excerpt[:20]) if excerpt else -1
    window = full_text[max(0, idx - 400) : idx] if idx >= 0 else full_text[:800]
    m = list(re.finditer(r"第[一二三四五六七八九十百零0-9]+条", window))
    if m:
        return m[-1].group(0)
    return None


def run_contract_clarity_test(
    *,
    documents: Sequence[Dict[str, Any]],
    business_id: str = "",
    voucher_no: str = "",
    customer_name: str = "",
    posting_date: str = "",
    book_amount: Optional[float] = None,
    existing_advisory: Optional[List[Dict[str, Any]]] = None,
) -> ContractClarityReport:
    blob, contract_id, contract_file = _contract_text_and_id(documents)
    report = ContractClarityReport(
        report_id=f"CTR-{business_id or 'UNKNOWN'}",
        business_id=business_id,
        voucher_no=voucher_no,
        contract_id=contract_id,
        customer_name=customer_name,
        ledger_check={
            "posting_date": posting_date,
            "book_amount": book_amount,
            # 本测只评条款清晰性，不替代截止/金额勾稽；禁止硬编码一致=真
            "posting_date_matches_control_date": None,
            "book_amount_matches_document_amount": None,
            "ledger_consistency_scope": "NOT_TESTED",
            "ledger_consistency_note": (
                "账务日期/金额一致性由截止与金额测试判定，合同条款测试不预填为通过"
            ),
        },
    )
    if not blob.strip():
        report.test_result = ContractTestResultBlock(
            test_status="WARNING",
            risk_level="证据不足-人工复核",
            test_dimension="综合",
            issue_code="CONTRACT_MISSING",
            issue_description="未取得销售合同正文，无法评价条款清晰性",
            accounting_misstatement_detected=False,
            manual_review_required=True,
        )
        report.human_readable_summary = "合同条款测试 WARNING：未取得合同"
        report.extracted = {
            "contract_id": contract_id,
            "issue_codes": ["CONTRACT_MISSING"],
            "dimensions": ["综合"],
            "dimension_statuses": build_dimension_statuses(
                has_contract=False, issues=[]
            ),
            "issue_sources": {"rule": ["CONTRACT_MISSING"], "llm": []},
        }
        report.workpaper_fill = {
            "会计分录编号": voucher_no,
            "客户名称": customer_name,
            "合同索引号": contract_id,
            "销售订单编号": business_id,
            "记录主要合同条款": "合同缺失",
            "审计结论": "需补充合同后复核",
            "系统状态": "WARNING",
        }
        return report

    issues = evaluate_all_clarity_rules(blob)
    for it in issues:
        # 规则引擎产出统一标 rule（模型默认已是 rule，显式写入防序列化丢失）
        it.source = "rule"
    llm_notes: List[str] = []
    advisory_store: List[Dict[str, Any]] = list(existing_advisory or [])
    # 按维补漏：规则已命中的维度跳过；未覆盖维度仍可调 LLM（避免「命中支付却漏履约」）
    uncovered = _uncovered_clarity_dimensions(issues)
    if uncovered:
        try:
            from src.llm.batch_assist import llm_supplement_clarity_issues

            extra, llm_notes = llm_supplement_clarity_issues(
                blob,
                [i.issue_code for i in issues],
                allowed_dimensions=uncovered,
            )
            llm_claims: List[Dict[str, Any]] = []
            for row in extra:
                issues.append(
                    ContractClarityIssue(
                        issue_code=str(row["issue_code"]),
                        dimension=row.get("dimension") or "综合",  # type: ignore[arg-type]
                        description=str(row.get("description") or ""),
                        excerpt=str(row.get("excerpt") or ""),
                        source="llm",
                        confidence=(
                            float(row["confidence"])
                            if row.get("confidence") is not None
                            else None
                        ),
                    )
                )
                llm_claims.append(
                    {
                        "issue_code": row.get("issue_code"),
                        "description": row.get("description"),
                        "excerpt": row.get("excerpt"),
                        "confidence": row.get("confidence"),
                        "file_name": contract_file,
                        "kind": "issue",
                    }
                )
            if llm_claims:
                try:
                    from src.audit.gap_fill_orchestrator import ingest_verified_claims

                    ingest = ingest_verified_claims(
                        advisory_store,
                        task_type="CONTRACT_CLARITY_REVIEW",
                        claims=llm_claims,
                        full_text=blob,
                        trigger_reasons=["DIMENSION_GAP_CLARITY"],
                        business_id=business_id or contract_id,
                        kind="issue",
                        require_excerpt=True,
                        min_confidence=0.85,
                    )
                    advisory_store = ingest["store"]
                    llm_notes.append(
                        f"顾问候选入库 proposed={len(ingest.get('proposed') or [])} "
                        f"dropped={len(ingest.get('dropped') or [])}"
                    )
                except Exception as exc:  # noqa: BLE001
                    llm_notes.append(f"顾问候选入库跳过：{exc}")
        except Exception as exc:  # noqa: BLE001
            llm_notes.append(f"LLM 条款补漏跳过：{exc}")
    else:
        llm_notes.append("四维均已被规则覆盖，跳过 LLM 条款补漏")
    rule_dims = sorted(
        {
            str(i.dimension)
            for i in issues
            if i.source == "rule" and str(i.dimension) in CLARITY_CORE_DIMENSIONS
        }
    )
    llm_notes.append(
        "规则覆盖维=" + (",".join(rule_dims) if rule_dims else "无")
        + "；待补漏维="
        + (",".join(uncovered) if uncovered else "无")
    )

    # 去重（规则 + LLM）
    seen_codes = set()
    deduped: List[ContractClarityIssue] = []
    for it in issues:
        if it.issue_code in seen_codes:
            continue
        seen_codes.add(it.issue_code)
        deduped.append(it)
    issues = deduped

    code, dim, desc = primary_issue(issues)

    if not issues:
        report.test_result = ContractTestResultBlock(
            test_status="PASS",
            risk_level="无异常",
            test_dimension="无",
            issue_code=None,
            issue_description=desc,
            accounting_misstatement_detected=False,
            manual_review_required=False,
            issues=[],
        )
        summary_clause = "关键条款完整可执行"
    else:
        report.test_result = ContractTestResultBlock(
            test_status="WARNING",
            risk_level="条款不确定-人工复核",
            test_dimension=dim,
            issue_code=code,
            issue_description=desc,
            accounting_misstatement_detected=False,
            manual_review_required=True,
            issues=issues,
        )
        summary_clause = desc

    evidence: List[ClauseEvidence] = []
    for it in issues[:3]:
        evidence.append(
            ClauseEvidence(
                file=contract_file,
                clause=_clause_no(it.excerpt, blob),
                text_excerpt=it.excerpt,
            )
        )
    if not evidence and contract_file:
        evidence.append(ClauseEvidence(file=contract_file, text_excerpt=_clip_head(blob)))
    report.evidence = evidence
    report.extracted = {
        "contract_id": contract_id,
        "issue_codes": [i.issue_code for i in issues],
        "dimensions": sorted({i.dimension for i in issues}),
        "issue_sources": {
            "rule": [i.issue_code for i in issues if i.source == "rule"],
            "llm": [i.issue_code for i in issues if i.source == "llm"],
        },
        "dimension_statuses": build_dimension_statuses(
            has_contract=bool(blob.strip()),
            issues=issues,
        ),
        "llm_assist_notes": llm_notes,
        "advisory_candidates": advisory_store,
        # AI 解读改为 UI 按需生成，避免批测同步等待千帆
        "llm_interpretation": {
            "skipped": True,
            "explanation": "AI 解读未自动执行（可在结果区按需生成）",
            "source": "deferred",
        },
    }
    report.workpaper_fill = {
        "会计分录编号": voucher_no,
        "客户名称": customer_name,
        "入账日期": posting_date,
        "入账金额": book_amount,
        "合同索引号": contract_id,
        "销售订单编号": business_id,
        "记录主要合同条款": summary_clause,
        "系统状态": report.test_result.test_status,
        "差异或风险说明": report.test_result.issue_description,
        "证据链索引": "/".join(
            str(d.get("file_name") or "") for d in documents if d.get("file_name")
        )[:300],
        "审计结论": (
            "合同条款清晰完整"
            if report.test_result.test_status == "PASS"
            else "合同条款存在不确定性，需取得补充依据并人工复核；本测试未认定账务错报"
        ),
    }
    report.human_readable_summary = (
        f"合同条款测试 {report.test_result.test_status} {business_id}: "
        f"{report.test_result.issue_code or 'OK'} / {report.test_result.issue_description}"
    )
    return report


def _clip_head(text: str, n: int = 120) -> str:
    t = re.sub(r"\s+", " ", text.strip())
    return t[:n] + ("…" if len(t) > n else "")


def _index_packs(vouchers_root: Path) -> Dict[str, Path]:
    mapping: Dict[str, Path] = {}
    candidates = [vouchers_root]
    children = [p for p in vouchers_root.iterdir() if p.is_dir()]
    if len(children) == 1 and not any(p.name.startswith("SO") for p in children):
        candidates.append(children[0])
    for base in candidates:
        for folder in base.iterdir():
            if not folder.is_dir():
                continue
            m = re.match(r"(SO\d{2}-\d{4})", folder.name, re.I)
            if m:
                mapping[normalize_biz_id(m.group(1))] = folder
    return mapping


def map_ledger_row(row: Dict[str, Any]) -> Dict[str, Any]:
    posting = row.get("过账日期") or row.get("入账日期") or ""
    if hasattr(posting, "strftime"):
        posting = posting.strftime("%Y-%m-%d")
    else:
        posting = str(posting)[:10]
    debit = row.get("借方金额") if row.get("借方金额") is not None else row.get("借方发生额")
    try:
        amount = float(debit) if debit is not None and str(debit) not in {"", "nan"} else None
    except (TypeError, ValueError):
        amount = None
    return {
        "voucher_no": str(row.get("凭证号") or row.get("凭证字号") or ""),
        "posting_date": posting,
        "customer_name": str(row.get("客户名称") or row.get("往来单位名称") or ""),
        "sales_order_no": normalize_biz_id(row.get("销售订单号") or row.get("订单编号") or ""),
        "book_amount": amount,
    }


def run_contract_clarity_batch(
    ledger_path: Union[str, Path],
    vouchers_root: Union[str, Path],
    *,
    sheet_name: Union[str, int] = 0,
    only_sales_orders: Optional[Sequence[str]] = None,
) -> ContractClarityBatchResult:
    path = Path(ledger_path)
    if sheet_name != 0:
        df = pd.read_excel(path, sheet_name=sheet_name)
        df = df.dropna(how="all")
        df.columns = [str(c).strip() for c in df.columns]
    else:
        df = load_ledger_file(path)

    packs = _index_packs(Path(vouchers_root))
    only = {normalize_biz_id(x) for x in only_sales_orders} if only_sales_orders else None
    reports: List[ContractClarityReport] = []

    for _, row in df.iterrows():
        meta = map_ledger_row(row.to_dict())
        so = meta["sales_order_no"]
        if not so:
            continue
        if only is not None and so not in only:
            continue
        folder = packs.get(so)
        if folder is None:
            reports.append(
                ContractClarityReport(
                    report_id=f"CTR-{so}",
                    business_id=so,
                    voucher_no=meta["voucher_no"],
                    customer_name=meta["customer_name"],
                    test_result=ContractTestResultBlock(
                        test_status="WARNING",
                        risk_level="证据不足-人工复核",
                        issue_code="EVIDENCE_PACK_MISSING",
                        issue_description=f"未找到 {so} 正式单据包",
                        manual_review_required=True,
                    ),
                    human_readable_summary=f"未找到 {so} 凭证包",
                )
            )
            continue
        docs = load_formal_documents(folder)
        reports.append(
            run_contract_clarity_test(
                documents=docs,
                business_id=so,
                voucher_no=meta["voucher_no"],
                customer_name=meta["customer_name"],
                posting_date=meta["posting_date"],
                book_amount=meta["book_amount"],
            )
        )

    result = ContractClarityBatchResult(total=len(reports), reports=reports)
    for r in reports:
        st = r.test_result.test_status
        if st == "PASS":
            result.pass_count += 1
        elif st == "WARNING":
            result.warning_count += 1
        elif st == "FAIL":
            result.fail_count += 1
        else:
            result.skipped_count += 1
    return result
