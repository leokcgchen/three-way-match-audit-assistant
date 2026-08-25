"""Per-field evidence gate used by automatic review.

Automatic acceptance requires a source location and a label compatible with the
document/field role.  Manual acceptance remains a separate human authority path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from src.models.field_values import accept_field, get_field_meta, seed_field_meta
from src.workflow.field_resolution.evidence_inventory import (
    attach_document_evidence,
    evidence_for_field,
)


@dataclass(frozen=True)
class FieldGateDecision:
    status: str
    reason_code: str
    reason: str
    evidence_ids: tuple[str, ...] = ()


_DOC_ROLE_ALIASES = {
    "warehouse_receipt": "receipt",
    "purchase_order": "order",
    "vat_invoice": "invoice",
}
_NUMERIC_FIELDS = {"quantity", "amount", "taxAmount", "totalAmount", "unitPrice", "unitPriceGross", "unitPriceNet"}
_DATE_FIELDS = {"documentDate", "deliveryDate", "acceptanceDate", "postingDate"}
_DERIVED_LINE_KEYS: dict[str, tuple[str, ...]] = {
    "quantity": ("quantity", "qty"),
    "amount": ("amount", "netAmount"),
    "totalAmount": ("totalAmount",),
}


def _role(document: dict[str, Any]) -> str:
    value = str(document.get("doc_type") or document.get("documentType") or "other").strip().lower()
    return _DOC_ROLE_ALIASES.get(value, value)


def _field_value(document: dict[str, Any], field_key: str) -> Any:
    meta = get_field_meta(document)
    slot = meta.get(field_key) if isinstance(meta.get(field_key), dict) else {}
    if slot.get("normalized_candidate") not in (None, ""):
        return slot.get("normalized_candidate")
    return (document.get("fields") or {}).get(field_key)


def _context(document: dict[str, Any], node: dict[str, Any]) -> str:
    raw = str(document.get("raw_text") or "")
    start = node.get("char_start")
    end = node.get("char_end")
    if isinstance(start, int) and isinstance(end, int):
        return raw[max(0, start - 64) : min(len(raw), end + 32)].casefold()
    return str(node.get("excerpt") or "").casefold()


def _label_role_compatible(document: dict[str, Any], field_key: str, node: dict[str, Any]) -> bool:
    role = _role(document)
    context = _context(document, node)
    if field_key in {"documentNo", "receiptNo", "invoiceNo", "contractNo"}:
        labels = {
            "receipt": ("验收单号", "签收单号", "收货单号", "入库单号", "certificate no"),
            "invoice": ("发票号码", "发票号", "invoice no"),
            "contract": ("合同编号", "合同号", "contract no", "s/c no"),
            "order": ("订单编号", "销售订单号", "order no"),
        }.get(role, ())
        if field_key == "receiptNo" and role != "receipt":
            return False
        if field_key == "invoiceNo" and role != "invoice":
            return False
        if field_key == "contractNo" and role != "contract":
            return False
        if bool(labels) and any(label in context for label in labels):
            return True
        if field_key == "invoiceNo" and role == "invoice":
            return bool(re.search(r"(?:^|\s)(?:invoice\s*)?no\.?\s+[a-z0-9]", context))
        return False
    if field_key == "orderNo":
        labels = ("订单编号", "订单号", "关联订单号", "对应销售订单", "order no")
        return any(label in context for label in labels)
    return True


def _typed_value_valid(field_key: str, value: Any) -> bool:
    if value in (None, ""):
        return False
    if field_key in _NUMERIC_FIELDS:
        try:
            parsed = Decimal(str(value).replace(",", "").replace("¥", "").replace("￥", ""))
            return parsed >= 0
        except (InvalidOperation, ValueError):
            return False
    if field_key in _DATE_FIELDS:
        match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", str(value))
        if not match:
            return False
        try:
            date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return False
    return True


def _decimal_value(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").replace("¥", "").replace("￥", ""))
    except (InvalidOperation, ValueError):
        return None


def _line_item_node(document: dict[str, Any], index: int, key: str) -> dict[str, Any] | None:
    wanted = f"items.{index}.{key}"
    return next(
        (
            node
            for node in list(document.get("field_evidence_nodes") or [])
            if isinstance(node, dict) and str(node.get("field_key") or "") == wanted
        ),
        None,
    )


def _verified_line_item_sum(
    document: dict[str, Any], field_key: str
) -> FieldGateDecision | None:
    """Accept a derived total only when every contributing line is positioned and sums exactly."""
    candidate_keys = _DERIVED_LINE_KEYS.get(field_key)
    fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
    rows = fields.get("items") if isinstance(fields.get("items"), list) else []
    target = _decimal_value(_field_value(document, field_key))
    if not candidate_keys or not rows or target is None:
        return None

    total = Decimal("0")
    evidence_ids: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return None
        key = next((name for name in candidate_keys if row.get(name) not in (None, "")), None)
        if key is None:
            return None
        value = _decimal_value(row.get(key))
        node = _line_item_node(document, index, key)
        if value is None or not node or not node.get("usable_for_decision"):
            return None
        evidence_id = str(node.get("evidence_id") or "")
        if not evidence_id:
            return None
        total += value
        evidence_ids.append(evidence_id)
    if total != target:
        return None
    return FieldGateDecision(
        "SYSTEM_VERIFIED",
        "DERIVED_FROM_VERIFIED_LINE_ITEMS",
        "汇总字段由全部已定位明细行严格加总得到",
        tuple(evidence_ids),
    )


def evaluate_candidate(document: dict[str, Any], field_key: str) -> FieldGateDecision:
    """Evaluate one current candidate without mutating its acceptance state."""
    seed_field_meta(document, source=str(document.get("ocr_source") or "ocr"))
    if document.get("field_evidence_nodes"):
        attach_document_evidence(document, changed_keys={field_key})
    else:
        attach_document_evidence(document)
    nodes = evidence_for_field(document, field_key)
    usable = [node for node in nodes if node.get("usable_for_decision")]
    if not usable:
        derived = _verified_line_item_sum(document, field_key)
        if derived is not None:
            return derived
        reason_code = "EVIDENCE_ANCHOR_MISSING"
        if nodes:
            reason_code = str((nodes[0].get("metadata") or {}).get("reason_code") or reason_code)
        status = "UNLOCATED" if not nodes or all(node.get("anchor_status") == "UNLOCATED" for node in nodes) else "NEEDS_REVIEW"
        return FieldGateDecision(status, reason_code, "字段没有可用于自动决策的原件定位")

    compatible = [node for node in usable if _label_role_compatible(document, field_key, node)]
    if not compatible:
        return FieldGateDecision(
            "ROLE_CONFLICT",
            "DOCUMENT_NUMBER_LABEL_ROLE_CONFLICT",
            "编号所在标签与单据角色或字段角色不兼容",
            tuple(str(node.get("evidence_id") or "") for node in usable),
        )
    value = _field_value(document, field_key)
    if not _typed_value_valid(field_key, value):
        return FieldGateDecision(
            "NEEDS_REVIEW",
            "FIELD_TYPE_OR_RANGE_INVALID",
            "字段未通过确定性类型或范围校验",
            tuple(str(node.get("evidence_id") or "") for node in compatible),
        )
    return FieldGateDecision(
        "SYSTEM_VERIFIED",
        "SOURCE_LOCATED_AND_ROLE_COMPATIBLE",
        "字段在原件中可定位，且标签、单据角色和类型校验一致",
        tuple(str(node.get("evidence_id") or "") for node in compatible),
    )


def accept_system_verified_fields(document: dict[str, Any]) -> list[str]:
    """Accept only candidates that pass the per-field source gate."""
    fields = dict(document.get("fields") or {})
    seed_field_meta(document, fields=fields, source=str(document.get("ocr_source") or "ocr"))
    accepted: list[str] = []
    for field_key, value in fields.items():
        if str(field_key).startswith("_") or field_key == "documentType":
            continue
        decision = evaluate_candidate(document, str(field_key))
        slot = get_field_meta(document).setdefault(str(field_key), {})
        slot["verification_status"] = decision.status
        slot["verification_reason_code"] = decision.reason_code
        slot["verification_reason"] = decision.reason
        slot["evidence_ids"] = list(decision.evidence_ids)
        if decision.status != "SYSTEM_VERIFIED":
            continue
        accept_field(
            document,
            str(field_key),
            value,
            source="system_verified",
            extractor="evidence_gate_v1",
        )
        slot = get_field_meta(document)[str(field_key)]
        slot["verification_status"] = decision.status
        slot["verification_reason_code"] = decision.reason_code
        slot["verification_reason"] = decision.reason
        slot["evidence_ids"] = list(decision.evidence_ids)
        accepted.append(str(field_key))
    return accepted


__all__ = ["FieldGateDecision", "accept_system_verified_fields", "evaluate_candidate"]
