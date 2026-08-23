"""三单数量/金额角色的可读文案（禁止只写「订/收/开」；禁止展示匹配得分）。"""

from __future__ import annotations

import re
from typing import Any


def _num(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return f"{f:g}"
    except (TypeError, ValueError):
        return str(v)


def quantity_roles_phrase(
    ordered: Any,
    received: Any,
    invoiced: Any,
    *,
    sep: str = "，",
) -> str:
    """订单数量 / 签收或验收数量 / 发票开票数量。"""
    return (
        f"订单数量 {_num(ordered)}"
        f"{sep}签收/验收数量 {_num(received)}"
        f"{sep}发票开票数量 {_num(invoiced)}"
    )


def amount_roles_phrase(
    ordered: Any,
    received: Any,
    invoiced: Any,
    *,
    sep: str = "，",
) -> str:
    return (
        f"订单金额 {_num(ordered)}"
        f"{sep}签收/验收金额 {_num(received)}"
        f"{sep}发票金额 {_num(invoiced)}"
    )


def strip_match_score_language(text: str) -> str:
    """去掉旧版三单「得分/匹配分」展示残留（引擎已不以得分放行）。"""
    s = str(text or "")
    if not s:
        return s
    s = re.sub(
        r"[，,]?\s*(?:三单)?匹配得分\s*[:：]?\s*\d+(?:\.\d+)?\s*分?",
        "",
        s,
    )
    s = re.sub(
        r"[，,]?\s*得分\s*[:：]?\s*\d+(?:\.\d+)?\s*分?",
        "",
        s,
    )
    s = re.sub(
        r"（得分\s*\d+(?:\.\d+)?\s*分?）",
        "",
        s,
    )
    s = re.sub(
        r"\(\s*得分\s*\d+(?:\.\d+)?\s*\)",
        "",
        s,
    )
    s = re.sub(r"三单匹配通过，\s*", "三单匹配通过：", s)
    s = re.sub(r"三单匹配失败，\s*", "三单匹配失败：", s)
    s = re.sub(r"三单匹配需关注，\s*", "三单匹配需关注：", s)
    s = re.sub(r"[，,]{2,}", "，", s)
    s = re.sub(r"：\s*：", "：", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ，,;；")


def expand_qty_role_shorthand(text: str) -> str:
    """把历史结果里残留的「订/收/开」扩成完整中文（展示层兜底）。"""
    s = strip_match_score_language(str(text or ""))
    if not s:
        return s
    s = s.replace("数量（订/收/开分槽）", "数量（订单数量、签收/验收数量、发票开票数量，分角色对照）")
    s = s.replace("数量（订/收/开）", "数量（订单数量、签收/验收数量、发票开票数量）")
    s = s.replace("订/收/开分槽", "订单/签收验收/发票开票分角色")
    s = s.replace("订/收/开三方", "订单、签收/验收、发票三方")
    s = s.replace("订/收/开", "订单、签收/验收、发票开票")
    s = re.sub(
        r"订\s*([\d.]+)\s*/\s*收\s*([\d.]+)\s*/\s*开\s*([\d.]+)",
        r"订单数量 \1，签收/验收数量 \2，发票开票数量 \3",
        s,
    )
    s = re.sub(
        r"数量订\s*([\d.]+)\s*/\s*收\s*([\d.]+)\s*/\s*开\s*([\d.]+)",
        r"数量：订单 \1，签收/验收 \2，发票开票 \3",
        s,
    )
    s = re.sub(
        r"(?<![订签发])订\s+([\d.—\-]+)\s*vs\s*收\s+([\d.—\-]+)\s*vs\s*开\s+([\d.—\-]+)",
        r"订单数量 \1 vs 签收/验收数量 \2 vs 发票开票数量 \3",
        s,
    )
    s = re.sub(r"(?<![订签发购])订\s+([\d.—\-]+)", r"订单数量 \1", s)
    s = re.sub(r"(?<![签验])收\s+([\d.—\-]+)", r"签收/验收数量 \1", s)
    s = re.sub(r"(?<![发开])开\s+([\d.—\-]+)", r"发票开票数量 \1", s)
    return s
