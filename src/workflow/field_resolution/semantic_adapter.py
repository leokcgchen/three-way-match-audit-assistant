"""Validate injected LLM entity-resolution proposals; never call a provider here."""

from __future__ import annotations

import json
from typing import Any

from src.workflow.field_resolution.contracts import make_resolution_edge


_MUTATION_KEYS = {
    "proposed_value",
    "normalized_value",
    "corrected_value",
    "amount",
    "quantity",
    "date",
    "identifier",
}
_RULE_ONLY_CONCEPTS = {
    "gross_amount",
    "net_amount",
    "tax_amount",
    "quantity",
    "document_date",
    "order_reference",
    "invoice_reference",
}


def build_semantic_resolution_prompt(
    evidence_nodes: list[dict[str, Any]], unresolved_concepts: list[str]
) -> str:
    evidence = [
        {
            "evidence_id": node.get("evidence_id"),
            "document_id": node.get("document_id"),
            "document_role": node.get("document_role"),
            "field_key": node.get("field_key"),
            "excerpt": node.get("excerpt"),
            "page": node.get("page"),
            "usable_for_decision": node.get("usable_for_decision"),
        }
        for node in evidence_nodes
    ]
    return f"""任务：为尚未解决的跨单据字段提出可解释的实体解析关系建议。

未解决概念：{json.dumps(unresolved_concepts, ensure_ascii=False)}
证据节点：{json.dumps(evidence, ensure_ascii=False, default=str)}

严格限制：
1. 只引用 evidence_id，不得引用文件名含义或补造证据。
2. supporting_facts 中的 excerpt 必须逐字来自对应证据节点。
3. 不得生成或改写金额、数量、日期、编号；这些事实只由规则引擎处理。
4. 每个建议必须列出 semantic_dimensions、transformations、supporting_facts、counter_evidence。
5. 语义一致必须至少有两个互相独立的证据维度；单一名称相似只能作为候选。
6. 输出 JSON 数组，不要输出审计 PASS/FAIL 终态。

每项结构：
{{"concept":"...","proposed_relation":"SEMANTIC_EQUIVALENT","source_evidence_ids":["..."],
"semantic_dimensions":["..."],"transformations":["..."],
"supporting_facts":[{{"evidence_id":"...","excerpt":"...","statement":"..."}}],
"counter_evidence":[],"reason_code":"..."}}
"""


def _rejected(
    proposal: Any,
    reason_code: str,
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    src = proposal if isinstance(proposal, dict) else {}
    valid_ids = [
        str(value)
        for value in list(src.get("source_evidence_ids") or [])
        if str(value) in evidence_by_id
    ]
    edge = make_resolution_edge(
        concept=str(src.get("concept") or "unknown"),
        relation_type="SEMANTIC_EQUIVALENT",
        left_evidence_ids=valid_ids[:1],
        right_evidence_ids=valid_ids[1:],
        decision_owner="model_gate",
        status="REJECTED",
        reason_code=reason_code,
        metadata={"proposal_rejected": True},
    )
    return edge


def validate_semantic_proposal(
    payload: Any,
    evidence_by_id: dict[str, dict[str, Any]],
    deterministic_edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Turn provider output into a controlled edge after evidence and precedence gates."""
    if not isinstance(payload, dict):
        return _rejected(payload, "PROPOSAL_SHAPE_INVALID", evidence_by_id)
    if any(key in payload for key in _MUTATION_KEYS):
        return _rejected(payload, "MODEL_VALUE_MUTATION_NOT_ALLOWED", evidence_by_id)
    concept = str(payload.get("concept") or "").strip()
    if not concept or payload.get("proposed_relation") != "SEMANTIC_EQUIVALENT":
        return _rejected(payload, "PROPOSAL_SHAPE_INVALID", evidence_by_id)
    if concept in _RULE_ONLY_CONCEPTS:
        return _rejected(payload, "MODEL_DECISION_DOMAIN_FORBIDDEN", evidence_by_id)

    ids = [str(value) for value in list(payload.get("source_evidence_ids") or []) if str(value)]
    if len(ids) < 2 or any(evidence_id not in evidence_by_id for evidence_id in ids):
        return _rejected(payload, "EVIDENCE_REFERENCE_INVALID", evidence_by_id)
    nodes = [evidence_by_id[evidence_id] for evidence_id in ids]
    if any(not node.get("usable_for_decision") for node in nodes):
        return _rejected(payload, "EVIDENCE_ANCHOR_MISSING", evidence_by_id)

    supporting = payload.get("supporting_facts")
    if not isinstance(supporting, list):
        return _rejected(payload, "SUPPORTING_FACTS_INVALID", evidence_by_id)
    facts_by_id = {
        str(fact.get("evidence_id")): fact
        for fact in supporting
        if isinstance(fact, dict) and fact.get("evidence_id")
    }
    for evidence_id in ids:
        fact = facts_by_id.get(evidence_id)
        node_excerpt = str(evidence_by_id[evidence_id].get("excerpt") or "")
        fact_excerpt = str((fact or {}).get("excerpt") or "")
        if not fact or not fact_excerpt or fact_excerpt not in node_excerpt:
            return _rejected(payload, "EVIDENCE_EXCERPT_INVALID", evidence_by_id)

    if any(
        str(edge.get("concept") or "") == concept and edge.get("status") == "CONFLICT"
        for edge in deterministic_edges
    ):
        return _rejected(payload, "DETERMINISTIC_CONFLICT_PRECEDENCE", evidence_by_id)

    dimensions = [str(value).strip() for value in list(payload.get("semantic_dimensions") or []) if str(value).strip()]
    distinct_dimensions = set(dimensions)
    distinct_documents = {str(node.get("document_id") or "") for node in nodes}
    counter_evidence = [dict(item) for item in list(payload.get("counter_evidence") or []) if isinstance(item, dict)]
    if counter_evidence:
        status = "CANDIDATE"
        reason_code = "COUNTER_EVIDENCE_REQUIRES_REVIEW"
    elif len(distinct_dimensions) < 2 or len(distinct_documents) < 2:
        status = "CANDIDATE"
        reason_code = "INDEPENDENT_EVIDENCE_INSUFFICIENT"
    else:
        status = "CONFIRMED"
        reason_code = str(payload.get("reason_code") or "SEMANTIC_EVIDENCE_CONFIRMED")

    return make_resolution_edge(
        concept=concept,
        relation_type="SEMANTIC_EQUIVALENT",
        left_evidence_ids=ids[:1],
        right_evidence_ids=ids[1:],
        decision_owner="model_gate",
        status=status,
        reason_code=reason_code,
        transformations=list(payload.get("transformations") or []),
        confirmed_facts=[str(fact.get("statement") or "") for fact in supporting if isinstance(fact, dict)],
        counter_evidence=counter_evidence,
        semantic_dimensions=dimensions,
        metadata={"provider_output_validated": True},
    )


def apply_semantic_proposals(
    proposals: Any,
    evidence_by_id: dict[str, dict[str, Any]],
    deterministic_edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(proposals, list):
        return [validate_semantic_proposal(proposals, evidence_by_id, deterministic_edges)]
    return [
        validate_semantic_proposal(proposal, evidence_by_id, deterministic_edges)
        for proposal in proposals
    ]


__all__ = [
    "apply_semantic_proposals",
    "build_semantic_resolution_prompt",
    "validate_semantic_proposal",
]
