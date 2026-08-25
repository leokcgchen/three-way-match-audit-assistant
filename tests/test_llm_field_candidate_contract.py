from __future__ import annotations

import copy

import pytest

from src.workflow.field_resolution.llm_candidates import validate_llm_field_supplement


DOCUMENT = {
    "document_id": "doc-1",
    "doc_type": "warehouse_receipt",
    "raw_text": "验收单号 YS-260102-005 关联订单号 SO-251209-7214",
}
EVIDENCE = {
    "ev-receipt": {
        "evidence_id": "ev-receipt",
        "document_id": "doc-1",
        "text": "验收单号 YS-260102-005",
    }
}


def valid_payload() -> dict:
    return {
        "schema_version": "llm_field_supplement.v2",
        "prompt_version": "field-supplement-p3-v2",
        "execution_status": "COMPLETED",
        "document_id": "doc-1",
        "candidates": [
            {
                "field_code": "documentNo",
                "field_role": "receipt_number",
                "raw_value": "YS-260102-005",
                "normalized_candidate": "YS-260102-005",
                "evidence_ids": ["ev-receipt"],
                "reason_code": "LABEL_VALUE_RELATION",
                "reason": "编号与验收单号标签在同一证据块中",
                "counterevidence_ids": [],
                "confidence": 0.96,
                "recommended_review": "SYSTEM_VALIDATE",
            }
        ],
        "missing_information": [],
        "final_professional_conclusion": None,
    }


def test_valid_llm_candidate_is_only_returned_as_candidate() -> None:
    candidates, errors = validate_llm_field_supplement(
        valid_payload(), document=DOCUMENT, evidence_by_id=EVIDENCE
    )

    assert errors == []
    assert candidates[0]["status"] == "CANDIDATE"
    assert candidates[0]["decision_owner"] == "rule_or_human"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"evidence_ids": ["missing"]}, "EVIDENCE_REFERENCE_INVALID"),
        ({"raw_value": "invented"}, "RAW_VALUE_NOT_IN_SOURCE"),
        ({"field_role": "made_up_role"}, "FIELD_ROLE_INVALID"),
        ({"recommended_review": "PASS"}, "REVIEW_ROUTE_INVALID"),
    ],
)
def test_invalid_llm_candidates_are_rejected(mutation: dict, code: str) -> None:
    payload = valid_payload()
    payload["candidates"][0].update(mutation)

    candidates, errors = validate_llm_field_supplement(
        payload, document=DOCUMENT, evidence_by_id=EVIDENCE
    )

    assert candidates == []
    assert code in errors


def test_final_conclusion_and_extra_authority_fields_are_forbidden() -> None:
    payload = valid_payload()
    payload["final_professional_conclusion"] = "PASS"
    payload["candidates"][0]["final_status"] = "PASS"

    candidates, errors = validate_llm_field_supplement(
        payload, document=DOCUMENT, evidence_by_id=EVIDENCE
    )

    assert candidates == []
    assert "FINAL_CONCLUSION_FORBIDDEN" in errors
    assert "CANDIDATE_EXTRA_FIELD" in errors


def test_cross_document_evidence_is_rejected() -> None:
    evidence = copy.deepcopy(EVIDENCE)
    evidence["ev-receipt"]["document_id"] = "doc-other"

    candidates, errors = validate_llm_field_supplement(
        valid_payload(), document=DOCUMENT, evidence_by_id=evidence
    )

    assert candidates == []
    assert "CROSS_DOCUMENT_EVIDENCE" in errors
