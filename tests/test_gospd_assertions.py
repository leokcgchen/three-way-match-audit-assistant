"""GOSPD01010.1 断言层口径。"""

from __future__ import annotations

from src.audit.gospd_assertions import (
    assert_step1_enforceable,
    assert_step21_revenue_amount,
    assert_step22_control_transferred,
    build_gospd_assertions,
)


def _docs_full(**extra_contract_text: str) -> list[dict]:
    raw = extra_contract_text.get("raw", "销售合同 双方盖章 商业实质 购销")
    return [
        {
            "doc_type": "contract",
            "file_name": "c.pdf",
            "raw_text": raw,
            "fields": {
                "contractNo": "HT25-0281",
                "paymentTerms": "签收后30日付款",
                "controlTransferTerms": "签收后转移控制权",
                "transportTerms": "签收确认",
            },
        },
        {
            "doc_type": "order",
            "file_name": "o.pdf",
            "fields": {"orderNo": "SO25-0281", "totalAmount": 1130, "quantity": 10},
        },
        {
            "doc_type": "receipt",
            "file_name": "r.pdf",
            "fields": {
                "documentNo": "R-1",
                "acceptanceDate": "2025-01-08",
                "quantity": 10,
            },
        },
        {
            "doc_type": "invoice",
            "file_name": "i.pdf",
            "fields": {
                "orderNo": "SO25-0281",
                "totalAmount": 1130,
                "postingDate": "2025-01-10",
            },
        },
        {
            "doc_type": "payment",
            "file_name": "p.pdf",
            "fields": {"totalAmount": 1130, "documentDate": "2025-02-01"},
        },
    ]


def test_step1_needs_approval_and_collectibility():
    docs = _docs_full(raw="销售合同 无签章字样")
    # 去掉回款
    docs = [d for d in docs if d["doc_type"] != "payment"]
    docs[0]["raw_text"] = "销售合同正文"
    r = assert_step1_enforceable(docs=docs, contract_res={"status": "PASS"})
    assert r["verdict"] is None  # 证据不足不写 Yes
    assert any("签字" in g or "盖章" in g for g in r["gaps"])
    assert any("收回" in g for g in r["gaps"])


def test_step1_yes_when_complete():
    r = assert_step1_enforceable(
        docs=_docs_full(),
        contract_res={"status": "PASS"},
    )
    assert r["verdict"] is True
    assert r["sub_checks"]["approval_commitment"] is True
    assert r["sub_checks"]["collectibility_ok"] is True


def test_step1_warning_clarity_not_auto_yes_without_evidence():
    docs = [d for d in _docs_full() if d["doc_type"] != "payment"]
    docs[0]["raw_text"] = "销售合同"
    r = assert_step1_enforceable(docs=docs, contract_res={"status": "WARNING"})
    assert r["verdict"] is not True


def test_step1_warning_never_yes_even_with_full_evidence():
    """条款 WARNING 时，即便回款/签章齐全也不得自动 Yes。"""
    r = assert_step1_enforceable(
        docs=_docs_full(),
        contract_res={
            "status": "WARNING",
            "clarity_report": {
                "test_result": {
                    "issues": [
                        {
                            "dimension": "支付条款",
                            "issue_code": "PAYMENT_PERIOD_AMBIGUOUS",
                            "description": "及时结清",
                        }
                    ]
                }
            },
        },
    )
    assert r["verdict"] is not True
    assert r["sub_checks"]["rights_payment_identifiable"] is False


def test_step21_pass_and_allocation_note():
    docs = _docs_full()
    docs[0]["fields"]["performanceObligations"] = "设备交付；安装调试"
    r = assert_step21_revenue_amount(
        docs=docs,
        amount={"status": "PASS", "human_readable_summary": "金额一致"},
    )
    assert r["verdict"] is True
    assert r["sub_checks"]["allocation_tested"] is False
    assert any("分摊" in n for n in r["notes"])


def test_step22_requires_receipt():
    docs = [d for d in _docs_full() if d["doc_type"] != "receipt"]
    r = assert_step22_control_transferred(
        docs=docs,
        three_way={
            "overall_status": "PASS",
            "match_result": {"overall_status": "PASS"},
            "cutoff_result": {"测试状态": "PASS"},
        },
    )
    assert r["verdict"] is False


def test_step22_yes_with_receipt_and_cutoff():
    r = assert_step22_control_transferred(
        docs=_docs_full(),
        three_way={
            "overall_status": "PASS",
            "match_result": {"overall_status": "PASS"},
            "cutoff_result": {"测试状态": "PASS"},
        },
    )
    assert r["verdict"] is True


def test_build_bundle_all_ok():
    job = {
        "contract_terms": {"status": "PASS"},
        "amount_test": {"status": "PASS"},
        "three_way": {
            "overall_status": "PASS",
            "match_result": {"overall_status": "PASS"},
            "cutoff_result": {"测试状态": "PASS"},
        },
    }
    out = build_gospd_assertions(docs=_docs_full(), job=job, chain_id="SO25-0281")
    assert out["step1"]["verdict"] is True
    assert out["step21"]["verdict"] is True
    assert out["step22"]["verdict"] is True
    assert out["all_ok"] is True
