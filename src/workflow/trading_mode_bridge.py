"""贸易模式判断桥：对接 src.trading_model，供底稿/截止取数。

纪律（对齐交接包硬约束）：
- 底稿投影仅三键：trading_mode_conclusion / status / confidence
- 默认不调 LLM；LLM 不得写入三键
- 禁止 ingest 与 use_llm 同开
- 名义 FOB/CIF 标签不得单独当实际履约结论
- 外销 P 列优先 On Board Date，不得用仓库签收日冒充
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EXPORT_ON_BOARD_TERMS = {
    "外销-FOB离岸价格",
    "外销-CIF成本加保险费、运费",
    "外销-CIP运费、保险费付至指定目的地",
    "外销-FCA货交承运人",
}


def trading_model_llm_enabled() -> bool:
    return str(os.getenv("TRADING_MODEL_LIVE_LLM") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _docs_for_interpret(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, item in enumerate(classified or []):
        if item.get("excluded_from_match"):
            continue
        raw = (
            item.get("raw_text")
            or item.get("ocr_text")
            or item.get("text")
            or ""
        )
        fields = dict(item.get("fields") or {})
        # 字段槽只能当别名线索，拼进原文供 harvest；不得单独当结论
        extras = []
        for key in ("transportTerms", "controlTransferTerms", "paymentTerms"):
            val = fields.get(key)
            if val and str(val).strip():
                extras.append(f"{key}: {val}")
        blob = str(raw or "").strip()
        if extras:
            blob = (blob + "\n" + "\n".join(extras)).strip()
        out.append(
            {
                "document_id": item.get("document_id") or f"DOC-{i + 1}",
                "doc_type": item.get("doc_type") or item.get("document_type") or "other",
                "document_type": item.get("doc_type") or item.get("document_type") or "other",
                "file_name": item.get("file_name") or "",
                "raw_text": blob,
                "text_blocks": item.get("text_blocks") or [],
                "fields": fields,
            }
        )
    return out


def interpret_chain_trading_mode(
    classified: list[dict[str, Any]],
    *,
    transaction_id: str,
    use_llm: Optional[bool] = None,
    persist: bool = False,
    data_root: Any = None,
) -> dict[str, Any]:
    """对一笔业务的 classified[] 跑贸易模式判断。

    返回结构供底稿/截止使用；失败时返回 empty=True，不抛到导出门禁。
    """
    docs = _docs_for_interpret(classified)
    if not docs:
        return {
            "empty": True,
            "workbook_view": {
                "trading_mode_conclusion": "无法判断实际贸易模式，请按切段证据复核",
                "status": "insufficient_evidence",
                "confidence": "no_conclusion",
            },
            "gospd_cells": {},
            "control": {},
            "classification": {},
        }

    llm = trading_model_llm_enabled() if use_llm is None else bool(use_llm)
    # 硬约束：ingest 与 judge 不得同开；导出路只 judge
    try:
        from src.trading_model.gospd01030 import project_gospd01030
        from src.trading_model.interpret import interpret_trading_model

        workbook_view, artifact = interpret_trading_model(
            classified=docs,
            transaction_id=transaction_id,
            use_llm=llm,
            persist=persist,
            ingest=False,
            data_root=data_root,
        )
    except Exception as exc:
        logger.exception("trading_model interpret failed tx=%s", transaction_id)
        return {
            "empty": True,
            "error": str(exc),
            "workbook_view": {
                "trading_mode_conclusion": "无法判断实际贸易模式，请按切段证据复核",
                "status": "insufficient_evidence",
                "confidence": "no_conclusion",
            },
            "gospd_cells": {},
            "control": {},
            "classification": {},
        }

    classification = dict(artifact.get("classification") or {})
    control = dict(
        artifact.get("control_transfer_assessment")
        or artifact.get("control")
        or {}
    )
    cells = project_gospd01030(
        classification=classification,
        control=control,
        documents=list(artifact.get("documents") or docs),
        missing_documents=list(artifact.get("missing_documents") or []),
    )
    # 契约：三键不得被 LLM 句子污染；interpret 已保证，这里再锁一次键集
    view = {
        "trading_mode_conclusion": str(
            (workbook_view or {}).get("trading_mode_conclusion") or ""
        ),
        "status": str((workbook_view or {}).get("status") or ""),
        "confidence": str((workbook_view or {}).get("confidence") or ""),
    }
    return {
        "empty": False,
        "workbook_view": view,
        "gospd_cells": cells,
        "control": control,
        "classification": classification,
        "llm_advisory": (artifact.get("llm") or {}).get("advisory"),
        "artifact_tx": artifact.get("transaction_id") or transaction_id,
    }


def e13_from_trading_mode(result: dict[str, Any] | None) -> str:
    if not result or result.get("empty"):
        return ""
    cells = result.get("gospd_cells") or {}
    return str(cells.get("E13_transport_terms") or "").strip()


def control_date_for_cutoff(result: dict[str, Any] | None) -> tuple[str, str]:
    """返回 (控制权日, 日期含义说明)。外销装船日优先；无则空。"""
    if not result or result.get("empty"):
        return "", ""
    cells = result.get("gospd_cells") or {}
    term = str(cells.get("E13_transport_terms") or "")
    date = cells.get("P_control_date")
    meaning = str(cells.get("P_date_meaning") or "")
    if term in _EXPORT_ON_BOARD_TERMS:
        return (str(date).strip() if date else ""), meaning
    if date:
        return str(date).strip(), meaning
    return "", meaning


def prefers_on_board_cutoff(result: dict[str, Any] | None) -> bool:
    term = e13_from_trading_mode(result)
    return term in _EXPORT_ON_BOARD_TERMS
