"""LLM 输出确定性验证：原文回查、置信度门槛、文档白名单。

验证失败的主张一律丢弃，不得进入正式字段 accepted 或规则终态。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def excerpt_in_text(excerpt: str, full_text: str, *, min_len: int = 6) -> bool:
    """核验摘录来自原文（允许空白差异）。"""
    ex = (excerpt or "").strip()
    if len(ex) < min_len:
        return False
    if ex in (full_text or ""):
        return True
    return normalize_ws(ex) in normalize_ws(full_text or "")


@dataclass
class VerifyResult:
    accepted: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return len(self.accepted)


def verify_claim(
    claim: Dict[str, Any],
    *,
    full_text: str,
    allowed_codes: Optional[Set[str]] = None,
    allowed_files: Optional[Set[str]] = None,
    min_confidence: float = 0.85,
    excerpt_keys: Sequence[str] = ("excerpt", "text_excerpt", "source_text"),
    code_key: str = "issue_code",
    file_keys: Sequence[str] = ("file_name", "document_id", "source_file"),
    require_excerpt: bool = True,
) -> tuple[bool, str]:
    """单条主张验证。返回 (是否通过, 原因)。"""
    if allowed_codes is not None:
        code = str(claim.get(code_key) or "").strip()
        if code and code not in allowed_codes:
            return False, f"code_not_allowed:{code}"

    if allowed_files is not None:
        fname = ""
        for k in file_keys:
            if claim.get(k):
                fname = str(claim.get(k)).strip()
                break
        if fname and fname not in allowed_files:
            return False, f"file_not_in_whitelist:{fname}"

    try:
        conf = float(claim.get("confidence")) if claim.get("confidence") is not None else None
    except (TypeError, ValueError):
        conf = None
    if conf is not None and conf < min_confidence:
        return False, f"confidence_below_gate:{conf}"

    excerpt = ""
    for k in excerpt_keys:
        if claim.get(k):
            excerpt = str(claim.get(k)).strip()
            break
    if require_excerpt:
        if not excerpt_in_text(excerpt, full_text):
            return False, "excerpt_not_in_source"
    elif excerpt and full_text and not excerpt_in_text(excerpt, full_text):
        return False, "excerpt_not_in_source"

    return True, "ok"


def verify_claims(
    claims: Iterable[Dict[str, Any]],
    *,
    full_text: str,
    allowed_codes: Optional[Set[str]] = None,
    allowed_files: Optional[Set[str]] = None,
    min_confidence: float = 0.85,
    require_excerpt: bool = True,
) -> VerifyResult:
    result = VerifyResult()
    for claim in claims:
        if not isinstance(claim, dict):
            result.rejected.append({"claim": claim, "reason": "not_object"})
            continue
        ok, reason = verify_claim(
            claim,
            full_text=full_text,
            allowed_codes=allowed_codes,
            allowed_files=allowed_files,
            min_confidence=min_confidence,
            require_excerpt=require_excerpt,
        )
        if ok:
            result.accepted.append(claim)
        else:
            result.rejected.append({**claim, "_reject_reason": reason})
            result.notes.append(reason)
    if result.rejected:
        result.notes.append(f"verifier_rejected={len(result.rejected)}")
    return result


def evidence_blob_from_documents(documents: Sequence[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for doc in documents:
        name = str(doc.get("file_name") or "")
        role = str(doc.get("doc_type") or doc.get("role") or "")
        text = str(doc.get("raw_text") or doc.get("ocr_text") or "")
        if text.strip():
            parts.append(f"【{role}|{name}】\n{text}")
    return "\n\n".join(parts)


def allowed_files_from_documents(documents: Sequence[Dict[str, Any]]) -> Set[str]:
    return {str(d.get("file_name") or "").strip() for d in documents if d.get("file_name")}
