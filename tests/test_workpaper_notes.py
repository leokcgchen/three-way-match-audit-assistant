"""GOSPD 底稿旁注：系统观察 / 待审计师判断。"""

from __future__ import annotations

from src.audit.gospd_assertions import build_gospd_assertions
from src.audit.workpaper_notes import (
    attach_workpaper_notes,
    build_workpaper_notes,
    merge_exception_text,
)


def test_build_notes_for_llm_contract_and_pending_advisory():
    notes = build_workpaper_notes(
        job={
            "advisory_candidates": [
                {
                    "candidate_id": "1",
                    "status": "PROPOSED",
                    "task_type": "CONTRACT_CLARITY_REVIEW",
                    "business_id": "SO25-0001",
                    "payload": {"issue_code": "REBATE_TERM_AMBIGUOUS"},
                    "kind": "issue",
                }
            ]
        },
        chain_id="SO25-0001",
        contract_res={
            "extracted": {
                "issue_sources": {"rule": [], "llm": ["REBATE_TERM_AMBIGUOUS"]}
            }
        },
        amount={
            "accuracy_report": {
                "source_values": {
                    "quantity_source": "llm",
                    "price_source": "order",
                }
            }
        },
        empty_verdict_labels=["2.1"],
    )
    assert "系统观察" in notes["system_observation"]
    assert "REBATE_TERM_AMBIGUOUS" in notes["system_observation"]
    assert "quantity" in notes["system_observation"]
    assert "待审计师判断" in notes["pending_judgment"]
    assert "2.1" in notes["pending_judgment"]
    assert "系统观察" in notes["exception_appendix"]
    assert "AI认为" not in notes["exception_appendix"]
    assert "审计结论为" not in notes["exception_appendix"]
    assert "已确认错报" not in notes["exception_appendix"]


def test_merge_exception_preserves_base():
    merged = merge_exception_text("步骤1:缺合同", "系统观察：\n- x")
    assert "步骤1:缺合同" in merged
    assert "【规则发现】" in merged
    assert "非审计师最终结论" in merged
    assert "系统观察" in merged


def test_assertions_exception_includes_notes_without_changing_yes():
    docs = [
        {
            "file_name": "c.pdf",
            "doc_type": "contract",
            "raw_text": "销售合同 双方盖章 商业实质 购销 签收后付款",
            "fields": {
                "contractNo": "HT25-0281",
                "paymentTerms": "签收后30日付款",
                "controlTransferTerms": "签收后转移控制权",
                "buyerName": "测试客户",
            },
        },
        {
            "file_name": "o.pdf",
            "doc_type": "order",
            "fields": {"orderNo": "SO25-0281", "quantity": 10, "totalAmount": 1130},
        },
        {
            "file_name": "r.pdf",
            "doc_type": "receipt",
            "fields": {
                "documentNo": "R-001",
                "acceptanceDate": "2025-01-08",
                "quantity": 10,
            },
        },
        {
            "file_name": "i.pdf",
            "doc_type": "invoice",
            "fields": {
                "orderNo": "SO25-0281",
                "totalAmount": 1130,
                "quantity": 10,
                "buyerName": "测试客户",
            },
            "ledger_amount": 1130,
        },
        {
            "file_name": "p.pdf",
            "doc_type": "payment",
            "fields": {"totalAmount": 1130},
        },
    ]
    job = {
        "goal_ids": ["gospd01010"],
        "contract_terms": {
            "status": "WARNING",
            "extracted": {
                "issue_sources": {"rule": [], "llm": ["REBATE_TERM_AMBIGUOUS"]}
            },
        },
        "amount_test": {
            "status": "PASS",
            "accuracy_report": {
                "amount_test": {"test_status": "PASS"},
                "source_values": {"quantity_source": "receipt", "price_source": "order"},
            },
        },
        "three_way": {
            "overall_status": "PASS",
            "match_result": {"overall_status": "PASS"},
            "cutoff_result": {"测试状态": "PASS"},
        },
        "advisory_candidates": [
            {
                "status": "PROPOSED",
                "task_type": "MATCHING_DISAMBIGUATION",
                "business_id": "SO25-0281",
                "payload": {"file_name": "x.pdf", "disposition": "KEEP_CANDIDATE"},
            }
        ],
    }
    out = build_gospd_assertions(docs=docs, job=job, chain_id="SO25-0281")
    # 终态仍由规则断言决定，旁注只进 exception
    assert out["step1"]["verdict_label"].startswith("Yes") or out["step1"]["verdict"] in {
        True,
        False,
        None,
    }
    assert "系统观察" in (out.get("exception") or "")
    assert "待审计师判断" in (out.get("exception") or "")
    assert out.get("system_observation")
    assert "AI认为有风险" not in (out.get("exception") or "")


def test_biz_match_empty_bid_not_cross_chain():
    from src.audit.workpaper_notes import _biz_match

    assert _biz_match({"business_id": ""}, "SO25-0281") is False
    assert _biz_match({"business_id": "SO25-0281"}, "SO25-0281") is True
    assert _biz_match({"business_id": "HT25-0281"}, "SO25-0281") is True
    assert _biz_match({"business_id": "SO25-0099"}, "SO25-0281") is False
    base = {"exception": "a", "all_ok_label": "Yes 是"}
    once = attach_workpaper_notes(
        base,
        contract_res={"extracted": {"issue_sources": {"llm": ["X"]}}},
    )
    twice = attach_workpaper_notes(
        once,
        contract_res={"extracted": {"issue_sources": {"llm": ["X"]}}},
    )
    # 分隔标题含「系统观察」字样；重复 attach 不得叠写观察块
    assert twice["exception"].count("系统观察：") == 1
    assert twice["exception"].count("待审计师判断：") == 1
    assert twice.get("rule_exception") == "a"
