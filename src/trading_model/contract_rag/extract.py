from __future__ import annotations

from typing import Any, Callable, Optional

ALLOWED = {"EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP", "UNKNOWN"}

SYSTEM = "只输出合法 JSON 对象，不要 Markdown。evidence 必须是给定段落的原文子串。"

USER_TEMPLATE = """只根据下列合同段落识别书面贸易/运输术语标签（Incoterms）。
这是合同上的写法，不是实际履约结论。不要编造段落中没有的术语。若无法判断，trade_mode 填 unknown。

段落：
{paragraphs}

只输出：
{{"trade_mode":"FOB","confidence":0.95,"evidence":"paragraph text..."}}
"""


def extract_trade_mode(
    hits: list[dict[str, Any]],
    *,
    llm_fn: Optional[Callable[[str], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Read the written Incoterms label from retrieved contract paragraphs.

    Experimental helper only. Does not judge actual fulfillment.
    """
    paragraphs = "\n\n".join(
        f"[{row.get('seq')}] {row.get('raw_text')}" for row in hits
    ) or "（无检索结果）"
    prompt = USER_TEMPLATE.format(paragraphs=paragraphs)
    if llm_fn is not None:
        raw = llm_fn(prompt) or {}
    else:
        from src.trading_model.llm_deepseek import complete_json, deepseek_available
        from src.trading_model.llm_json import live_llm_enabled

        if not live_llm_enabled() or not deepseek_available():
            return {
                "trade_mode": "unknown",
                "confidence": 0.0,
                "evidence": "",
                "skipped": "no_llm",
            }
        raw = complete_json(prompt, system=SYSTEM)
    mode = str(raw.get("trade_mode") or "unknown").upper()
    if mode not in ALLOWED:
        mode = "UNKNOWN"
    evidence = str(raw.get("evidence") or "")
    blob = "\n".join(str(row.get("raw_text") or "") for row in hits)
    if evidence and evidence not in blob:
        evidence = ""
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    if mode != "UNKNOWN" and not evidence:
        confidence = min(confidence, 0.4)
    return {
        "trade_mode": mode if mode != "UNKNOWN" else "unknown",
        "confidence": confidence,
        "evidence": evidence,
        "model_id": raw.get("_model_id"),
    }
