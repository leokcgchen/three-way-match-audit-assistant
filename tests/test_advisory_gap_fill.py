"""受控补缺 Phase1：AdvisoryCandidate + orchestrator verifier 闸门。"""

from __future__ import annotations

import pytest

from src.audit.gap_fill_orchestrator import (
    decide_in_container,
    ingest_into_container,
    ingest_verified_claims,
    queue_snapshot,
)
from src.models.advisory_candidates import (
    decide_candidate,
    default_invalidates_for_task,
    new_advisory_candidate,
    pending_proposed,
    summary_counts,
    upsert_candidates,
)


SOURCE = "销售订单号：SO25-0021；对应合同 HT25-0021。"


def test_new_candidate_defaults_advisory_only():
    c = new_advisory_candidate(
        task_type="FIELD_GAP_FILL",
        kind="fact",
        trigger_reasons=["UNRESOLVED_FIELD:documentNo"],
        business_id="SO25-0021",
        payload={"field_name": "documentNo", "normalized_candidate": "SO25-0021"},
        evidence={"excerpt": "销售订单号：SO25-0021", "source_doc": "order.pdf"},
        verify={"passed": True, "reason": "ok"},
        invalidates=["fields", "gate5"],
    )
    assert c["decision_authority"] == "LLM_ADVISORY_ONLY"
    assert c["status"] == "PROPOSED"
    assert "fields" in c["invalidates"]
    assert c["candidate_id"]


def test_upsert_preserves_verified():
    a = new_advisory_candidate(
        task_type="MATCHING_DISAMBIGUATION",
        payload={"file_name": "a.pdf", "disposition": "ADOPT"},
        evidence={"excerpt": "销售订单号：SO25-0021", "source_doc": "a.pdf"},
        fingerprint="a.pdf|ADOPT",
        business_id="SO25-0021",
    )
    store, _, after = decide_candidate([a], a["candidate_id"], "VERIFIED", actor="tester")
    assert after["status"] == "VERIFIED"

    refreshed = new_advisory_candidate(
        task_type="MATCHING_DISAMBIGUATION",
        payload={"file_name": "a.pdf", "disposition": "ADOPT", "reason": "new"},
        evidence={"excerpt": "销售订单号：SO25-0021", "source_doc": "a.pdf"},
        fingerprint="a.pdf|ADOPT",
        business_id="SO25-0021",
        status="PROPOSED",
    )
    merged = upsert_candidates(store, [refreshed], preserve_decided=True)
    assert len(merged) == 1
    assert merged[0]["status"] == "VERIFIED"
    assert merged[0]["payload"]["reason"] == "new"


def test_ingest_verifier_gate_drops_ungrounded():
    claims = [
        {
            "file_name": "order.pdf",
            "disposition": "ADOPT",
            "excerpt": "销售订单号：SO25-0021",
            "confidence": 0.9,
        },
        {
            "file_name": "order.pdf",
            "disposition": "EXCLUDE",
            "excerpt": "这段原文根本不存在XXXX",
            "confidence": 0.99,
        },
    ]
    out = ingest_verified_claims(
        [],
        task_type="MATCHING_DISAMBIGUATION",
        claims=claims,
        full_text=SOURCE,
        trigger_reasons=["MATCHING_AMBIGUITY"],
        business_id="SO25-0021",
        require_excerpt=True,
    )
    assert len(out["proposed"]) == 1
    assert len(out["dropped"]) == 1
    assert out["dropped"][0]["status"] == "DROPPED"
    assert out["dropped"][0]["verify"]["passed"] is False
    assert pending_proposed(out["store"])[0]["status"] == "PROPOSED"


def test_ingest_into_container_and_decide_returns_invalidates():
    job = {"advisory_candidates": []}
    ingest_into_container(
        job,
        task_type="AMOUNT_GAP_FILL",
        claims=[
            {
                "field_name": "quantity",
                "value": 10,
                "excerpt": "销售订单号：SO25-0021",
                "confidence": 0.95,
            }
        ],
        full_text=SOURCE,
        trigger_reasons=["PRICING_ELEMENT_MISSING"],
        require_excerpt=True,
    )
    snap = queue_snapshot(job)
    assert snap["counts"]["PROPOSED"] == 1
    cid = snap["pending"][0]["candidate_id"]
    decided = decide_in_container(job, cid, "VERIFIED", actor="auditor", reason="核对原文")
    assert decided["after"]["status"] == "VERIFIED"
    assert "amount" in decided["invalidates"]
    assert default_invalidates_for_task("AMOUNT_GAP_FILL") == decided["invalidates"]
    assert summary_counts(job["advisory_candidates"])["VERIFIED"] == 1


def test_dropped_cannot_be_promoted():
    c = new_advisory_candidate(
        task_type="FIELD_GAP_FILL",
        status="DROPPED",
        payload={"field_name": "x"},
        evidence={"excerpt": "nope"},
        verify={"passed": False, "reason": "excerpt_not_in_source"},
    )
    with pytest.raises(ValueError, match="DROPPED"):
        decide_candidate([c], c["candidate_id"], "VERIFIED")
