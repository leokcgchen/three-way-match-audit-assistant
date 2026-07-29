"""字段归一化（移植自 fieldNormalize.ts 的核心规则，精简版）。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from src.legacy_ocr.amount_resolve import apply_amount_fields


def to_ascii_digits(raw: str) -> str:
    out = []
    for ch in str(raw or ""):
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19:
            out.append(chr(code - 0xFF10 + 0x30))
        elif ch in {"．", "。"}:
            out.append(".")
        elif ch == "，":
            out.append(",")
        else:
            out.append(ch)
    return "".join(out).strip()


def normalize_quantity_token(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = to_ascii_digits(str(raw))
    if not s:
        return None
    spaced = re.match(r"^(\d{1,3})\s+(\d{1,3})(?:\.\d+)?$", s)
    if spaced:
        return f"{spaced.group(1)}{spaced.group(2)}"
    if re.match(r"^\d{1,3}(,\d{3})+(\.\d+)?$", s):
        s = s.replace(",", "")
    s = re.sub(r"(台|支|个|件|箱|套|千克|公斤|kg|pcs?)$", "", s, flags=re.I).strip()
    m = re.match(r"^(\d+(?:\.\d+)?)", s)
    return m.group(1) if m else None


def normalize_document_date_token(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = to_ascii_digits(str(raw)).replace("/", "-").replace(".", "-")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if not m:
        # 中文日期
        m2 = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$", to_ascii_digits(str(raw)))
        if not m2:
            return str(raw).strip() or None
        y, mo, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
    else:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not y or mo < 1 or mo > 12 or d < 1 or d > 31:
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _parse_number(raw: Any) -> Optional[float]:
    q = normalize_quantity_token(raw)
    if q is None:
        return None
    try:
        return float(q)
    except ValueError:
        return None


def normalize_extracted_fields(
    input_fields: Optional[Dict[str, Any]],
    ocr_raw_text: str = "",
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """归一化提取字段；返回 (fields, repairs)。"""
    fields: Dict[str, Any] = dict(input_fields or {})
    repairs: List[Dict[str, Any]] = []

    for date_key in (
        "documentDate",
        "deliveryDate",
        "acceptanceDate",
        "receiptDateForCutoff",
        "postingDate",
    ):
        if fields.get(date_key):
            before = str(fields[date_key])
            after = normalize_document_date_token(before)
            if after and after != before:
                fields[date_key] = after
                repairs.append(
                    {
                        "path": date_key,
                        "before": before,
                        "after": after,
                        "rule": "pad_or_reject_date",
                    }
                )
            elif after:
                fields[date_key] = after

    if fields.get("quantity") is not None:
        before = str(fields.get("quantity"))
        after = normalize_quantity_token(before)
        if after and after != before:
            fields["quantity"] = after
            repairs.append(
                {
                    "path": "quantity",
                    "before": before,
                    "after": after,
                    "rule": "glue_split_digits",
                }
            )

    items = fields.get("items")
    if isinstance(items, list):
        fixed = []
        for item in items:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            qty_before = row.get("quantity")
            qty = normalize_quantity_token(qty_before)
            unit_price = _parse_number(row.get("unitPrice"))
            amount = _parse_number(row.get("amount"))
            if (
                unit_price
                and unit_price > 0
                and amount
                and amount > 0
                and (qty is None or abs(float(qty) * unit_price - amount) / amount > 0.08)
            ):
                implied = amount / unit_price
                qty = str(round(implied) if abs(implied - round(implied)) < 0.05 else round(implied, 2))
                repairs.append(
                    {
                        "path": f"items[name={str(row.get('name', ''))[:24]}].quantity",
                        "before": None if qty_before is None else str(qty_before),
                        "after": qty,
                        "rule": "qty_from_amount_div_unit_price",
                    }
                )
            elif qty is not None:
                row["quantity"] = qty
            fixed.append(row)
        if fixed:
            fields["items"] = fixed
            item_sum = sum(_parse_number(it.get("quantity")) or 0 for it in fixed)
            top = _parse_number(fields.get("quantity"))
            if item_sum > 0 and (top is None or abs(top - item_sum) / max(top or item_sum, item_sum) > 0.25):
                repairs.append(
                    {
                        "path": "quantity",
                        "before": None if fields.get("quantity") is None else str(fields.get("quantity")),
                        "after": str(item_sum),
                        "rule": "top_quantity_from_sum_of_line_items",
                    }
                )
                fields["quantity"] = str(item_sum)

    fields = apply_amount_fields(fields, ocr_raw_text)

    if repairs:
        fields["_fieldRepairs"] = repairs
    return fields, repairs
