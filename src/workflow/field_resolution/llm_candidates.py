"""Validate advisory LLM field supplements before they enter field resolution.

The model may propose source-backed candidates.  It never grants verification or
an audit conclusion; deterministic rules or a reviewer own those decisions.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Tuple

SCHEMA_VERSION = "llm_field_supplement.v2"
PROMPT_VERSION = "field-supplement-p3-v2"

_TOP_LEVEL_FIELDS = {
    "schema_version",
    "prompt_version",
    "execution_status",
    "document_id",
    "candidates",
    "missing_information",
    "final_professional_conclusion",
}
_CANDIDATE_FIELDS = {
    "field_code",
    "field_role",
    "raw_value",
    "normalized_candidate",
    "evidence_ids",
    "reason_code",
    "reason",
    "counterevidence_ids",
    "confidence",
    "recommended_review",
}
_EXECUTION_STATUSES = {"COMPLETED", "NEEDS_REVIEW", "BLOCKED", "ERROR"}
_FIELD_ROLES = {
    "receipt_number",
    "invoice_number",
    "order_number",
    "contract_number",
    "business_number",
    "document_date",
    "acceptance_date",
    "delivery_date",
    "buyer",
    "seller",
    "goods_name",
    "model",
    "quantity",
    "unit_price",
    "net_amount",
    "tax_amount",
    "gross_amount",
    "tax_rate",
    "transport_term",
    "control_transfer_term",
    "other_source_fact",
}
_REASON_CODES = {
    "LABEL_VALUE_RELATION",
    "TABLE_HEADER_COLUMN_RELATION",
    "REPEATED_ENTITY_RELATION",
    "SEMANTIC_ROLE_RELATION",
    "SPATIAL_RELATION",
    "SOURCE_FACT_AMBIGUOUS",
}
_REVIEW_ROUTES = {"SYSTEM_VALIDATE", "HUMAN_REVIEW"}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def validate_llm_field_supplement(
    payload: Mapping[str, Any],
    *,
    document: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Return validated advisory candidates and stable rejection codes."""

    errors: List[str] = []
    if not isinstance(payload, Mapping):
        return [], ["PAYLOAD_NOT_OBJECT"]
    if set(payload) - _TOP_LEVEL_FIELDS:
        errors.append("PAYLOAD_EXTRA_FIELD")
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("SCHEMA_VERSION_INVALID")
    if payload.get("prompt_version") != PROMPT_VERSION:
        errors.append("PROMPT_VERSION_INVALID")
    if payload.get("execution_status") not in _EXECUTION_STATUSES:
        errors.append("EXECUTION_STATUS_INVALID")
    document_id = str(document.get("document_id") or document.get("id") or "")
    if str(payload.get("document_id") or "") != document_id:
        errors.append("DOCUMENT_ID_MISMATCH")
    if payload.get("final_professional_conclusion") not in (None, ""):
        errors.append("FINAL_CONCLUSION_FORBIDDEN")

    raw_text = str(
        document.get("raw_text")
        or document.get("ocr_text")
        or document.get("rawText")
        or ""
    )
    accepted: List[Dict[str, Any]] = []
    candidate_values = payload.get("candidates")
    if not isinstance(candidate_values, list):
        errors.append("CANDIDATES_NOT_LIST")
        return [], sorted(set(errors))

    for raw_candidate in candidate_values:
        local_errors: List[str] = []
        if not isinstance(raw_candidate, Mapping):
            errors.append("CANDIDATE_NOT_OBJECT")
            continue
        if set(raw_candidate) - _CANDIDATE_FIELDS:
            local_errors.append("CANDIDATE_EXTRA_FIELD")
        if raw_candidate.get("field_role") not in _FIELD_ROLES:
            local_errors.append("FIELD_ROLE_INVALID")
        if raw_candidate.get("reason_code") not in _REASON_CODES:
            local_errors.append("REASON_CODE_INVALID")
        if raw_candidate.get("recommended_review") not in _REVIEW_ROUTES:
            local_errors.append("REVIEW_ROUTE_INVALID")
        try:
            confidence = float(raw_candidate.get("confidence"))
            if not 0.0 <= confidence <= 1.0:
                raise ValueError
        except (TypeError, ValueError):
            local_errors.append("CONFIDENCE_INVALID")

        evidence_ids = raw_candidate.get("evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            local_errors.append("EVIDENCE_REQUIRED")
            evidence_ids = []
        referenced_text: List[str] = []
        for evidence_id in evidence_ids:
            evidence = evidence_by_id.get(str(evidence_id))
            if not evidence:
                local_errors.append("EVIDENCE_REFERENCE_INVALID")
                continue
            evidence_document_id = str(
                evidence.get("document_id") or evidence.get("documentId") or ""
            )
            if evidence_document_id != document_id:
                local_errors.append("CROSS_DOCUMENT_EVIDENCE")
            referenced_text.append(
                str(evidence.get("text") or evidence.get("raw_text") or evidence.get("excerpt") or "")
            )
        counter_ids = raw_candidate.get("counterevidence_ids")
        if not isinstance(counter_ids, list):
            local_errors.append("COUNTEREVIDENCE_NOT_LIST")
        else:
            for evidence_id in counter_ids:
                evidence = evidence_by_id.get(str(evidence_id))
                if not evidence:
                    local_errors.append("EVIDENCE_REFERENCE_INVALID")
                elif str(evidence.get("document_id") or evidence.get("documentId") or "") != document_id:
                    local_errors.append("CROSS_DOCUMENT_EVIDENCE")

        raw_value = raw_candidate.get("raw_value")
        if raw_value not in (None, ""):
            compact_value = _compact(raw_value)
            source_blobs = [raw_text, *referenced_text]
            if not compact_value or not any(compact_value in _compact(blob) for blob in source_blobs):
                local_errors.append("RAW_VALUE_NOT_IN_SOURCE")

        if local_errors:
            errors.extend(local_errors)
            continue
        candidate = dict(raw_candidate)
        candidate["status"] = "CANDIDATE"
        candidate["decision_owner"] = "rule_or_human"
        candidate["schema_version"] = "field_candidate.v2"
        candidate["document_id"] = document_id
        accepted.append(candidate)

    if errors:
        return [], sorted(set(errors))
    return accepted, []
