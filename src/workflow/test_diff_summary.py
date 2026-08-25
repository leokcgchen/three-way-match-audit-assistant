"""从三单/截止结果抽出可展示的左右值差异（禁止只写 FAIL）。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_RULES_PATH = _ROOT / "config" / "mes_match_rules.v1.json"

_FIELD_CN = {
    "supplier_name": "客户名称",
    "supplier": "客户名称",
    "buyer_name": "购方",
    "total_amount": "价税合计",
    "amount": "金额",
    "quantity": "数量（订单 / 签收验收 / 发票开票）",
    "qty": "数量（订单 / 签收验收 / 发票开票）",
}


@lru_cache(maxsize=1)
def load_match_rule_catalog() -> dict[str, Any]:
    try:
        return json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": "0", "rules": []}


def list_match_rules() -> list[dict[str, Any]]:
    cat = load_match_rule_catalog()
    return [r for r in (cat.get("rules") or []) if isinstance(r, dict)]


def _field_cn(raw: Any) -> str:
    key = str(raw or "字段")
    return _FIELD_CN.get(key) or _FIELD_CN.get(key.lower()) or key


def _cmp_line(c: dict[str, Any]) -> str:
    field = _field_cn(c.get("field_name") or c.get("field"))
    is_qty = str(c.get("field_name") or c.get("field") or "").lower() in {
        "quantity",
        "qty",
    }
    bits = []
    if c.get("order_value") not in (None, ""):
        bits.append(
            f"{'订单数量' if is_qty else '订单'} {c.get('order_value')}"
        )
    if c.get("receipt_value") not in (None, ""):
        bits.append(
            f"{'签收/验收数量' if is_qty else '签收/验收'} {c.get('receipt_value')}"
        )
    if c.get("delivery_value") not in (None, ""):
        bits.append(f"发货 {c.get('delivery_value')}")
    if c.get("invoice_value") not in (None, ""):
        bits.append(
            f"{'发票开票数量' if is_qty else '发票'} {c.get('invoice_value')}"
        )
    from src.three_way_match.phrases import expand_qty_role_shorthand

    msg = expand_qty_role_shorthand(
        str(c.get("diff_description") or c.get("message") or c.get("auditor_explain") or "")
    ).strip()
    if bits:
        line = f"{field}：{' vs '.join(bits)}"
        return f"{line}（{msg}）" if msg else line
    if msg:
        return f"{field}：{msg}"
    return field


def _is_bad_cmp(c: dict[str, Any]) -> bool:
    if c.get("is_consistent") is False:
        return True
    st = str(c.get("status") or "").upper()
    return "FAIL" in st or "WARN" in st or "不" in st


def _three_way_blob(sample: dict[str, Any]) -> dict[str, Any]:
    twm = sample.get("three_way_match")
    if isinstance(twm, dict) and twm:
        return twm
    legacy = sample.get("three_way") if isinstance(sample.get("three_way"), dict) else {}
    return legacy or {}


def _cutoff_blob(sample: dict[str, Any]) -> dict[str, Any]:
    cut = sample.get("cutoff_test")
    if isinstance(cut, dict) and cut:
        return cut
    legacy = sample.get("three_way") if isinstance(sample.get("three_way"), dict) else {}
    if not legacy:
        return {}
    return {
        "status": legacy.get("cutoff_test_status") or legacy.get("cutoff_status"),
        "问题描述": (legacy.get("cutoff_result") or {}).get("问题描述")
        if isinstance(legacy.get("cutoff_result"), dict)
        else legacy.get("cutoff_skipped_reason"),
        "result": legacy.get("cutoff_result"),
    }


def sample_diff_lines(sample: dict[str, Any] | None, *, limit: int = 5) -> list[str]:
    """本笔测试失败时的可读差异行。"""
    if not isinstance(sample, dict):
        return []
    lines: list[str] = []
    tw = _three_way_blob(sample)
    fc = tw.get("field_consistency") if isinstance(tw.get("field_consistency"), dict) else {}
    comparisons = list(fc.get("comparisons") or tw.get("comparisons") or [])
    if not comparisons:
        mr = tw.get("match_result") if isinstance(tw.get("match_result"), dict) else {}
        comparisons = list(mr.get("comparisons") or [])
    for c in comparisons:
        if not isinstance(c, dict) or not _is_bad_cmp(c):
            continue
        lines.append(_cmp_line(c))
        if len(lines) >= limit:
            return lines

    fulfillment = (
        tw.get("fulfillment") if isinstance(tw.get("fulfillment"), dict) else {}
    )
    fulfillment_flags = list(fulfillment.get("flags") or [])
    if fulfillment_flags:
        flag_labels = {
            "STRONG_MODEL_CONFLICT": "规格型号冲突",
            "PARTIAL_FULFILLMENT": "累计签收不足",
            "OVER_INVOICE_QTY": "开票数量超过累计签收数量",
            "OVER_RECEIPT_QTY": "累计签收数量超过订单数量",
            "DUPLICATE_ALLOCATION": "存在重复分配",
            "AMBIGUOUS_ALLOCATION": "货物行归属存在歧义",
        }
        details = [flag_labels.get(str(flag), str(flag)) for flag in fulfillment_flags]
        summary = str(fulfillment.get("summary") or "").strip()
        line = "履约/货物：" + "；".join(dict.fromkeys(details))
        lines.append(f"{line}（{summary}）" if summary else line)
        if len(lines) >= limit:
            return lines

    binding = tw.get("document_binding") if isinstance(tw.get("document_binding"), dict) else {}
    if str(binding.get("status") or "").upper() == "FAIL" and binding.get("reason"):
        lines.append(str(binding.get("reason")))

    cut = _cutoff_blob(sample)
    cut_st = str(cut.get("status") or cut.get("测试状态") or "").upper()
    if "FAIL" in cut_st or "未通过" in cut_st:
        result = cut.get("result") if isinstance(cut.get("result"), dict) else cut
        ctrl = (
            result.get("控制权转移日")
            or result.get("acceptance_date")
            or result.get("签收日期")
            or result.get("delivery_date")
        )
        post = result.get("入账日期") or result.get("posting_date") or result.get("记账日期")
        issue = str(result.get("问题描述") or cut.get("问题描述") or "").strip()
        if ctrl not in (None, "") and post not in (None, ""):
            line = f"截止：签收/控制权 {ctrl} vs 入账 {post}"
            lines.append(f"{line}（{issue}）" if issue else line)
        elif issue:
            lines.append(f"截止：{issue}")
        else:
            lines.append("截止性测试未通过（详见结论页）")

    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= limit:
            break
    return out
