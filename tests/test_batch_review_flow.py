"""一键审阅 / 顾问门禁口径回归。"""

from __future__ import annotations

from src.audit.workpaper_notes import (
    blocking_advisory_for_export,
    digest_field_advisories_on_confirm,
)
from src.models.advisory_candidates import new_advisory_candidate
from src.workflow.export_readiness import build_export_readiness
from src.workflow.job_store import JOB_STORE
from src.workflow.signatures import fields_signature


def test_field_confirm_digests_field_gap_fill():
    job = JOB_STORE.create(title="digest-adv")
    jid = job["job_id"]
    docs = [
        {
            "file_name": "SO1_inv.pdf",
            "doc_type": "invoice",
            "fields": {"orderNo": "SO1", "totalAmount": "1"},
        }
    ]
    cand = new_advisory_candidate(
        task_type="FIELD_GAP_FILL",
        business_id="SO1",
        payload={"field_name": "buyerName", "normalized_candidate": "X"},
        evidence={"source_doc": "SO1_inv.pdf"},
    )
    other = new_advisory_candidate(
        task_type="AMOUNT_GAP_FILL",
        business_id="SO1",
        payload={"field_name": "quantity", "normalized_candidate": 2},
        evidence={"source_doc": "SO1_inv.pdf"},
    )
    JOB_STORE.update(
        jid,
        goal_ids=["gospd01030"],
        plan={
            "goal_ids": ["gospd01030"],
            "required_steps": ["field_confirm", "conclusion_gate5"],
            "goals": [],
            "skipped_steps": [],
        },
        classified=docs,
        active_chain_id="SO1",
        advisory_candidates=[cand, other],
    )
    out = JOB_STORE.confirm_fields(jid)
    store = out.get("advisory_candidates") or []
    by_id = {c["candidate_id"]: c for c in store}
    assert by_id[cand["candidate_id"]]["status"] == "DROPPED"
    assert by_id[other["candidate_id"]]["status"] == "PROPOSED"
    assert blocking_advisory_for_export(out) == []


def test_export_readiness_advisory_stage_not_blocking():
    cand = new_advisory_candidate(
        task_type="AMOUNT_GAP_FILL",
        payload={"field_name": "quantity"},
        invalidates=["amount"],
    )
    job = {
        "classified": [{"file_name": "a.pdf", "doc_type": "invoice", "fields": {}}],
        "goal_ids": [],
        "plan": {"required_steps": [], "goals": [], "skipped_steps": []},
        "advisory_candidates": [cand],
        "conclusion_confirmed": True,
    }
    ready = build_export_readiness(job)
    adv = next(s for s in ready["stages"] if s["id"] == "advisory")
    assert adv["status"] == "DONE"
    assert adv["blocking"] is False


def test_batch_confirm_matching_accepts_proposed_relations():
    """一键串单须顺带 PROPOSED→VERIFIED，与本笔勾稽主路径一致。"""
    from src.models.relation_candidates import new_relation, pending_proposed
    from src.workflow.batch_review import batch_confirm_matching

    job = JOB_STORE.create(title="batch-gate4-accept")
    jid = job["job_id"]
    docs = [
        {
            "file_name": "SO25-0296_inv.pdf",
            "doc_type": "invoice",
            "fields": {"orderNo": "SO25-0296", "totalAmount": "1"},
        },
        {
            "file_name": "SO25-0296_po.pdf",
            "doc_type": "sales_order",
            "fields": {"orderNo": "SO25-0296", "totalAmount": "1"},
        },
    ]
    rels = [
        new_relation(
            from_id="SO25-0296_inv.pdf",
            to_id=f"SO25-0296_po_{i}.pdf",
            rel_type="SUPPORTS",
            status="PROPOSED",
            shared_keys=["SO25-0296"],
        )
        for i in range(3)
    ]
    evidence = {"matched": True, "links": []}
    JOB_STORE.update(
        jid,
        goal_ids=["gospd01030"],
        plan={
            "goal_ids": ["gospd01030"],
            "required_steps": [
                "field_confirm",
                "evidence_match",
                "relations_gate4",
                "conclusion_gate5",
            ],
            "goals": [],
            "skipped_steps": [],
        },
        classified=docs,
        active_chain_id="SO25-0296",
        fields_confirmed=True,
        evidence=evidence,
        relations=rels,
        duplicates={"ran": True, "findings": [], "summary": {"total": 0}},
        gospd_sample_results={
            "SO25-0296": {
                "fields_confirmed": True,
                "evidence": evidence,
                "relations": rels,
                "duplicates": {"ran": True, "findings": [], "summary": {"total": 0}},
                "matching_confirmed": False,
            }
        },
    )
    assert pending_proposed(JOB_STORE.get(jid).get("relations") or [])
    out = batch_confirm_matching(jid)
    assert out["blocked"] == [], out
    assert "SO25-0296" in out["confirmed"]
    job2 = out["job"]
    assert job2.get("matching_confirmed") is True
    assert pending_proposed(job2.get("relations") or []) == []
    sample = (job2.get("gospd_sample_results") or {}).get("SO25-0296") or {}
    assert sample.get("matching_confirmed") is True
    assert pending_proposed(sample.get("relations") or []) == []


def test_batch_review_auto_confirms_complete_fields_then_runs():
    """字段已齐但未点确认时，一键审阅须先自动确认，不能跳过还显示「测试在自动跑」。"""
    from src.workflow.batch_review import run_batch_review

    order_no = "SO25-0281"
    docs = [
        {
            "file_name": f"{order_no}_order.pdf",
            "doc_type": "order",
            "fields": {
                "orderNo": order_no,
                "contractNo": "HT-1",
                "buyerName": "甲公司",
                "quantity": "1",
                "totalAmount": "113",
            },
        },
        {
            "file_name": f"{order_no}_receipt.pdf",
            "doc_type": "receipt",
            "fields": {"orderNo": order_no, "acceptanceDate": "2025-12-30", "quantity": "1"},
        },
        {
            "file_name": f"{order_no}.pdf",
            "doc_type": "invoice",
            "fields": {
                "orderNo": order_no,
                "invoiceNo": "INV-1",
                "buyerName": "甲公司",
                "totalAmount": "113",
                "amount": "100",
                "taxAmount": "13",
                "quantity": "1",
                "documentDate": "2025-12-28",
            },
        },
    ]
    job = JOB_STORE.create(title="auto-fields-then-test")
    jid = job["job_id"]
    JOB_STORE.update(
        jid,
        goal_ids=["gospd01030"],
        plan={
            "goal_ids": ["gospd01030"],
            "required_steps": [
                "field_confirm",
                "relations_gate4",
                "three_way_cutoff",
                "conclusion_gate5",
            ],
            "goals": [],
            "skipped_steps": [],
        },
        classified=docs,
        active_chain_id=order_no,
        sample_population={"business_ids": [order_no]},
        gospd_sample_results={order_no: {"fields_confirmed": False}},
        period_end="2025-12-31",
    )
    out = run_batch_review(jid)
    skip_reasons = [str(s.get("reason") or "") for s in out.get("skipped") or []]
    assert not any("字段未确认" in r for r in skip_reasons), out
    sample = (out["job"].get("gospd_sample_results") or {}).get(order_no) or {}
    assert sample.get("fields_confirmed") is True
    assert sample.get("three_way") or any(
        "三单" in a for r in (out.get("ran") or []) for a in (r.get("actions") or [])
    )
