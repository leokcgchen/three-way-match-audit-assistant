"""合同条款测试：清晰性与完整性（手册级）+ 兼容旧接口。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field

from src.contract_terms.dimension_status import (
    PERF_DIMENSION,
    build_dimension_statuses,
)
from src.contract_terms.models import ContractClarityReport
from src.contract_terms.runner import run_contract_clarity_test
from src.contract_terms.rules import evaluate_all_clarity_rules

ClauseStatus = Literal["PASS", "WARNING", "FAIL", "SKIPPED"]

CLAUSE_LABELS = {
    "transport": "运输条款",
    "control_transfer": "控制权约定",
    "performance_obligation": "履约义务",
    "settlement": "结算规则",
    "ambiguity": "条款清晰度",
    "consideration": "交易对价",
}


class ClauseCheck(BaseModel):
    clause_id: str
    clause_name: str
    status: ClauseStatus
    excerpt: str = ""
    issue: str = ""


class ContractTermsResult(BaseModel):
    status: ClauseStatus
    file_name: str = ""
    checks: List[ClauseCheck] = Field(default_factory=list)
    extracted: Dict[str, Any] = Field(default_factory=dict)
    issue_description: str = ""
    human_readable_summary: str = ""
    clarity_report: Optional[ContractClarityReport] = None


def extract_contract_clauses(
    fields: Dict[str, Any],
    raw_text: str = "",
) -> Dict[str, Any]:
    """兼容旧字段摘要输出。"""
    issues = evaluate_all_clarity_rules(raw_text or "")
    return {
        "clarityIssues": [i.model_dump() for i in issues],
        "issueCodes": [i.issue_code for i in issues],
    }


def run_contract_terms_test(
    documents: Sequence[Dict[str, Any]],
    *,
    business_id: str = "",
    voucher_no: str = "",
    customer_name: str = "",
    existing_advisory: Optional[List[Dict[str, Any]]] = None,
) -> ContractTermsResult:
    """工作流入口：条款歧义→WARNING；本测试不输出账务FAIL。"""
    report = run_contract_clarity_test(
        documents=documents,
        business_id=business_id,
        voucher_no=voucher_no,
        customer_name=customer_name,
        existing_advisory=existing_advisory,
    )
    issues = list(report.test_result.issues or [])
    extracted = dict(report.extracted or {})
    dim_st = extracted.get("dimension_statuses")
    if not isinstance(dim_st, dict):
        dim_st = build_dimension_statuses(
            has_contract=report.test_result.issue_code != "CONTRACT_MISSING",
            issues=issues,
        )
        extracted["dimension_statuses"] = dim_st
        report.extracted = extracted

    checks: List[ClauseCheck] = []
    po_st = str(dim_st.get(PERF_DIMENSION) or "").upper()
    if po_st == "CLEAR":
        checks.append(
            ClauseCheck(
                clause_id="performance_obligation",
                clause_name=PERF_DIMENSION,
                status="PASS",
                issue="履约义务维度未发现可区分性歧义",
            )
        )
    elif po_st == "AMBIGUOUS":
        checks.append(
            ClauseCheck(
                clause_id="performance_obligation",
                clause_name=PERF_DIMENSION,
                status="WARNING",
                issue="履约义务边界不清，需复核是否已适当确定可区分履约义务",
            )
        )
    elif po_st == "MISSING":
        checks.append(
            ClauseCheck(
                clause_id="performance_obligation",
                clause_name=PERF_DIMENSION,
                status="WARNING",
                issue="缺少合同，无法评价履约义务",
            )
        )

    if report.test_result.test_status == "PASS":
        checks.append(
            ClauseCheck(
                clause_id="clarity",
                clause_name="条款清晰度",
                status="PASS",
                issue="合同关键条款完整可执行",
            )
        )
    else:
        for it in issues:
            checks.append(
                ClauseCheck(
                    clause_id=it.issue_code.lower(),
                    clause_name=it.dimension,
                    status="WARNING",
                    excerpt=it.excerpt,
                    issue=it.description,
                )
            )
        if not any(c.clause_id == "clarity" for c in checks) and not issues:
            checks.append(
                ClauseCheck(
                    clause_id="clarity",
                    clause_name="条款清晰度",
                    status="WARNING",
                    issue=report.test_result.issue_description,
                )
            )

    return ContractTermsResult(
        status=report.test_result.test_status,
        file_name=(report.evidence[0].file if report.evidence else ""),
        checks=checks,
        extracted=extracted,
        issue_description=report.test_result.issue_description,
        human_readable_summary=report.human_readable_summary,
        clarity_report=report,
    )
