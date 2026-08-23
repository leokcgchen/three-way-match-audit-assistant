"""按底稿目标 + 本笔实际单据类型，算出必填字段（无外标 MESP）。"""

from __future__ import annotations

from typing import Any

from src.legacy_ocr.amount_resolve import _parse_number
from src.workflow.field_catalog import FIELD_LABELS, SYSTEM_REQUIRED

# GOSPD01030 填表/三单/截止真正用到的 key，按本笔已有单据裁剪。
# 入账日来自裁剪序时账，不挡字段确认。
_GOSPD01030_BY_TYPE: dict[str, tuple[str, ...]] = {
    "contract": ("contractNo", "buyerName"),
    "order": ("orderNo", "contractNo", "buyerName", "quantity", "totalAmount"),
    "invoice": ("invoiceNo", "buyerName", "totalAmount", "documentDate"),
    "receipt": ("acceptanceDate", "quantity", "orderNo"),
    "delivery": ("deliveryDate", "quantity", "orderNo"),
    "payment": ("documentDate", "totalAmount"),
}


def _doc_type(doc: dict[str, Any]) -> str:
    return str(doc.get("doc_type") or "other").strip() or "other"


def _field_value(doc: dict[str, Any], key: str) -> Any:
    fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
    return fields.get(key)


_AMOUNT_KEYS = frozenset({"amount", "taxAmount", "totalAmount"})


def _is_filled(value: Any, key: str = "") -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan", "-"}:
        return False
    if key in _AMOUNT_KEYS:
        num = _parse_number(value)
        return num is not None and float(num) > 0
    return True


def required_fields_for_docs(
    docs: list[dict[str, Any]],
    *,
    goal_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """返回本笔必填项：key / label / filled / source_types。"""
    goals = set(goal_ids or [])
    only_01030 = goals == {"gospd01030"} or not goals
    use_01030 = "gospd01030" in goals or not goals
    present_types = {_doc_type(d) for d in docs if _doc_type(d) != "other"}
    keys: list[str] = []

    def add(key: str) -> None:
        if key and key not in keys:
            keys.append(key)

    for dt in present_types:
        if only_01030:
            for key in _GOSPD01030_BY_TYPE.get(dt, ()):
                add(key)
        else:
            for key in SYSTEM_REQUIRED.get(dt, ()):
                add(key)
            if use_01030:
                for key in _GOSPD01030_BY_TYPE.get(dt, ()):
                    add(key)

    rows: list[dict[str, Any]] = []
    for key in keys:
        sources = [
            dt
            for dt in present_types
            if key in SYSTEM_REQUIRED.get(dt, ()) or key in _GOSPD01030_BY_TYPE.get(dt, ())
        ]
        filled = any(_is_filled(_field_value(d, key), key) for d in docs)
        rows.append(
            {
                "key": key,
                "label": FIELD_LABELS.get(key, key),
                "filled": filled,
                "source_types": sources,
            }
        )
    return rows


def missing_required_fields(docs: list[dict[str, Any]], *, goal_ids: list[str] | None = None) -> list[str]:
    return [r["key"] for r in required_fields_for_docs(docs, goal_ids=goal_ids) if not r["filled"]]
