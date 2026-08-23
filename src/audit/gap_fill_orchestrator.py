"""受控补缺编排：触发记录 → 主张回查 → 写入 AdvisoryCandidate 队列。

原则：
- 不调用 LLM 本身（各 runner 仍负责取数）；本模块只收口「候选如何进店」。
- 回查失败 → DROPPED，永不 PROPOSED。
- 不改规则终态、不写字段 accepted。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from src.llm.verifier import verify_claim
from src.models.advisory_candidates import (
    STORE_KEY,
    decide_candidate,
    default_invalidates_for_task,
    invalidation_targets_for,
    new_advisory_candidate,
    pending_proposed,
    summary_counts,
    upsert_candidates,
)


def get_store(container: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(container, dict):
        return []
    raw = container.get(STORE_KEY)
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


def set_store(container: Dict[str, Any], store: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    container[STORE_KEY] = list(store)
    return container[STORE_KEY]


def _evidence_from_claim(claim: Dict[str, Any]) -> Dict[str, Any]:
    excerpt = ""
    for k in ("excerpt", "text_excerpt", "source_text"):
        if claim.get(k):
            excerpt = str(claim.get(k)).strip()
            break
    source_doc = ""
    for k in ("file_name", "document_id", "source_file", "source_doc"):
        if claim.get(k):
            source_doc = str(claim.get(k)).strip()
            break
    return {
        "excerpt": excerpt,
        "source_text": excerpt,
        "source_doc": source_doc,
        "page": claim.get("page"),
    }


def _kind_from_claim(claim: Dict[str, Any], default: str = "fact") -> str:
    explicit = str(claim.get("kind") or claim.get("candidate_kind") or "").strip().lower()
    if explicit:
        return explicit
    if claim.get("rel_type") or claim.get("from_id") or claim.get("to_id"):
        return "relationship"
    if claim.get("issue_code") or claim.get("issue_type"):
        return "issue"
    if claim.get("hypothesis") or claim.get("observation"):
        return "semantic_finding"
    if claim.get("interpretation") or claim.get("review_checklist"):
        return "interpretation"
    return default


def ingest_verified_claims(
    store: Optional[Iterable[Dict[str, Any]]],
    *,
    task_type: str,
    claims: Iterable[Dict[str, Any]],
    full_text: str,
    trigger_reasons: Optional[Sequence[str]] = None,
    business_id: str = "",
    kind: str = "fact",
    invalidates: Optional[Sequence[str]] = None,
    actor: str = "llm",
    allowed_codes: Optional[Set[str]] = None,
    allowed_files: Optional[Set[str]] = None,
    min_confidence: float = 0.85,
    require_excerpt: bool = True,
    preserve_decided: bool = True,
) -> Dict[str, Any]:
    """对每条主张跑 verifier，通过→PROPOSED，失败→DROPPED，再 upsert。"""
    inv = list(invalidates) if invalidates is not None else default_invalidates_for_task(task_type)
    triggers = [str(x) for x in (trigger_reasons or []) if x]
    if not triggers:
        triggers = [f"TASK:{task_type}"]

    built: List[Dict[str, Any]] = []
    verify_notes: List[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        ok, reason = verify_claim(
            claim,
            full_text=full_text,
            allowed_codes=allowed_codes,
            allowed_files=allowed_files,
            min_confidence=min_confidence,
            require_excerpt=require_excerpt,
        )
        evidence = _evidence_from_claim(claim)
        claim_kind = _kind_from_claim(claim, default=kind)
        status = "PROPOSED" if ok else "DROPPED"
        if not ok:
            verify_notes.append(reason)
        cand = new_advisory_candidate(
            task_type=task_type,
            kind=claim_kind,
            status=status,
            trigger_reasons=triggers,
            business_id=business_id or str(claim.get("business_id") or ""),
            payload=deepcopy(claim),
            evidence=evidence,
            verify={"passed": ok, "reason": reason},
            invalidates=inv if ok else [],
            actor=actor,
            note="" if ok else f"verifier:{reason}",
        )
        built.append(cand)

    merged = upsert_candidates(store, built, preserve_decided=preserve_decided)
    return {
        "store": merged,
        "ingested": built,
        "proposed": [c for c in built if c["status"] == "PROPOSED"],
        "dropped": [c for c in built if c["status"] == "DROPPED"],
        "verify_notes": verify_notes,
        "counts": summary_counts(merged),
    }


def ingest_into_container(
    container: Dict[str, Any],
    *,
    task_type: str,
    claims: Iterable[Dict[str, Any]],
    full_text: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """把 ingest 结果写回 container[advisory_candidates]。"""
    result = ingest_verified_claims(
        get_store(container),
        task_type=task_type,
        claims=claims,
        full_text=full_text,
        **kwargs,
    )
    set_store(container, result["store"])
    return result


def decide_in_container(
    container: Dict[str, Any],
    candidate_id: str,
    status: str,
    *,
    actor: str = "manual",
    reason: str = "",
) -> Dict[str, Any]:
    store, before, after = decide_candidate(
        get_store(container),
        candidate_id,
        status,
        actor=actor,
        reason=reason,
    )
    set_store(container, store)
    targets = invalidation_targets_for(after) if status.upper() == "VERIFIED" else []
    return {
        "store": store,
        "before": before,
        "after": after,
        "invalidates": targets,
        "pending": pending_proposed(store),
        "counts": summary_counts(store),
    }


def queue_snapshot(container: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    store = get_store(container)
    return {
        "counts": summary_counts(store),
        "pending": pending_proposed(store),
        "store": store,
    }
