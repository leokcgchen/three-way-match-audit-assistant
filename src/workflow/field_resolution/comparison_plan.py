"""Build the five-domain auditor comparison plan from evidence and resolution edges."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from src.workflow.chain_workspace import docs_for_chain, get_sample
from src.workflow.field_resolution.contracts import make_resolution_edge
from src.workflow.field_resolution.evidence_inventory import attach_document_evidence
from src.workflow.field_resolution.line_items import extract_line_nodes, match_line_groups
from src.workflow.field_resolution.resolution_engine import resolve_rule_edges
from src.workflow.field_resolution.semantic_adapter import apply_semantic_proposals


_STATUS_PRIORITY = {"PASS": 0, "PASS_WITH_WARNING": 1, "MISSING_EVIDENCE": 2, "CONFLICT": 3}
_ESSENTIAL_CONCEPTS = {"order_reference", "seller_identity", "buyer_identity", "goods_identity", "quantity", "gross_amount"}
_CONCEPT_LABELS = {
    "order_reference": "关联订单号",
    "seller_identity": "卖方/供货方",
    "buyer_identity": "买方/收货方",
    "seller_address": "卖方地址",
    "buyer_address": "买方地址/交货地点",
    "goods_identity": "货物",
    "model": "规格型号",
    "quantity": "数量",
    "unit": "计量单位",
    "currency": "币种",
    "gross_amount": "价税合计",
}
_DATE_LABELS = {
    ("order", "documentDate"): "订单日期",
    ("order", "plannedDeliveryDate"): "计划交付日期",
    ("contract", "documentDate"): "合同日期",
    ("delivery", "deliveryDate"): "发货日期",
    ("receipt", "acceptanceDate"): "验收/控制权转移",
    ("receipt", "arrivalDateTime"): "到货时间",
    ("receipt", "acceptanceDateTime"): "验收/控制权转移时间",
    ("receipt", "receiptDate"): "签收/控制权转移",
    ("receipt", "documentDate"): "签收单日期",
    ("invoice", "documentDate"): "开票日期",
    ("invoice", "invoiceDate"): "开票日期",
    ("invoice", "invoiceDateTime"): "开票时间",
    ("invoice", "postingDate"): "入账日期",
}
_DOCUMENT_SPECIFIC_KEYS = {
    "documentNo",
    "invoiceNo",
    "contractNo",
    "receiptNo",
    "customerCode",
    "buyerCode",
    "clientCode",
    "materialCode",
    "batchNo",
    "carrierName",
    "vehiclePlate",
    "bankAccount",
    "drawer",
    "reviewer",
}


def aggregate_status(statuses: list[str]) -> str:
    relevant = [status for status in statuses if status in _STATUS_PRIORITY]
    return max(relevant, key=lambda status: _STATUS_PRIORITY[status]) if relevant else "PASS"


def _source_documents(job: dict[str, Any], chain_id: str) -> list[dict[str, Any]]:
    classified = list(job.get("classified") or [])
    selected = docs_for_chain(classified, chain_id) if chain_id else classified
    return deepcopy(selected or classified)


def field_resolution_source_hash(job: dict[str, Any], chain_id: str) -> str:
    documents = _source_documents(job, chain_id)
    stable = [
        {
            "document_id": doc.get("file_fingerprint") or doc.get("file_name"),
            "doc_type": doc.get("doc_type"),
            "fields": doc.get("fields") or {},
            "field_meta": doc.get("_field_meta") or {},
            "raw_text": doc.get("raw_text") or "",
            "text_blocks": doc.get("text_blocks") or [],
        }
        for doc in documents
    ]
    blob = json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return sha256(blob.encode("utf-8")).hexdigest()


def _reason_text(edge: dict[str, Any]) -> str:
    facts = [str(value) for value in list(edge.get("confirmed_facts") or []) if str(value)]
    transformations = [str(value) for value in list(edge.get("transformations") or []) if str(value)]
    counter = [str(item.get("message") or item.get("reason_code") or "") for item in list(edge.get("counter_evidence") or []) if isinstance(item, dict)]
    parts: list[str] = []
    if facts:
        parts.append("；".join(facts))
    if transformations:
        parts.append("处理：" + "、".join(transformations))
    if edge.get("calculation"):
        parts.append("复算：" + str(edge.get("calculation")))
    if counter:
        parts.append("反证：" + "；".join(counter))
    if not parts:
        parts.append(str(edge.get("reason_code") or "待人工解释"))
    return "。".join(parts)


def _edge_result(edge: dict[str, Any]) -> str:
    if edge.get("status") == "CONFLICT":
        return "CONFLICT"
    if edge.get("status") in {"CANDIDATE", "REJECTED"}:
        return "MISSING_EVIDENCE"
    return "PASS"


def _edge_row(edge: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    evidence_ids = [*list(edge.get("left_evidence_ids") or []), *list(edge.get("right_evidence_ids") or [])]
    values = [
        {
            "evidence_id": evidence_id,
            "document_id": evidence_by_id.get(str(evidence_id), {}).get("document_id"),
            "document_role": evidence_by_id.get(str(evidence_id), {}).get("document_role"),
            "field_key": evidence_by_id.get(str(evidence_id), {}).get("field_key"),
            "value": evidence_by_id.get(str(evidence_id), {}).get("normalized_value")
            if evidence_by_id.get(str(evidence_id), {}).get("normalized_value") not in (None, "")
            else evidence_by_id.get(str(evidence_id), {}).get("raw_value"),
            "page": evidence_by_id.get(str(evidence_id), {}).get("page"),
            "excerpt": evidence_by_id.get(str(evidence_id), {}).get("excerpt"),
        }
        for evidence_id in evidence_ids
        if str(evidence_id) in evidence_by_id
    ]
    return {
        "row_id": str(edge.get("edge_id") or ""),
        "edge_id": str(edge.get("edge_id") or ""),
        "concept": str(edge.get("concept") or ""),
        "label": _CONCEPT_LABELS.get(str(edge.get("concept") or ""), str(edge.get("concept") or "")),
        "result": _edge_result(edge),
        "relation_type": edge.get("relation_type"),
        "reason_code": edge.get("reason_code"),
        "reason_text": _reason_text(edge),
        "evidence_ids": evidence_ids,
        "values": values,
        "transformations": list(edge.get("transformations") or []),
        "counter_evidence": list(edge.get("counter_evidence") or []),
    }


def _customer_code_issue(evidence_nodes: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    nodes = [
        node
        for node in evidence_nodes
        if str(node.get("field_key") or "") in {"customerCode", "buyerCode", "clientCode"}
        and node.get("usable_for_decision")
    ]
    values = {str(node.get("normalized_value") if node.get("normalized_value") not in (None, "") else node.get("raw_value")) for node in nodes}
    if len(values) <= 1:
        return None, None
    ids = [str(node.get("evidence_id") or "") for node in nodes]
    edge = make_resolution_edge(
        concept="customer_code_mapping",
        relation_type="DOCUMENT_SPECIFIC",
        left_evidence_ids=ids[:1],
        right_evidence_ids=ids[1:],
        decision_owner="rule",
        status="CANDIDATE",
        reason_code="CUSTOMER_CODE_MAPPING_REQUIRED",
        confirmed_facts=["客户名称、地址等主体证据可勾连"],
        counter_evidence=[
            {
                "reason_code": "CUSTOMER_CODE_MAPPING_REQUIRED",
                "message": "不同系统客户编码不一致，需取得编码映射或人工解释",
                "evidence_ids": ids,
                "values": sorted(values),
            }
        ],
    )
    issue = {
        "issue_code": "CUSTOMER_CODE_MAPPING_REQUIRED",
        "severity": "WARNING",
        "title": "客户编码待映射",
        "message": "销售与仓储等系统使用了不同客户编码；主体名称一致不等于编码天然相同。",
        "edge_id": edge["edge_id"],
        "evidence_ids": ids,
        "values": sorted(values),
        "resolution_status": "PENDING",
    }
    return edge, issue


def _chronology(evidence_nodes: list[dict[str, Any]], job: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for node in evidence_nodes:
        label = _DATE_LABELS.get((str(node.get("document_role") or ""), str(node.get("field_key") or "")))
        if not label or label in seen_labels or not node.get("usable_for_decision"):
            continue
        value = node.get("normalized_value") if node.get("normalized_value") not in (None, "") else node.get("raw_value")
        events.append(
            {
                "label": label,
                "value": str(value),
                "evidence_id": node.get("evidence_id"),
                "document_id": node.get("document_id"),
                "page": node.get("page"),
            }
        )
        seen_labels.add(label)
    events.sort(key=lambda event: str(event.get("value") or ""))
    cutoff = sample.get("cutoff_test") or sample.get("cutoff_result") or job.get("cutoff_test") or job.get("cutoff_result") or {}
    raw_status = str(cutoff.get("test_status") or cutoff.get("测试状态") or cutoff.get("status") or "NOT_TESTED").upper()
    status = "CONFLICT" if raw_status == "FAIL" else ("PASS" if raw_status == "PASS" else "NOT_TESTED")
    return {
        "events": events,
        "reporting_period_end": job.get("period_end"),
        "status": status,
        "reason_text": str(cutoff.get("reason") or cutoff.get("问题描述") or "按控制权转移日、报告期末与入账日判断归属期间。"),
    }


def build_field_resolution_payload(
    job: dict[str, Any], chain_id: str, semantic_payload: Any = None
) -> dict[str, Any]:
    documents = _source_documents(job, chain_id)
    for document in documents:
        attach_document_evidence(document)
    evidence_nodes = [node for document in documents for node in list(document.get("field_evidence_nodes") or [])]
    evidence_by_id = {str(node.get("evidence_id") or ""): node for node in evidence_nodes if node.get("evidence_id")}
    edges = resolve_rule_edges(documents, evidence_nodes, {})
    if semantic_payload is not None:
        edges.extend(apply_semantic_proposals(semantic_payload, evidence_by_id, edges))

    customer_edge, customer_issue = _customer_code_issue(evidence_nodes)
    issues: list[dict[str, Any]] = []
    if customer_edge and customer_issue:
        edges.append(customer_edge)
        issues.append(customer_issue)

    line_nodes = [line for document in documents for line in extract_line_nodes(document)]
    line_groups = match_line_groups(
        [line for line in line_nodes if line.get("document_role") == "order"],
        [line for line in line_nodes if line.get("document_role") in {"receipt", "delivery"}],
        [line for line in line_nodes if line.get("document_role") == "invoice"],
    )
    consistency_edges = [edge for edge in edges if edge.get("concept") in _CONCEPT_LABELS]
    consistency_rows = [_edge_row(edge, evidence_by_id) for edge in consistency_edges]
    three_way_inputs = [
        row["result"]
        for row in consistency_rows
        if row["concept"] in _ESSENTIAL_CONCEPTS
    ]

    recalculation_rows: list[dict[str, Any]] = []
    for group in line_groups:
        recalculation_rows.append(
            {
                "row_id": f"{group['order_line_id']}:quantity",
                "concept": "line_quantity",
                "label": f"{group['order_line_id']} 数量归集",
                "result": group.get("quantity_result"),
                "calculation": group.get("calculation"),
                "evidence_ids": group.get("evidence_ids") or [],
                "reason_codes": group.get("reason_codes") or [],
            }
        )
        if group.get("amount_result") != "NOT_TESTED":
            recalculation_rows.append(
                {
                    "row_id": f"{group['order_line_id']}:amount",
                    "concept": "line_amount",
                    "label": f"{group['order_line_id']} 金额复算",
                    "result": group.get("amount_result"),
                    "calculation": group.get("amount_calculation"),
                    "evidence_ids": group.get("evidence_ids") or [],
                    "reason_codes": group.get("reason_codes") or [],
                }
            )
        if group.get("quantity_result") == "FAIL" or group.get("amount_result") == "FAIL" or group.get("unit_result") == "FAIL":
            three_way_inputs.append("CONFLICT")
        elif group.get("quantity_result") == "REVIEW":
            three_way_inputs.append("MISSING_EVIDENCE")

    if issues and "CONFLICT" not in three_way_inputs and "MISSING_EVIDENCE" not in three_way_inputs:
        three_way_inputs.append("PASS_WITH_WARNING")
    three_way_status = aggregate_status(three_way_inputs)
    sample = get_sample(job, chain_id) if chain_id else {}
    chronology = _chronology(evidence_nodes, job, sample)
    cutoff_status = chronology["status"]
    overall_inputs = [three_way_status]
    if cutoff_status == "CONFLICT":
        overall_inputs.append("CONFLICT")
    elif cutoff_status == "PASS":
        overall_inputs.append("PASS")
    overall_status = aggregate_status(overall_inputs)

    used_keys = {
        str(evidence_by_id.get(str(evidence_id), {}).get("field_key") or "")
        for edge in consistency_edges
        for evidence_id in [*list(edge.get("left_evidence_ids") or []), *list(edge.get("right_evidence_ids") or [])]
    }
    chronology_ids = {str(event.get("evidence_id") or "") for event in chronology["events"]}
    document_specific = [
        {
            "row_id": str(node.get("evidence_id") or ""),
            "field_key": str(node.get("field_key") or ""),
            "label": str(node.get("field_key") or ""),
            "value": node.get("normalized_value") if node.get("normalized_value") not in (None, "") else node.get("raw_value"),
            "document_id": node.get("document_id"),
            "document_role": node.get("document_role"),
            "evidence_id": node.get("evidence_id"),
            "page": node.get("page"),
            "comparison_effect": "NONE",
        }
        for node in evidence_nodes
        if (
            str(node.get("field_key") or "") in _DOCUMENT_SPECIFIC_KEYS
            or str(node.get("field_key") or "") not in used_keys
        )
        and str(node.get("evidence_id") or "") not in chronology_ids
    ]
    comparison_plan = {
        "schema_version": "comparison_plan.v1",
        "chain_id": chain_id,
        "overall_status": overall_status,
        "three_way_status": three_way_status,
        "cutoff_status": cutoff_status,
        "domains": {
            "consistency": consistency_rows,
            "recalculation": recalculation_rows,
            "chronology": chronology,
            "document_specific": document_specific,
            "issues": issues,
        },
    }
    source_hash = field_resolution_source_hash(job, chain_id)
    semantic_hash = sha256(json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest() if semantic_payload is not None else "none"
    resolution_id = "fr-" + sha256(f"{chain_id}|{source_hash}|{semantic_hash}".encode("utf-8")).hexdigest()[:24]
    return {
        "schema_version": "field_resolution.v1",
        "resolution_id": resolution_id,
        "source_hash": source_hash,
        "chain_id": chain_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_nodes": evidence_nodes,
        "edges": edges,
        "line_groups": line_groups,
        "comparison_plan": comparison_plan,
        "issues": issues,
        "audit_log": [],
    }


def build_comparison_plan(job: dict[str, Any], chain_id: str, semantic_payload: Any = None) -> dict[str, Any]:
    return build_field_resolution_payload(job, chain_id, semantic_payload)["comparison_plan"]


__all__ = [
    "aggregate_status",
    "build_comparison_plan",
    "build_field_resolution_payload",
    "field_resolution_source_hash",
]
