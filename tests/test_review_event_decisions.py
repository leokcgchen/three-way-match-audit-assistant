from __future__ import annotations

import pytest

from src.models.advisory_candidates import new_advisory_candidate
from src.workflow.job_store import JOB_STORE
from src.workflow.review_event_decisions import apply_review_decision
from src.workflow.review_events import build_review_events


def _advisory_job() -> tuple[str, dict]:
    job = JOB_STORE.create(title="event-decision")
    candidate = new_advisory_candidate(
        task_type="FIELD_GAP_FILL",
        business_id="SO25-0281",
        evidence={"source_doc": "invoice.pdf", "page": 1, "excerpt": "金额 100"},
        payload={"field_name": "totalAmount", "value": 100, "confidence": 0.82},
        invalidates=["fields"],
        fingerprint="decision-candidate",
    )
    JOB_STORE.update(job["job_id"], advisory_candidates=[candidate])
    event = build_review_events(JOB_STORE.get(job["job_id"]) or {})[0]
    return job["job_id"], event


def test_accept_ai_resolves_advisory_and_persists_decision_record():
    job_id, event = _advisory_job()
    result = apply_review_decision(
        job_id,
        event["event_id"],
        {"decision": "ACCEPT_AI", "operator": "auditor-a"},
    )
    assert result["decision"]["state"] == "RESOLVED"
    saved = JOB_STORE.get(job_id) or {}
    candidate = saved["advisory_candidates"][0]
    assert candidate["status"] == "VERIFIED"
    record = saved["review_event_decisions"][event["event_id"]]
    assert record["operator"] == "auditor-a"
    assert record["event"]["source_ref"] == event["source_ref"]


def test_missing_document_cannot_be_closed_by_decision_shortcut():
    job = JOB_STORE.create(title="missing-event")
    JOB_STORE.update(
        job["job_id"],
        goal_ids=["gospd01010"],
        plan={"goal_ids": ["gospd01010"], "required_steps": [], "goals": []},
        sample_population={"business_ids": ["SO25-0281"], "count": 1},
    )
    event = build_review_events(JOB_STORE.get(job["job_id"]) or {})[0]
    with pytest.raises(ValueError, match="上传"):
        apply_review_decision(
            job["job_id"], event["event_id"], {"decision": "DOCUMENT_ISSUE", "reason": "无"}
        )


def test_manual_value_updates_source_field_and_invalidates_only_declared_targets():
    job = JOB_STORE.create(title="manual-value")
    candidate = new_advisory_candidate(
        task_type="FIELD_GAP_FILL",
        business_id="SO25-0281",
        evidence={"source_doc": "invoice.pdf", "page": 1},
        payload={"field_name": "totalAmount", "value": 100},
        invalidates=["amount"],
        fingerprint="manual-value",
    )
    JOB_STORE.update(
        job["job_id"],
        classified=[
            {
                "file_name": "invoice.pdf",
                "doc_type": "invoice",
                "fields": {"totalAmount": 98},
            }
        ],
        advisory_candidates=[candidate],
    )
    event = next(
        row
        for row in build_review_events(JOB_STORE.get(job["job_id"]) or {})
        if row["source_ref"].startswith("advisory:")
    )
    result = apply_review_decision(
        job["job_id"],
        event["event_id"],
        {"decision": "MANUAL_VALUE", "value": 101, "reason": "核对原件"},
    )
    saved = JOB_STORE.get(job["job_id"]) or {}
    assert saved["classified"][0]["fields"]["totalAmount"] == 101
    assert result["decision"]["after"] == 101


def test_manual_value_requires_a_value():
    job_id, event = _advisory_job()
    with pytest.raises(ValueError, match="人工值"):
        apply_review_decision(job_id, event["event_id"], {"decision": "MANUAL_VALUE"})


def test_resolved_unchanged_fact_is_not_projected_as_open_again():
    job = JOB_STORE.create(title="resolved-ledger")
    JOB_STORE.update(
        job["job_id"],
        classified=[
            {
                "file_name": "invoice.pdf",
                "doc_type": "invoice",
                "fields": {"totalAmount": 98},
                "ledger_evaluated": True,
                "ledger_match_ok": False,
                "ledger_amount": 100,
            }
        ],
    )
    event = next(
        row
        for row in build_review_events(JOB_STORE.get(job["job_id"]) or {})
        if row["event_type"] == "LEDGER_MISMATCH"
    )
    apply_review_decision(
        job["job_id"], event["event_id"], {"decision": "ACCEPT_AI"}
    )
    assert build_review_events(JOB_STORE.get(job["job_id"]) or {}) == []


def _quality_sample_job() -> tuple[str, dict]:
    job = JOB_STORE.create(title="quality-decision")
    JOB_STORE.update(
        job["job_id"],
        quality_sample_selections=[
            {
                "selection_id": "qs-1",
                "chain_id": "SO25-0281",
                "reason": "随机复核自动通过样本",
                "source_ref": "quality_sample:v2:SO25-0281",
            }
        ],
    )
    event = next(
        row
        for row in build_review_events(JOB_STORE.get(job["job_id"]) or {})
        if row["event_type"] == "QUALITY_SAMPLE"
    )
    return job["job_id"], event


def test_quality_sample_correct_closes_without_changing_audit_result():
    job_id, event = _quality_sample_job()
    result = apply_review_decision(
        job_id, event["event_id"], {"decision": "CORRECT", "operator": "reviewer"}
    )
    assert result["decision"]["decision"] == "CORRECT"
    saved = JOB_STORE.get(job_id) or {}
    assert saved.get("quality_findings") in (None, [])
    assert build_review_events(saved) == []


def test_quality_sample_false_negative_creates_real_blocking_event():
    job_id, event = _quality_sample_job()
    apply_review_decision(
        job_id,
        event["event_id"],
        {"decision": "FALSE_NEGATIVE", "reason": "验收日期与订单不一致"},
    )
    events = build_review_events(JOB_STORE.get(job_id) or {})
    finding = next(row for row in events if row["event_type"] == "QUALITY_FALSE_NEGATIVE")
    assert finding["severity"] == "BLOCKING"
    assert "验收日期" in finding["reason"]


@pytest.mark.parametrize("decision", ["OVERRIDE", "AUDIT_FAIL", "DOCUMENT_ISSUE"])
def test_consequential_decisions_require_reason(decision: str):
    job_id, event = _advisory_job()
    with pytest.raises(ValueError, match="理由"):
        apply_review_decision(job_id, event["event_id"], {"decision": decision, "reason": ""})
