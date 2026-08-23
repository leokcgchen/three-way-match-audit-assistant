"""证据匹配 LLM 消歧：仅输出候选建议，不改规则终态。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from src.llm.batch_assist import batch_llm_assist_enabled, llm_chat_json
from src.llm.verifier import (
    allowed_files_from_documents,
    evidence_blob_from_documents,
    verify_claims,
)


def llm_matching_disambiguation(
    classified: Sequence[Dict[str, Any]],
    rule_result: Dict[str, Any],
    *,
    business_id: str = "",
) -> Dict[str, Any]:
    """对规则匹配结果做消歧建议。

    返回结构：
    {
      "ran": bool,
      "proposals": [...],  # 已通过原文核验的候选
      "rejected": [...],
      "notes": [...],
      "blocks_downstream": bool,  # CONFLICT/AMBIGUOUS 时提示阻断金额/截止
    }
    """
    notes: List[str] = []
    empty = {
        "ran": False,
        "proposals": [],
        "rejected": [],
        "notes": notes,
        "blocks_downstream": False,
    }
    if not batch_llm_assist_enabled():
        notes.append("未启用 BATCH_LLM_ASSIST / 无 LLM Key，跳过匹配消歧")
        return empty

    status = str(rule_result.get("status") or "").upper()
    # PASS 且无未串联节点时不必消歧
    unlinked = [
        n
        for n in (rule_result.get("nodes") or [])
        if isinstance(n, dict) and n.get("role") != "other" and not n.get("linked")
    ]
    role_files: Dict[str, List[str]] = {}
    for item in classified:
        role = str(item.get("doc_type") or "")
        if not role or role == "other":
            continue
        role_files.setdefault(role, []).append(str(item.get("file_name") or ""))
    multi_candidate = any(len(v) > 1 for v in role_files.values())
    if status == "PASS" and not unlinked and not multi_candidate:
        notes.append("规则匹配已明确，跳过 LLM 消歧")
        return empty

    docs = [
        {
            "file_name": x.get("file_name"),
            "doc_type": x.get("doc_type"),
            "fields": x.get("fields") or {},
            "raw_text": (str(x.get("raw_text") or ""))[:3500],
            "excluded_from_match": bool(x.get("excluded_from_match")),
        }
        for x in classified
        if not x.get("excluded_from_match")
    ]
    blob = evidence_blob_from_documents(docs)
    if not blob.strip():
        notes.append("无 OCR 文本，跳过匹配消歧")
        return empty

    from src.llm.prompts import (
        UNIFIED_SYSTEM_PROMPT,
        build_matching_disambiguation_user,
        extract_matching_proposals,
    )

    prompt = build_matching_disambiguation_user(
        business_id=business_id
        or ",".join(str(k) for k in (rule_result.get("anchor_keys") or [])[:3]),
        rule_result=rule_result,
        documents=docs,
        documents_blob=blob,
    )
    try:
        data = llm_chat_json(prompt, system=UNIFIED_SYSTEM_PROMPT, max_tokens=1200)
        raw_proposals = extract_matching_proposals(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("matching disambiguation failed: {}", exc)
        notes.append(f"LLM 匹配消歧失败：{exc}")
        return empty

    allowed_files = allowed_files_from_documents(docs)
    verified = verify_claims(
        raw_proposals,
        full_text=blob,
        allowed_files=allowed_files,
        min_confidence=0.85,
        require_excerpt=True,
    )
    # disposition 校验
    proposals: List[Dict[str, Any]] = []
    for p in verified.accepted:
        disp = str(p.get("disposition") or "").upper()
        if disp not in {"ADOPT", "EXCLUDE", "KEEP_CANDIDATE"}:
            verified.rejected.append({**p, "_reject_reason": "bad_disposition"})
            continue
        proposals.append(
            {
                "file_name": str(p.get("file_name") or ""),
                "disposition": disp,
                "reason": str(p.get("reason") or p.get("description") or ""),
                "excerpt": str(p.get("excerpt") or p.get("text_excerpt") or "")[:200],
                "confidence": p.get("confidence"),
                "suggested_biz_id": p.get("suggested_biz_id") or p.get("business_id"),
                "source": "llm_matching_disambiguation",
            }
        )

    overall = str(data.get("overall_ambiguity") or data.get("ambiguity") or "").upper()
    blocks = overall in {"CONFLICT", "AMBIGUOUS"} or any(
        p["disposition"] == "KEEP_CANDIDATE" for p in proposals
    )
    if verified.rejected:
        notes.append(f"消歧主张核验拒绝 {len(verified.rejected)} 条")
    if proposals:
        notes.append(f"LLM 消歧候选 {len(proposals)} 条（未改规则终态）")
    else:
        notes.append("LLM 未产生可核验的消歧候选")

    return {
        "ran": True,
        "proposals": proposals,
        "rejected": verified.rejected,
        "notes": notes,
        "blocks_downstream": blocks,
        "overall_ambiguity": overall or ("AMBIGUOUS" if blocks else "CLEAR"),
        "prompt_task": "MATCHING_DISAMBIGUATION",
    }


def apply_disambiguation_proposal(
    classified: List[Dict[str, Any]],
    proposal: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """人工采纳单条建议：排除文件或写回建议业务编号（正式采用）。"""
    from src.models.field_values import accept_field

    fname = str(proposal.get("file_name") or "")
    disp = str(proposal.get("disposition") or "").upper()
    out = list(classified)
    for item in out:
        if str(item.get("file_name") or "") != fname:
            continue
        if disp == "EXCLUDE":
            item["excluded_from_match"] = True
        elif disp == "ADOPT":
            item["excluded_from_match"] = False
            biz = proposal.get("suggested_biz_id")
            if biz:
                # 人工 VERIFIED/采纳：写入 ACCEPTED，而非旁路候选（避免门禁签名抖动）
                accept_field(
                    item,
                    "documentNo",
                    str(biz),
                    source="matching_adopt",
                    extractor="MATCHING_DISAMBIGUATION",
                )
        elif disp == "KEEP_CANDIDATE":
            item["match_keep_candidate"] = True
    return out
