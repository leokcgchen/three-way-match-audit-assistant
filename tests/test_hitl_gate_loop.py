"""HITL 死循环与顾问候选卫生：字段签名不被候选扰动；Gate4 不因误伤丢证据。"""

from __future__ import annotations

from src.models.advisory_candidates import new_advisory_candidate
from src.models.field_values import accept_all_current_fields, set_candidate
from src.workflow.job_store import JOB_STORE
from src.workflow.signatures import fields_signature


def test_set_candidate_does_not_demote_accepted():
    item = {
        "file_name": "r.pdf",
        "doc_type": "receipt",
        "fields": {"documentNo": "R-1", "acceptanceDate": "2026-01-02"},
    }
    accept_all_current_fields(item)
    before = fields_signature([item])
    set_candidate(item, "documentNo", "SO25-0281", source="llm", extractor="gap")
    assert item["_field_meta"]["documentNo"]["status"] == "ACCEPTED"
    assert item["_field_meta"]["documentNo"]["accepted_value"] == "R-1"
    assert item["fields"]["documentNo"] == "R-1"
    assert item["_field_meta"]["documentNo"]["normalized_candidate"] == "SO25-0281"
    assert fields_signature([item]) == before


def test_require_fields_soft_invalidate_keeps_evidence():
    job = JOB_STORE.create(title="soft-inv")
    jid = job["job_id"]
    doc = {
        "file_name": "r.pdf",
        "doc_type": "receipt",
        "fields": {"acceptanceDate": "2026-01-02"},
    }
    accept_all_current_fields(doc)
    JOB_STORE.update(
        jid,
        classified=[doc],
        fields_confirmed=True,
        fields_confirm_sig=fields_signature([doc]),
        matching_confirmed=True,
        matching_confirm_sig="m",
        evidence={"status": "PASS", "nodes": [{"role": "receipt"}]},
        relations=[{"relation_id": "r1", "status": "VERIFIED"}],
        amount_test={"status": "PASS"},
        three_way={"overall_status": "PASS"},
    )
    # 人为改掉 ACCEPTED，制造真实漂移
    d0 = (JOB_STORE.get(jid) or {})["classified"][0]
    d0["fields"]["acceptanceDate"] = "2099-01-01"
    d0["_field_meta"]["acceptanceDate"]["accepted_value"] = "2099-01-01"
    JOB_STORE.update(jid, classified=[d0])

    try:
        JOB_STORE.require_fields_confirmed(jid)
        assert False, "should raise"
    except ValueError as exc:
        assert "字段相对确认时已变化" in str(exc)

    fresh = JOB_STORE.get(jid)
    assert fresh["fields_confirmed"] is False
    assert fresh["matching_confirmed"] is False
    # 证据保留，Gate4 可在重确认字段后继续
    assert fresh["evidence"]["status"] == "PASS"
    assert fresh["relations"]
    # 下游测试必须作废，避免脏结论直通 Gate5
    assert fresh.get("amount_test") is None
    assert fresh.get("three_way") is None


def test_matching_fingerprint_stable_across_excerpt_noise():
    a = new_advisory_candidate(
        task_type="MATCHING_DISAMBIGUATION",
        business_id="SO25-0281",
        payload={
            "file_name": "x.pdf",
            "disposition": "ADOPT",
            "suggested_biz_id": "SO25-0281",
            "excerpt": "aaa",
        },
    )
    b = new_advisory_candidate(
        task_type="MATCHING_DISAMBIGUATION",
        business_id="SO25-0281",
        payload={
            "file_name": "x.pdf",
            "disposition": "ADOPT",
            "suggested_biz_id": "SO25-0281",
            "excerpt": "bbb completely different",
        },
    )
    assert a["candidate_id"] == b["candidate_id"]
    assert a["fingerprint"] == b["fingerprint"]
