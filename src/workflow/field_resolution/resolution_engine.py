"""Deterministic entity resolution with evidence-backed explanations."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from src.workflow.field_resolution.contracts import make_resolution_edge
from src.workflow.field_resolution.normalizers import (
    normalize_address,
    normalize_currency,
    normalize_goods,
    normalize_identifier,
    normalize_legal_entity,
    normalize_unit,
    parse_decimal,
)


_ROLE_LABELS = {
    "order": "订单",
    "contract": "合同",
    "delivery": "发货",
    "receipt": "签收",
    "invoice": "发票",
    "payment": "回款",
    "other": "其他单据",
}
_ROLE_ORDER = {role: index for index, role in enumerate(("order", "contract", "delivery", "receipt", "invoice", "payment", "other"))}

_CONCEPTS: dict[str, tuple[tuple[str, ...], Callable[[Any], Any], str]] = {
    "order_reference": (("orderNo", "salesOrderNo", "purchaseOrderNo", "relatedOrderNo"), normalize_identifier, "订单编号格式归一化"),
    "seller_identity": (("sellerName", "supplierName", "vendorName", "shipperName"), normalize_legal_entity, "主体名称标点与空格归一化"),
    "buyer_identity": (("buyerName", "customerName", "consigneeName", "receiverName"), normalize_legal_entity, "主体名称标点与空格归一化"),
    "seller_address": (("sellerAddress", "supplierAddress", "shipperAddress"), normalize_address, "移除空格和标点"),
    "buyer_address": (("buyerAddress", "customerAddress", "deliveryAddress", "consigneeAddress"), normalize_address, "移除空格和标点"),
    "goods_identity": (("goodsName", "productName", "itemName", "cargoName"), normalize_goods, "货品名称格式归一化"),
    "model": (("model", "specification", "specModel", "goodsModel"), normalize_goods, "规格型号格式归一化"),
    "quantity": (("quantity", "orderQuantity", "receiptQuantity", "invoiceQuantity", "acceptedQuantity"), parse_decimal, "数值精度归一化"),
    "unit": (("unit", "quantityUnit", "goodsUnit"), normalize_unit, "计量单位归一化"),
    "currency": (("currency", "currencyCode"), normalize_currency, "币种代码归一化"),
    "gross_amount": (("totalAmount", "grossAmount", "amountWithTax", "totalWithTax"), parse_decimal, "金额格式归一化"),
}

_TAX_KEYS = {
    "seller_identity": ("sellerTaxId", "supplierTaxId", "vendorTaxId"),
    "buyer_identity": ("buyerTaxId", "customerTaxId", "consigneeTaxId"),
}


def _value(node: dict[str, Any]) -> Any:
    value = node.get("normalized_value")
    return node.get("raw_value") if value is None or value == "" else value


def _sort_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        nodes,
        key=lambda node: (
            _ROLE_ORDER.get(str(node.get("document_role") or "other"), 99),
            str(node.get("document_id") or ""),
            str(node.get("field_key") or ""),
        ),
    )


def _nodes_for_keys(evidence_nodes: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    allowed = set(keys)
    return _sort_nodes([node for node in evidence_nodes if str(node.get("field_key") or "") in allowed])


def _references(nodes: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    ids = [str(node.get("evidence_id") or "") for node in nodes if node.get("evidence_id")]
    return ids[:1], ids[1:]


def _display_number(value: Any) -> str:
    number = parse_decimal(value)
    if number is None:
        return str(value)
    return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")


def _facts(concept: str, nodes: list[dict[str, Any]]) -> list[str]:
    facts: list[str] = []
    for node in nodes:
        role = _ROLE_LABELS.get(str(node.get("document_role") or "other"), "其他单据")
        value = _value(node)
        if concept == "quantity":
            facts.append(f"{role}数量{_display_number(value)}台")
        elif concept == "gross_amount":
            facts.append(f"{role}价税合计{_display_number(value)}")
        else:
            facts.append(f"{role}{value}")
    return facts


def _tax_conflict_edge(
    concept: str,
    party_nodes: list[dict[str, Any]],
    tax_nodes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    usable_tax = [node for node in tax_nodes if node.get("usable_for_decision")]
    normalized = {normalize_identifier(_value(node)) for node in usable_tax if normalize_identifier(_value(node))}
    if len(normalized) <= 1:
        return None
    nodes = _sort_nodes([*party_nodes, *usable_tax])
    left, right = _references(nodes)
    return make_resolution_edge(
        concept=concept,
        relation_type="CONTRADICTS",
        left_evidence_ids=left,
        right_evidence_ids=right,
        decision_owner="rule",
        status="CONFLICT",
        reason_code="TAX_ID_CONFLICT",
        confirmed_facts=_facts(concept, party_nodes),
        counter_evidence=[
            {
                "reason_code": "TAX_ID_CONFLICT",
                "message": "主体名称相近，但税号不一致，不能自动认定为同一主体",
                "evidence_ids": [str(node.get("evidence_id") or "") for node in usable_tax],
                "values": [str(_value(node)) for node in usable_tax],
            }
        ],
    )


def _resolve_concept(
    concept: str,
    nodes: list[dict[str, Any]],
    normalizer: Callable[[Any], Any],
    transformation: str,
) -> dict[str, Any]:
    left, right = _references(nodes)
    usable = [node for node in nodes if node.get("usable_for_decision")]
    if len(usable) < 2:
        return make_resolution_edge(
            concept=concept,
            relation_type="MISSING_EVIDENCE",
            left_evidence_ids=left,
            right_evidence_ids=right,
            decision_owner="rule",
            status="CANDIDATE",
            reason_code="DECISION_EVIDENCE_MISSING",
            counter_evidence=[{"reason_code": "DECISION_EVIDENCE_MISSING", "message": "可定位证据不足两份"}],
        )

    normalized = [normalizer(_value(node)) for node in usable]
    raw = [str(_value(node)).strip().casefold() for node in usable]
    all_valid = all(value is not None and value != "" for value in normalized)
    same = all_valid and len(set(normalized)) == 1
    usable_left, usable_right = _references(usable)
    if same:
        relation = "EXACT_EQUAL" if len(set(raw)) == 1 else "NORMALIZED_EQUAL"
        transformations = [] if relation == "EXACT_EQUAL" else [transformation]
        return make_resolution_edge(
            concept=concept,
            relation_type=relation,
            left_evidence_ids=usable_left,
            right_evidence_ids=usable_right,
            decision_owner="rule",
            status="CONFIRMED",
            reason_code="RAW_VALUE_EQUAL" if relation == "EXACT_EQUAL" else "NORMALIZED_VALUE_EQUAL",
            transformations=transformations,
            confirmed_facts=_facts(concept, usable),
        )

    reason = "IDENTIFIER_MISMATCH" if concept == "order_reference" else "DETERMINISTIC_VALUE_CONFLICT"
    return make_resolution_edge(
        concept=concept,
        relation_type="CONTRADICTS",
        left_evidence_ids=usable_left,
        right_evidence_ids=usable_right,
        decision_owner="rule",
        status="CONFLICT",
        reason_code=reason,
        transformations=[transformation] if any(raw_value != str(norm).casefold() for raw_value, norm in zip(raw, normalized)) else [],
        counter_evidence=[
            {
                "reason_code": reason,
                "message": "确定性归一化后仍不一致",
                "evidence_ids": [str(node.get("evidence_id") or "") for node in usable],
                "values": [str(_value(node)) for node in usable],
            }
        ],
    )


def resolve_rule_edges(
    documents: list[dict[str, Any]],
    evidence_nodes: list[dict[str, Any]],
    sample_row: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve deterministic comparisons. Documents/sample row are context only."""
    del documents, sample_row
    edges: list[dict[str, Any]] = []
    for concept, (keys, normalizer, transformation) in _CONCEPTS.items():
        nodes = _nodes_for_keys(evidence_nodes, keys)
        if not nodes:
            continue
        if concept in _TAX_KEYS:
            tax_nodes = _nodes_for_keys(evidence_nodes, _TAX_KEYS[concept])
            conflict = _tax_conflict_edge(concept, nodes, tax_nodes)
            if conflict:
                edges.append(conflict)
                continue
        edges.append(_resolve_concept(concept, nodes, normalizer, transformation))
    return edges


__all__ = ["resolve_rule_edges"]
