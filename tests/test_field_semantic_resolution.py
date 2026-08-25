from src.workflow.field_resolution.contracts import make_evidence_node, make_resolution_edge
from src.workflow.field_resolution.semantic_adapter import (
    apply_semantic_proposals,
    build_semantic_resolution_prompt,
    validate_semantic_proposal,
)


def _node(evidence_key: str, field_key: str, value: str, document: str) -> dict:
    node = make_evidence_node(
        document_id=document,
        document_role="order" if "order" in document else "receipt",
        field_key=field_key,
        raw_value=value,
        normalized_value=value,
        excerpt=value,
        page=1,
        char_start=0,
        char_end=len(value),
        bbox=None,
        source="ocr",
        extractor="test",
    )
    node["evidence_id"] = evidence_key
    return node


def _evidence() -> dict[str, dict]:
    nodes = [
        _node("order-name-model", "goodsName", "伺服电机 SM-130", "order.pdf"),
        _node("receipt-name", "goodsName", "伺服电机", "receipt.pdf"),
        _node("receipt-model", "model", "SM-130", "receipt.pdf"),
    ]
    return {node["evidence_id"]: node for node in nodes}


def _proposal(*, dimensions: list[str], evidence_ids: list[str] | None = None) -> dict:
    evidence = _evidence()
    ids = evidence_ids or list(evidence)
    return {
        "concept": "goods_identity",
        "proposed_relation": "SEMANTIC_EQUIVALENT",
        "source_evidence_ids": ids,
        "semantic_dimensions": dimensions,
        "transformations": ["拆分品名与型号"],
        "supporting_facts": [
            {"evidence_id": key, "excerpt": evidence[key]["excerpt"], "statement": "原文支持"}
            for key in ids
            if key in evidence
        ],
        "counter_evidence": [],
        "reason_code": "NAME_MODEL_SPLIT_EQUIVALENT",
    }


def test_name_and_model_are_two_independent_dimensions() -> None:
    result = validate_semantic_proposal(
        _proposal(dimensions=["goods_name", "model"]),
        _evidence(),
        [],
    )
    assert result["status"] == "CONFIRMED"
    assert result["decision_owner"] == "model_gate"
    assert result["relation_type"] == "SEMANTIC_EQUIVALENT"


def test_duplicate_paraphrases_are_not_independent_evidence() -> None:
    result = validate_semantic_proposal(
        _proposal(dimensions=["goods_name", "goods_name"]),
        _evidence(),
        [],
    )
    assert result["status"] == "CANDIDATE"
    assert result["reason_code"] == "INDEPENDENT_EVIDENCE_INSUFFICIENT"


def test_model_cannot_override_rule_amount_conflict() -> None:
    evidence = _evidence()
    conflict = make_resolution_edge(
        concept="goods_identity",
        relation_type="CONTRADICTS",
        left_evidence_ids=["order-name-model"],
        right_evidence_ids=["receipt-name"],
        decision_owner="rule",
        status="CONFLICT",
        reason_code="MODEL_CONFLICT",
    )
    result = validate_semantic_proposal(
        _proposal(dimensions=["goods_name", "model"]),
        evidence,
        [conflict],
    )
    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "DETERMINISTIC_CONFLICT_PRECEDENCE"


def test_invented_evidence_or_excerpt_is_rejected() -> None:
    invented = _proposal(dimensions=["goods_name", "model"], evidence_ids=["not-real"])
    assert validate_semantic_proposal(invented, _evidence(), [])["reason_code"] == "EVIDENCE_REFERENCE_INVALID"

    bad_excerpt = _proposal(dimensions=["goods_name", "model"])
    bad_excerpt["supporting_facts"][0]["excerpt"] = "模型编造的原文"
    assert validate_semantic_proposal(bad_excerpt, _evidence(), [])["reason_code"] == "EVIDENCE_EXCERPT_INVALID"


def test_model_generated_business_value_is_rejected() -> None:
    proposal = _proposal(dimensions=["goods_name", "model"])
    proposal["proposed_value"] = "SM-999"
    result = validate_semantic_proposal(proposal, _evidence(), [])
    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "MODEL_VALUE_MUTATION_NOT_ALLOWED"


def test_counterevidence_keeps_proposal_for_human_review() -> None:
    proposal = _proposal(dimensions=["goods_name", "model"])
    proposal["counter_evidence"] = [{"reason_code": "MODEL_VARIANT_AMBIGUOUS", "message": "型号后缀无法解释"}]
    result = validate_semantic_proposal(proposal, _evidence(), [])
    assert result["status"] == "CANDIDATE"
    assert result["reason_code"] == "COUNTER_EVIDENCE_REQUIRES_REVIEW"


def test_prompt_and_batch_adapter_keep_model_advisory_and_evidence_bound() -> None:
    prompt = build_semantic_resolution_prompt(list(_evidence().values()), ["goods_identity"])
    assert "只引用 evidence_id" in prompt
    assert "不得生成或改写金额、数量、日期、编号" in prompt
    results = apply_semantic_proposals(
        [_proposal(dimensions=["goods_name", "model"])],
        _evidence(),
        [],
    )
    assert [item["status"] for item in results] == ["CONFIRMED"]

