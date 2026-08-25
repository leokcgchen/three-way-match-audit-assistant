"""Versioned dictionary contracts for evidence nodes and resolution edges."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256
import json
from typing import Any


EDGE_STATUSES = frozenset({"CANDIDATE", "CONFIRMED", "CONFLICT", "REJECTED"})
RELATION_TYPES = frozenset(
    {
        "EXACT_EQUAL",
        "NORMALIZED_EQUAL",
        "DERIVED_EQUAL",
        "SEMANTIC_EQUIVALENT",
        "CHRONOLOGY",
        "DOCUMENT_SPECIFIC",
        "MISSING_EVIDENCE",
        "CONTRADICTS",
    }
)
COMPARISON_DOMAINS = (
    "consistency",
    "recalculation",
    "chronology",
    "document_specific",
    "issues",
)


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}-{sha256(serialized.encode('utf-8')).hexdigest()[:24]}"


def _string_list(values: Iterable[object] | None) -> list[str]:
    return [str(value) for value in (values or []) if str(value).strip()]


def make_evidence_node(
    *,
    document_id: str,
    document_role: str,
    field_key: str,
    raw_value: Any,
    excerpt: str,
    page: int | None,
    char_start: int | None,
    char_end: int | None,
    bbox: list[float] | tuple[float, ...] | None,
    source: str,
    extractor: str,
    normalized_value: Any = None,
    anchor_status: str | None = None,
    usable_for_decision: bool | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a stable evidence node without pretending an unlocated value is evidence."""
    bbox_value = list(bbox) if bbox is not None else None
    inferred_anchor_status = anchor_status or (
        "ANCHORED" if excerpt and ((char_start is not None and char_end is not None) or bbox_value) else "UNLOCATED"
    )
    inferred_usable = inferred_anchor_status == "ANCHORED" if usable_for_decision is None else bool(usable_for_decision)
    identity = {
        "document_id": document_id,
        "field_key": field_key,
        "raw_value": raw_value,
        "page": page,
        "char_start": char_start,
        "char_end": char_end,
        "bbox": bbox_value,
    }
    return {
        "schema_version": "field_evidence_node.v1",
        "evidence_id": _stable_id("ev", identity),
        "document_id": str(document_id or ""),
        "document_role": str(document_role or "unknown"),
        "field_key": str(field_key or ""),
        "raw_value": raw_value,
        "normalized_value": normalized_value,
        "excerpt": str(excerpt or ""),
        "page": page,
        "char_start": char_start,
        "char_end": char_end,
        "bbox": bbox_value,
        "source": str(source or "unknown"),
        "extractor": str(extractor or "unknown"),
        "anchor_status": inferred_anchor_status,
        "usable_for_decision": inferred_usable,
        "metadata": dict(metadata or {}),
    }


def validate_evidence_node(node: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if node.get("schema_version") != "field_evidence_node.v1":
        errors.append("EVIDENCE_SCHEMA_INVALID")
    if not str(node.get("document_id") or "").strip():
        errors.append("EVIDENCE_DOCUMENT_MISSING")
    if not str(node.get("field_key") or "").strip():
        errors.append("EVIDENCE_FIELD_KEY_MISSING")
    raw_value = node.get("raw_value")
    if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        errors.append("EVIDENCE_VALUE_MISSING")

    char_start = node.get("char_start")
    char_end = node.get("char_end")
    bbox = node.get("bbox")
    has_char_anchor = char_start is not None or char_end is not None
    has_bbox_anchor = isinstance(bbox, (list, tuple)) and len(bbox) == 4
    if has_char_anchor and (
        not isinstance(char_start, int)
        or not isinstance(char_end, int)
        or char_start < 0
        or char_end <= char_start
    ):
        errors.append("EVIDENCE_CHAR_RANGE_INVALID")
    if bbox is not None and not has_bbox_anchor:
        errors.append("EVIDENCE_BBOX_INVALID")
    if not str(node.get("excerpt") or "").strip() or (not has_char_anchor and not has_bbox_anchor):
        errors.append("EVIDENCE_ANCHOR_MISSING")
    return errors


def make_resolution_edge(
    *,
    concept: str,
    relation_type: str,
    left_evidence_ids: Iterable[object],
    right_evidence_ids: Iterable[object],
    decision_owner: str,
    status: str,
    reason_code: str,
    transformations: Iterable[object] | None = None,
    confirmed_facts: Iterable[object] | None = None,
    counter_evidence: Iterable[Mapping[str, Any]] | None = None,
    semantic_dimensions: Iterable[object] | None = None,
    calculation: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    left_ids = _string_list(left_evidence_ids)
    right_ids = _string_list(right_evidence_ids)
    identity = {
        "concept": concept,
        "relation_type": relation_type,
        "left": sorted(left_ids),
        "right": sorted(right_ids),
        "reason_code": reason_code,
    }
    return {
        "schema_version": "resolution_edge.v1",
        "edge_id": _stable_id("edge", identity),
        "concept": str(concept or ""),
        "relation_type": str(relation_type or ""),
        "left_evidence_ids": left_ids,
        "right_evidence_ids": right_ids,
        "decision_owner": str(decision_owner or ""),
        "status": str(status or ""),
        "reason_code": str(reason_code or ""),
        "transformations": _string_list(transformations),
        "confirmed_facts": _string_list(confirmed_facts),
        "counter_evidence": [dict(item) for item in (counter_evidence or [])],
        "semantic_dimensions": _string_list(semantic_dimensions),
        "calculation": calculation,
        "metadata": dict(metadata or {}),
    }


def validate_resolution_edge(
    edge: Mapping[str, Any], evidence_by_id: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    errors: list[str] = []
    if edge.get("schema_version") != "resolution_edge.v1":
        errors.append("EDGE_SCHEMA_INVALID")
    if not str(edge.get("concept") or "").strip():
        errors.append("EDGE_CONCEPT_MISSING")
    if edge.get("relation_type") not in RELATION_TYPES:
        errors.append("EDGE_RELATION_TYPE_INVALID")
    if edge.get("status") not in EDGE_STATUSES:
        errors.append("EDGE_STATUS_INVALID")
    if not str(edge.get("reason_code") or "").strip():
        errors.append("EDGE_REASON_MISSING")
    evidence_ids = [
        *_string_list(edge.get("left_evidence_ids")),
        *_string_list(edge.get("right_evidence_ids")),
    ]
    if not evidence_ids or any(evidence_id not in evidence_by_id for evidence_id in evidence_ids):
        errors.append("EVIDENCE_REFERENCE_INVALID")
    return errors


__all__ = [
    "COMPARISON_DOMAINS",
    "EDGE_STATUSES",
    "RELATION_TYPES",
    "make_evidence_node",
    "make_resolution_edge",
    "validate_evidence_node",
    "validate_resolution_edge",
]
