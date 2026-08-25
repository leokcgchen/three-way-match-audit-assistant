from src.workflow.field_resolution.contracts import (
    COMPARISON_DOMAINS,
    EDGE_STATUSES,
    RELATION_TYPES,
    make_evidence_node,
    make_resolution_edge,
    validate_evidence_node,
    validate_resolution_edge,
)


def _anchored_node(**overrides: object) -> dict:
    values = {
        "document_id": "order.pdf",
        "document_role": "order",
        "field_key": "goodsName",
        "raw_value": "伺服电机",
        "excerpt": "伺服电机",
        "page": 1,
        "char_start": 5,
        "char_end": 9,
        "bbox": None,
        "source": "ocr",
        "extractor": "field-parser-v1",
    }
    values.update(overrides)
    return make_evidence_node(**values)


def test_contract_enums_cover_approved_design() -> None:
    assert {"CANDIDATE", "CONFIRMED", "CONFLICT", "REJECTED"}.issubset(EDGE_STATUSES)
    assert {"EXACT_EQUAL", "NORMALIZED_EQUAL", "SEMANTIC_EQUIVALENT"}.issubset(RELATION_TYPES)
    assert COMPARISON_DOMAINS == (
        "consistency",
        "recalculation",
        "chronology",
        "document_specific",
        "issues",
    )


def test_evidence_requires_real_anchor() -> None:
    node = _anchored_node(excerpt="", page=None, char_start=None, char_end=None, bbox=None)
    assert validate_evidence_node(node) == ["EVIDENCE_ANCHOR_MISSING"]


def test_evidence_rejects_invalid_character_range() -> None:
    node = _anchored_node(char_start=9, char_end=5)
    assert validate_evidence_node(node) == ["EVIDENCE_CHAR_RANGE_INVALID"]


def test_evidence_id_is_stable_and_changes_with_location() -> None:
    first = _anchored_node()
    same = _anchored_node()
    moved = _anchored_node(char_start=6, char_end=10)
    assert first["evidence_id"] == same["evidence_id"]
    assert first["evidence_id"] != moved["evidence_id"]
    assert first["schema_version"] == "field_evidence_node.v1"


def test_edge_cannot_reference_unknown_evidence() -> None:
    edge = make_resolution_edge(
        concept="goods_identity",
        relation_type="SEMANTIC_EQUIVALENT",
        left_evidence_ids=["ev-missing"],
        right_evidence_ids=["ev-2"],
        decision_owner="model",
        status="CANDIDATE",
        reason_code="NAME_MODEL_SPLIT_EQUIVALENT",
    )
    assert validate_resolution_edge(edge, {}) == ["EVIDENCE_REFERENCE_INVALID"]


def test_valid_edge_has_stable_schema_and_references() -> None:
    left = _anchored_node()
    right = _anchored_node(
        document_id="receipt.pdf",
        document_role="receipt",
        char_start=12,
        char_end=16,
    )
    evidence = {left["evidence_id"]: left, right["evidence_id"]: right}
    edge = make_resolution_edge(
        concept="goods_identity",
        relation_type="EXACT_EQUAL",
        left_evidence_ids=[left["evidence_id"]],
        right_evidence_ids=[right["evidence_id"]],
        decision_owner="rule",
        status="CONFIRMED",
        reason_code="RAW_VALUE_EQUAL",
        confirmed_facts=["订单与签收单均记载伺服电机"],
    )
    assert edge["schema_version"] == "resolution_edge.v1"
    assert validate_resolution_edge(edge, evidence) == []

