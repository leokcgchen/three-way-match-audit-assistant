from __future__ import annotations

from src.models.advisory_candidates import new_advisory_candidate
from src.workflow.review_events import (
    build_review_events,
    events_for_desk_row,
    review_event_summary,
)
from src.workflow.sample_desk import build_desk_chains


def _missing_invoice_job() -> dict:
    return {
        "job_id": "job-events",
        "plan": {
            "goal_ids": ["gospd01010"],
            "required_steps": ["amount_test"],
            "goals": [],
            "skipped_steps": [],
        },
        "goal_ids": ["gospd01010"],
        "sample_population": {
            "business_ids": ["SO25-0281"],
            "count": 1,
            "source": "test",
        },
        "classified": [
            {
                "file_name": "order.pdf",
                "doc_type": "order",
                "fields": {"orderNo": "SO25-0281", "quantity": 10, "totalAmount": 100},
            }
        ],
        "gospd_sample_results": {},
    }


def test_missing_invoice_becomes_blocking_event():
    events = build_review_events(_missing_invoice_job())
    event = next(row for row in events if row["event_type"] == "MISSING_DOCUMENT")
    assert event["severity"] == "BLOCKING"
    assert event["chain_id"] == "SO25-0281"
    assert event["action_step"] == "sample_desk"
    assert "发票" in event["reason"]


def test_clean_empty_job_has_no_manual_event():
    assert build_review_events({"job_id": "empty", "classified": []}) == []


def test_event_ids_are_stable_and_pending_advisory_is_review_event():
    candidate = new_advisory_candidate(
        task_type="MATCHING_DISAMBIGUATION",
        business_id="SO25-0281",
        evidence={"source_doc": "order.pdf", "excerpt": "订单号 SO25-0281"},
        payload={"suggested_biz_id": "SO25-0281"},
        fingerprint="same",
    )
    job = {
        "job_id": "stable",
        "classified": [],
        "advisory_candidates": [candidate],
    }
    first = build_review_events(job)
    second = build_review_events(job)
    assert first[0]["event_id"] == second[0]["event_id"]
    assert first[0]["event_type"] == "RELATIONSHIP_AMBIGUITY"
    assert first[0]["severity"] == "REVIEW"


def test_review_event_summary_counts_open_severity_and_missing():
    events = build_review_events(_missing_invoice_job())
    summary = review_event_summary(events)
    assert summary["open"] == 1
    assert summary["blocking"] == 1
    assert summary["missing"] == 1


def test_desk_row_exposes_event_counts_and_auto_pass_flag():
    row = build_desk_chains(_missing_invoice_job())[0]
    assert row["event_count"] == 1
    assert row["blocking_event_count"] == 1
    assert row["missing_doc_types"] == ["发票"]
    assert row["auto_passed"] is False


def test_duplicate_fail_signal_becomes_rule_conflict():
    job = {
        "job_id": "duplicate",
        "classified": [],
        "duplicates": {
            "blocks_downstream_hint": True,
            "findings": [
                {
                    "finding_id": "invoice_no:001",
                    "issue_type": "DUPLICATE_INVOICE_NO",
                    "severity": "FAIL_SIGNAL",
                    "biz_id": "SO25-0281",
                    "title": "重复发票号 001",
                    "file_names": ["a.pdf", "b.pdf"],
                    "note": "请人工确认保留哪一版。",
                }
            ],
        },
    }
    event = next(row for row in build_review_events(job) if row["event_type"] == "RULE_CONFLICT")
    assert event["severity"] == "BLOCKING"
    assert event["chain_id"] == "SO25-0281"
    assert event["source_ref"] == "duplicate:invoice_no:001"


def test_job_ocr_issue_becomes_provenance_gap():
    job = {
        "job_id": "ocr-gap",
        "classified": [],
        "ocr_issues": [
            {
                "file_name": "scan.pdf",
                "chain_id": "SO25-0281",
                "message": "第 2 页无法读取",
                "page_no": 2,
            }
        ],
    }
    event = next(row for row in build_review_events(job) if row["event_type"] == "PROVENANCE_GAP")
    assert event["evidence"]["page_no"] == 2
    assert event["action_kind"] == "REVIEW_EVIDENCE"


def test_quality_sample_selection_is_non_blocking_sample_event():
    job = {
        "job_id": "quality",
        "classified": [],
        "quality_sample_selections": [
            {
                "selection_id": "qs-1",
                "chain_id": "SO25-0281",
                "reason": "随机复核自动通过样本",
                "source_ref": "auto-pass:SO25-0281",
            }
        ],
    }
    event = next(row for row in build_review_events(job) if row["event_type"] == "QUALITY_SAMPLE")
    assert event["severity"] == "SAMPLE"
    assert event["action_kind"] == "REVIEW_SAMPLE"


def test_field_gap_candidate_becomes_low_confidence_event():
    candidate = new_advisory_candidate(
        task_type="FIELD_GAP_FILL",
        business_id="SO25-0281",
        evidence={"source_doc": "invoice.pdf", "page_no": 1},
        payload={"value": "100.00", "confidence": 0.61},
        fingerprint="field-gap",
    )
    event = build_review_events(
        {"job_id": "field-gap", "classified": [], "advisory_candidates": [candidate]}
    )[0]
    assert event["event_type"] == "LOW_CONFIDENCE"
    assert event["confidence"] == 0.61


def test_ledger_mismatch_preserves_both_values():
    job = {
        "job_id": "ledger-mismatch",
        "classified": [
            {
                "file_name": "invoice.pdf",
                "doc_type": "invoice",
                "fields": {"totalAmount": 98},
                "ledger_evaluated": True,
                "ledger_match_ok": False,
                "ledger_amount": 100,
            }
        ],
    }
    event = next(row for row in build_review_events(job) if row["event_type"] == "LEDGER_MISMATCH")
    assert event["ledger_value"] == 100
    assert event["observed_value"] == 98


def test_failed_desk_test_becomes_audit_test_event():
    event = events_for_desk_row(
        {"job_id": "failed-test"},
        {
            "chain_id": "SO25-0281",
            "reason": "test_fail",
            "label": "测试未通过 · 金额不一致",
            "diff_lines": ["账载 100，单据 98"],
        },
    )[0]
    assert event["event_type"] == "AUDIT_TEST_FAILED"
    assert event["severity"] == "BLOCKING"
