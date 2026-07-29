"""OCR 金额智能提取与万元归一化。"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

WAN_YUAN_THRESHOLD = 10000.0

TOTAL_AMOUNT_LABELS = (
    "价税合计",
    "含税总金额",
    "合计金额",
    "总金额",
    "金额合计",
    "totalamount",
    "grand total",
)

NET_AMOUNT_LABELS = (
    "未税金额",
    "不含税金额",
    "未税小计",
    "金额",
    "amount",
)


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


def _parse_number(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    s = to_ascii_digits(str(raw))
    if not s:
        return None
    s = s.replace(",", "").replace("¥", "").replace("￥", "").strip()
    m = re.match(r"^(\d+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def to_wan_yuan(value: float) -> float:
    """>10000 视为元并转万元；否则视为已是万元。"""
    if value > WAN_YUAN_THRESHOLD:
        return round(value / WAN_YUAN_THRESHOLD, 6)
    return round(value, 6)


def _first_amount_in_text(ocr_text: str, labels: Tuple[str, ...]) -> Optional[float]:
    if not ocr_text:
        return None
    for label in labels:
        pattern = (
            rf"{re.escape(label)}\s*[:：]?\s*[¥￥$]?\s*([\d,]+(?:\.\d{{1,2}})?)"
        )
        m = re.search(pattern, ocr_text, flags=re.I)
        if m:
            val = _parse_number(m.group(1).replace(",", ""))
            if val is not None and val > 0:
                return val
    return None


def _parse_tax_rate(fields: Dict[str, Any], ocr_text: str) -> Optional[float]:
    for raw in (fields.get("taxRate"), fields.get("税率")):
        if raw is None:
            continue
        text = to_ascii_digits(str(raw)).replace("%", "").strip()
        try:
            num = float(text)
            return num / 100.0 if num > 1 else num
        except ValueError:
            continue
    if ocr_text:
        m = re.search(r"税率\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%?", ocr_text, flags=re.I)
        if m:
            num = float(m.group(1))
            return num / 100.0 if num > 1 else num
    # 备注常见「税率13%」
    remarks = str(fields.get("remarks") or "")
    m = re.search(r"税率\s*(\d+(?:\.\d+)?)\s*%", remarks)
    if m:
        return float(m.group(1)) / 100.0
    return None


def _sum_line_items(fields: Dict[str, Any]) -> Optional[float]:
    items = fields.get("items")
    if not isinstance(items, list):
        return None
    total = 0.0
    has_any = False
    for row in items:
        if not isinstance(row, dict):
            continue
        qty = _parse_number(row.get("quantity"))
        price = _parse_number(row.get("unitPrice"))
        if qty and price and qty > 0 and price > 0:
            total += qty * price
            has_any = True
            continue
        amt = _parse_number(row.get("amount"))
        if amt and amt > 0:
            total += amt
            has_any = True
    return total if has_any else None


def _top_qty_times_price(fields: Dict[str, Any]) -> Optional[float]:
    qty = _parse_number(fields.get("quantity"))
    for key in ("unitPrice", "price", "未税单价"):
        price = _parse_number(fields.get(key))
        if qty and price and qty > 0 and price > 0:
            return qty * price
    return None


def resolve_total_amount_wan(
    fields: Dict[str, Any],
    ocr_text: str = "",
) -> Tuple[Optional[float], Optional[str]]:
    """
    按优先级解析总金额（万元）。

    1. 价税合计 / 含税总金额 / 合计金额
    2. 未税金额 × (1 + 税率)
    3. 数量 × 单价（行项目汇总或顶层）
    4. 均不可得 → None
    """
    # 1) 字段或 OCR 文本中的含税合计
    for key in ("totalAmount", "价税合计", "含税总金额", "合计金额"):
        val = _parse_number(fields.get(key))
        if val is not None and val > 0:
            return to_wan_yuan(val), f"field:{key}"
    from_text = _first_amount_in_text(ocr_text, TOTAL_AMOUNT_LABELS)
    if from_text is not None:
        return to_wan_yuan(from_text), "ocr:含税合计"

    # 2) 未税 × (1+税率)
    net = None
    for key in ("amount", "未税金额", "不含税金额", "未税小计"):
        val = _parse_number(fields.get(key))
        if val is not None and val > 0:
            net = val
            break
    if net is None:
        net = _first_amount_in_text(ocr_text, NET_AMOUNT_LABELS)
    tax_amount = _parse_number(fields.get("taxAmount"))
    rate = _parse_tax_rate(fields, ocr_text)
    if net is not None and net > 0:
        if tax_amount is not None and tax_amount > 0:
            gross = net + tax_amount
            return to_wan_yuan(gross), "calc:未税+税额"
        if rate is not None:
            gross = net * (1.0 + rate)
            return to_wan_yuan(gross), "calc:未税×(1+税率)"

    # 3) 数量 × 单价
    computed = _sum_line_items(fields)
    if computed is None:
        computed = _top_qty_times_price(fields)
    if computed is not None and computed > 0:
        return to_wan_yuan(computed), "calc:数量×单价"

    return None, None


def apply_amount_fields(fields: Dict[str, Any], ocr_text: str = "") -> Dict[str, Any]:
    """写入 totalAmount（万元）或标记缺失，不填默认值。"""
    out = dict(fields)
    amount_wan, rule = resolve_total_amount_wan(out, ocr_text)
    if amount_wan is not None:
        out["totalAmount"] = str(amount_wan)
        out["_amountSource"] = rule
        out["_amountUnit"] = "万元"
        out.pop("_totalAmountMissing", None)
    else:
        out.pop("totalAmount", None)
        out["_totalAmountMissing"] = True
    return out
