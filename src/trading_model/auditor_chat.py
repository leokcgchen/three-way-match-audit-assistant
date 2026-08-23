from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

_PROMPT = Path(__file__).resolve().parent / "prompts" / "auditor_chat_v1.md"
_MARK = re.compile(r"\[(\d+)\]")


def _default_llm(prompt: str) -> dict[str, Any]:
    from src.trading_model.llm_deepseek import complete_json
    from src.trading_model.llm_json import deepseek_api_key, live_llm_enabled

    if not live_llm_enabled() or not deepseek_api_key():
        raise RuntimeError("auditor chat unavailable")
    return complete_json(prompt, system="只输出合法 JSON 对象，不要 Markdown 前言。")


def _match_paragraph(excerpt: str, paragraphs: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not excerpt:
        return None
    for para in paragraphs:
        if excerpt in str(para.get("raw_text") or ""):
            return para
    return None


def ask_auditor(
    question: str,
    paragraphs: list[dict[str, Any]],
    *,
    hits: Optional[list[dict[str, Any]]] = None,
    llm_fn: Optional[Callable[[str], dict[str, Any]]] = None,
) -> dict[str, Any]:
    q = (question or "").strip()
    if not q:
        raise ValueError("question is empty")
    if not paragraphs:
        raise ValueError("paragraphs required")
    payload = {
        "question": q,
        "paragraphs": [
            {
                "document_id": p.get("document_id"),
                "source_file": p.get("source_file"),
                "seq": p.get("seq"),
                "page": p.get("page"),
                "raw_text": p.get("raw_text"),
            }
            for p in paragraphs
        ],
        "hits": hits or [],
    }
    prompt = _PROMPT.read_text(encoding="utf-8") + "\n\n" + json.dumps(payload, ensure_ascii=False)
    raw = (llm_fn or _default_llm)(prompt)
    kept: list[dict[str, Any]] = []
    kept_n: set[int] = set()
    for item in raw.get("citations") or []:
        if not isinstance(item, dict):
            continue
        excerpt = str(item.get("excerpt") or "")
        para = _match_paragraph(excerpt, paragraphs)
        if para is None:
            continue
        try:
            n = int(item.get("n"))
        except (TypeError, ValueError):
            continue
        kept_n.add(n)
        kept.append(
            {
                "n": n,
                "document_id": para["document_id"],
                "source_file": para["source_file"],
                "seq": para["seq"],
                "page": para["page"],
                "excerpt": excerpt,
            }
        )
    answer = str(raw.get("answer") or "")
    answer = _MARK.sub(lambda m: m.group(0) if int(m.group(1)) in kept_n else "", answer)
    answer = re.sub(r" +", " ", answer).strip()
    return {"answer": answer, "citations": kept}
