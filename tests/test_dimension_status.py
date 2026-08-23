"""合同条款维度状态：履约义务与整单解耦。"""

from __future__ import annotations

from src.contract_terms.dimension_status import build_dimension_statuses
from src.contract_terms.models import ContractClarityIssue
from src.contract_terms.checker import run_contract_terms_test


def test_build_dimension_po_clear_despite_payment_issue():
    issues = [
        ContractClarityIssue(
            issue_code="PAYMENT_TERMS_MISSING",
            dimension="支付条款",
            description="缺支付",
            excerpt="",
        )
    ]
    st = build_dimension_statuses(has_contract=True, issues=issues)
    assert st["履约义务"] == "CLEAR"
    assert st["支付条款"] == "AMBIGUOUS"


def test_run_contract_exposes_dimension_statuses():
    docs = [
        {
            "file_name": "c.pdf",
            "doc_type": "contract",
            "raw_text": (
                "销售合同。卖方交付货物，买方签收后转移控制权。"
                "不含安装、不含培训、不包含持续技术支持。"
                # 故意不写支付条款，触发支付维度 WARNING
            ),
            "fields": {"contractNo": "HT-1"},
        }
    ]
    result = run_contract_terms_test(docs, business_id="SO-1")
    dims = (result.extracted or {}).get("dimension_statuses") or {}
    assert dims.get("履约义务") == "CLEAR"
    po_checks = [c for c in result.checks if c.clause_id == "performance_obligation"]
    assert po_checks and po_checks[0].status == "PASS"
