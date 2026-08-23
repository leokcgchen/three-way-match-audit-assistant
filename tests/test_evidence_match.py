"""证据匹配单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evidence_match import build_evidence_chain


def test_evidence_chain_pass_core_docs():
    classified = [
        {
            "file_name": "SO25-0281_HT25-0281_01_销售合同.pdf",
            "doc_type": "contract",
            "fields": {"contractNo": "HT25-0281", "documentNo": "HT25-0281"},
        },
        {
            "file_name": "SO25-0281_HT25-0281_02_销售订单.pdf",
            "doc_type": "order",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "SO25-0281_HT25-0281_04_产品验收单.pdf",
            "doc_type": "receipt",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "SO25-0281_HT25-0281_05_增值税发票.pdf",
            "doc_type": "invoice",
            "fields": {"invoiceNo": "INV0281", "documentNo": "SO25-0281"},
        },
    ]
    result = build_evidence_chain(
        classified,
        ledger_matched_biz_id="SO25-0281",
        ledger_posting_date="2025-12-10",
    )
    assert result.status == "PASS"
    assert "SO25-0281" in result.anchor_keys
    linked_roles = {n.role for n in result.nodes if n.linked}
    assert {"contract", "order", "receipt", "invoice", "ledger"} <= linked_roles
    print("test_evidence_chain_pass_core_docs: PASS", result.status)


def test_evidence_chain_fail_when_ledger_missing():
    classified = [
        {
            "file_name": "SO25-0281_订单.pdf",
            "doc_type": "order",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "SO25-0281_签收.pdf",
            "doc_type": "receipt",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "SO25-0281_发票.pdf",
            "doc_type": "invoice",
            "fields": {"documentNo": "SO25-0281"},
        },
    ]
    result = build_evidence_chain(classified)
    assert result.status == "FAIL"
    assert "序时账" in result.issue_description or "ledger" in result.missing_roles
    print("test_evidence_chain_fail_when_ledger_missing: PASS")


def test_evidence_fuzzy_biz_id_link():
    classified = [
        {
            "file_name": "a.pdf",
            "doc_type": "order",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "b.pdf",
            "doc_type": "invoice",
            "fields": {"documentNo": "SO250281"},
        },
        {
            "file_name": "c.pdf",
            "doc_type": "receipt",
            "fields": {"documentNo": "SO25-0281"},
        },
        {
            "file_name": "d.pdf",
            "doc_type": "contract",
            "fields": {"contractNo": "HT25-0281"},
        },
    ]
    result = build_evidence_chain(
        classified, ledger_matched_biz_id="SO250281", ledger_posting_date="2025-12-10"
    )
    assert any(n.role == "invoice" and n.linked for n in result.nodes)
    assert any(n.role == "order" and n.linked for n in result.nodes)
    print("test_evidence_fuzzy_biz_id_link: PASS", result.status)


def test_classify_delivery_separate_from_receipt():
    from src.workflow.classify import classify_document

    assert classify_document("SO25-0281_HT25-0281_03_销售发货单.pdf", "") == "delivery"
    assert classify_document("SO25-0281_HT25-0281_04_产品验收单.pdf", "") == "receipt"
    assert classify_document("银行流水_回款.pdf", "") == "payment"
    print("test_classify_delivery_separate_from_receipt: PASS")


def test_evidence_optional_delivery_payment_missing_still_pass():
    """缺发货/回款（可选）时仍 PASS，不挡结论。"""
    classified = [
        {
            "file_name": "SO25-0282_合同.pdf",
            "doc_type": "contract",
            "fields": {"contractNo": "KJHT25-0282"},
        },
        {
            "file_name": "SO25-0282_订单.pdf",
            "doc_type": "order",
            "fields": {"documentNo": "SO25-0282"},
        },
        {
            "file_name": "SO25-0282_验收.pdf",
            "doc_type": "receipt",
            "fields": {"documentNo": "DAP-QS25-0282"},
        },
        {
            "file_name": "SO25-0282_发票.pdf",
            "doc_type": "invoice",
            "fields": {"invoiceNo": "25322025000000002821", "documentNo": "SO25-0282"},
        },
    ]
    result = build_evidence_chain(
        classified,
        ledger_matched_biz_id="SO25-0282",
        ledger_posting_date="2025-12-10",
    )
    assert result.status == "PASS"
    assert "未上传发货单" in result.human_readable_summary
    assert "WARNING" not in result.issue_description.upper()


def test_heal_optional_attachment_warning():
    from src.evidence_match.linker import heal_optional_attachment_warning

    blob = {
        "status": "WARNING",
        "issue_description": "核心证据已按业务编号串联；未上传发货单（可选）；未上传回款资料（可选）",
        "human_readable_summary": "锚点 SO25-0282；已串联",
    }
    assert heal_optional_attachment_warning(blob) is True
    assert blob["status"] == "PASS"
    assert "可选" not in blob["issue_description"]


if __name__ == "__main__":
    test_evidence_chain_pass_core_docs()
    test_evidence_chain_fail_when_ledger_missing()
    test_evidence_fuzzy_biz_id_link()
    test_classify_delivery_separate_from_receipt()
    print("test_evidence_match: ALL PASS")
