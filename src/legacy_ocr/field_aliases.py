"""字段同义词 / 表头别名归一。

启发式常只认「数量」「发货数量」等少数词，真实单据常见「发运数量/实收数量/
合格数量」「签收/验收完成日期」等。本模块在规则层把别名收敛到管线标准键，
再交给 LLM 补缺；禁止把缺字段默认为 0 后当成业务差异。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _normalize_qty_token(raw: Any) -> Optional[str]:
    from src.legacy_ocr.field_normalize import normalize_quantity_token

    return normalize_quantity_token(raw)

# 数量：优先实收/合格，再发运/发货，再通用 quantity
QUANTITY_FIELD_KEYS: Tuple[str, ...] = (
    "quantity",
    "deliveredQuantity",
    "delivered_quantity",
    "receivedQuantity",
    "received_quantity",
    "acceptedQuantity",
    "accepted_quantity",
    "shippedQuantity",
    "shipped_quantity",
    "qty",
    "实收数量",
    "合格数量",
    "发运数量",
    "发货数量",
    "交货数量",
    "装船数量",
    "提单数量",
)

# 金额：价税合计优先
AMOUNT_FIELD_KEYS: Tuple[str, ...] = (
    "totalAmount",
    "amountInclTax",
    "grandTotal",
    "价税合计",
    "含税总金额",
    "合计金额",
    "总金额",
)

NET_AMOUNT_FIELD_KEYS: Tuple[str, ...] = (
    "amount",
    "netAmount",
    "amountExclTax",
    "未税金额",
    "不含税金额",
)

# 购销方
SUPPLIER_FIELD_KEYS: Tuple[str, ...] = (
    "supplierName",
    "sellerName",
    "vendorName",
    "销售方名称",
    "销方名称",
    "供应商名称",
    "供应商",
)
BUYER_FIELD_KEYS: Tuple[str, ...] = (
    "buyerName",
    "customerName",
    "购方名称",
    "购买方名称",
    "客户名称",
    "收货单位",
)

# 日期类（签收侧）
ACCEPTANCE_DATE_KEYS: Tuple[str, ...] = (
    "acceptanceDate",
    "receiptDateForCutoff",
    "签收日期",
    "验收日期",
    "签收/验收完成日期",
    "验收完成日期",
)
ARRIVAL_DATE_KEYS: Tuple[str, ...] = (
    "deliveryDate",
    "到货日期",
    "发货日期",
    "交货日期",
    "入库日期",
)


def _first_present(fields: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        val = fields.get(key)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        return val
    return None


def pick_quantity_value(fields: Optional[Dict[str, Any]]) -> Optional[float]:
    """从标准键与中文别名中取数量；取不到返回 None（勿默认为 0）。"""
    if not isinstance(fields, dict):
        return None
    for key in QUANTITY_FIELD_KEYS:
        raw = fields.get(key)
        if raw is None or (isinstance(raw, str) and not str(raw).strip()):
            continue
        token = _normalize_qty_token(raw)
        if token is None:
            continue
        try:
            num = float(token)
        except ValueError:
            continue
        if num > 0:
            return num
    # items 行合计
    items = fields.get("items")
    if isinstance(items, list) and items:
        total = 0.0
        hit = False
        for row in items:
            if not isinstance(row, dict):
                continue
            q = pick_quantity_value(row)
            if q:
                total += q
                hit = True
        if hit and total > 0:
            return total
    return None


def pick_amount_value(
    fields: Optional[Dict[str, Any]], *, keys: Sequence[str] = AMOUNT_FIELD_KEYS
) -> Optional[float]:
    if not isinstance(fields, dict):
        return None
    for key in keys:
        raw = fields.get(key)
        if raw is None or (isinstance(raw, str) and not str(raw).strip()):
            continue
        token = _normalize_qty_token(raw)
        if token is None:
            continue
        try:
            num = float(token)
        except ValueError:
            continue
        if num > 0:
            return num
    return None


def coalesce_field_aliases(fields: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把别名收敛到管线标准键（不覆盖已有非空标准键）。"""
    out: Dict[str, Any] = dict(fields or {})
    repairs: List[Dict[str, Any]] = list(out.get("_fieldRepairs") or [])

    def _fill(std_key: str, value: Any, rule: str) -> None:
        if value is None:
            return
        cur = out.get(std_key)
        if cur is not None and str(cur).strip():
            return
        out[std_key] = value
        repairs.append(
            {
                "path": std_key,
                "before": None,
                "after": str(value),
                "rule": rule,
            }
        )

    qty = pick_quantity_value(out)
    if qty is not None:
        _fill("quantity", str(qty) if float(qty).is_integer() else qty, "alias_quantity")

    amt = pick_amount_value(out, keys=AMOUNT_FIELD_KEYS)
    if amt is not None:
        _fill("totalAmount", amt, "alias_total_amount")

    net = pick_amount_value(out, keys=NET_AMOUNT_FIELD_KEYS)
    if net is not None:
        _fill("amount", net, "alias_net_amount")

    supplier = _first_present(out, SUPPLIER_FIELD_KEYS)
    if supplier is not None:
        _fill("supplierName", str(supplier).strip(), "alias_supplier")

    buyer = _first_present(out, BUYER_FIELD_KEYS)
    if buyer is not None:
        _fill("buyerName", str(buyer).strip(), "alias_buyer")

    acc = _first_present(out, ACCEPTANCE_DATE_KEYS)
    if acc is not None:
        _fill("acceptanceDate", str(acc).strip(), "alias_acceptance_date")

    arr = _first_present(out, ARRIVAL_DATE_KEYS)
    if arr is not None:
        _fill("deliveryDate", str(arr).strip(), "alias_delivery_date")

    if repairs:
        out["_fieldRepairs"] = repairs
    return out


_QTY_HEADER_ALIASES = (
    "实收数量",
    "合格数量",
    "发运数量",
    "发货数量",
    "交货数量",
    "装船数量",
    "提单数量",
    "数量",
)


def parse_quantity_from_delivery_table(text: str) -> Optional[float]:
    """从签收/发货 Markdown 或类表格 OCR 文本解析交付数量。

    兼容表头：发运/发货/实收/合格数量；优先实收→合格→发运/发货→数量。
    """
    if not text or not str(text).strip():
        return None
    raw = str(text)
    lines = raw.splitlines()

    for i, line in enumerate(lines):
        if "数量" not in line:
            continue
        # 表头行：至少命中一个交付数量同义词
        if not any(alias in line for alias in _QTY_HEADER_ALIASES):
            continue
        cells_h = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells_h) < 2:
            continue
        # 按优先级选列
        col_idx: Optional[int] = None
        for prefer in (
            "实收数量",
            "合格数量",
            "发运数量",
            "发货数量",
            "交货数量",
            "装船数量",
            "提单数量",
            "数量",
        ):
            for j, h in enumerate(cells_h):
                if h == prefer or (prefer != "数量" and prefer in h):
                    # 「数量」列避免误吃「差异数量」
                    if prefer == "数量" and ("差异" in h or "差额" in h):
                        continue
                    if "差异" in h:
                        continue
                    col_idx = j
                    break
            if col_idx is not None:
                break
        if col_idx is None:
            continue

        for row in lines[i + 1 : i + 12]:
            if not row.strip().startswith("|") or re.search(r"\|\s*---", row):
                continue
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if col_idx >= len(cells):
                continue
            token = _normalize_qty_token(cells[col_idx])
            if not token:
                continue
            try:
                num = float(token)
            except ValueError:
                continue
            if num > 0:
                return num

    # 行内「实收数量：48」等
    for label in ("实收数量", "合格数量", "发运数量", "发货数量", "交货数量"):
        m = re.search(
            rf"{label}\s*[:：]?\s*(\d+(?:\.\d+)?)",
            raw,
        )
        if m:
            return float(m.group(1))

    m = re.search(
        r"(?:装船数量|提单数量)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:件|PCS|pcs)?",
        raw,
        flags=re.I,
    )
    if m:
        return float(m.group(1))
    return None


def enrich_fields_from_text_aliases(
    fields: Optional[Dict[str, Any]],
    ocr_text: str = "",
) -> Dict[str, Any]:
    """文本表头别名补缺 → 标准键，再做字典别名收敛。"""
    out = dict(fields or {})
    if not out.get("quantity") and ocr_text:
        q = parse_quantity_from_delivery_table(ocr_text)
        if q:
            out["quantity"] = str(int(q)) if float(q).is_integer() else q
            out.setdefault("deliveredQuantity", out["quantity"])
            repairs = list(out.get("_fieldRepairs") or [])
            repairs.append(
                {
                    "path": "quantity",
                    "before": None,
                    "after": str(out["quantity"]),
                    "rule": "table_alias_quantity",
                }
            )
            out["_fieldRepairs"] = repairs
    return coalesce_field_aliases(out)
