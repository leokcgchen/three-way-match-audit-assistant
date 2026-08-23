"""统一顾问候选（AdvisoryCandidate）：LLM/启发式补缺的唯一正式载体。

生命周期：
  trigger → PROPOSED（须经 verifier）→ VERIFIED | REJECTED
  回查失败 → DROPPED（不得进入正式字段 accepted / 规则终态 / VERIFIED 关系）

与 relation_candidates 并存：关系边仍用专用模型；本模块可镜像记录「为何提出」。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha1
from typing import Any, Dict, Iterable, List, Optional, Sequence
from uuid import uuid4

CandidateStatus = str  # PROPOSED | VERIFIED | REJECTED | DROPPED
CandidateKind = str  # fact | relationship | semantic_finding | issue | interpretation

VALID_STATUSES = frozenset({"PROPOSED", "VERIFIED", "REJECTED", "DROPPED"})
VALID_KINDS = frozenset(
    {"fact", "relationship", "semantic_finding", "issue", "interpretation"}
)

# 人工接受后建议脏哪些下游测项（编排器 / job_store 消费）
INVALIDATE_TARGETS = frozenset(
    {"fields", "evidence", "amount", "cutoff", "terms", "three_way", "gate5", "workbook"}
)

STORE_KEY = "advisory_candidates"


def make_candidate_id(
    *,
    task_type: str,
    kind: str,
    business_id: str = "",
    fingerprint: str = "",
) -> str:
    raw = f"{task_type}|{kind}|{business_id}|{fingerprint}"
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_advisory_candidate(
    *,
    task_type: str,
    kind: CandidateKind = "fact",
    status: CandidateStatus = "PROPOSED",
    trigger_reasons: Optional[Sequence[str]] = None,
    business_id: str = "",
    payload: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    verify: Optional[Dict[str, Any]] = None,
    invalidates: Optional[Sequence[str]] = None,
    actor: str = "llm",
    note: str = "",
    fingerprint: str = "",
    decision_authority: str = "LLM_ADVISORY_ONLY",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    kind_n = str(kind or "fact").strip().lower()
    if kind_n not in VALID_KINDS:
        kind_n = "fact"
    status_n = str(status or "PROPOSED").upper()
    if status_n not in VALID_STATUSES:
        status_n = "PROPOSED"

    payload = dict(payload or {})
    evidence = dict(evidence or {})
    if not fingerprint:
        # 匹配消歧：用稳定键做指纹，避免 excerpt 微小差异刷出重复顾问行
        if str(task_type or "").upper() == "MATCHING_DISAMBIGUATION":
            fp_bits = [
                str(payload.get("file_name") or evidence.get("source_doc") or ""),
                str(payload.get("disposition") or "").upper(),
                str(
                    payload.get("suggested_biz_id")
                    or payload.get("business_id")
                    or payload.get("normalized_candidate")
                    or ""
                ),
            ]
        else:
            fp_bits = [
                str(payload.get("field_name") or payload.get("issue_code") or ""),
                str(payload.get("normalized_candidate") or payload.get("value") or ""),
                str(evidence.get("excerpt") or "")[:80],
                str(evidence.get("source_doc") or ""),
            ]
        fingerprint = "|".join(fp_bits)

    inv = []
    for t in invalidates or []:
        t_n = str(t or "").strip().lower()
        if t_n in INVALIDATE_TARGETS:
            inv.append(t_n)

    return {
        "candidate_id": make_candidate_id(
            task_type=str(task_type or ""),
            kind=kind_n,
            business_id=str(business_id or ""),
            fingerprint=fingerprint,
        ),
        "fingerprint": fingerprint,
        "kind": kind_n,
        "task_type": str(task_type or ""),
        "status": status_n,
        "decision_authority": str(decision_authority or "LLM_ADVISORY_ONLY"),
        "trigger_reasons": [str(x) for x in (trigger_reasons or []) if x],
        "business_id": str(business_id or ""),
        "payload": payload,
        "evidence": {
            "excerpt": str(evidence.get("excerpt") or ""),
            "source_doc": str(evidence.get("source_doc") or ""),
            "page": evidence.get("page"),
            "source_text": str(evidence.get("source_text") or evidence.get("excerpt") or ""),
        },
        "verify": {
            "passed": bool((verify or {}).get("passed", False)),
            "reason": str((verify or {}).get("reason") or ""),
        },
        "invalidates": inv,
        "actor": str(actor or "llm"),
        "note": str(note or ""),
        "created_at": _now(),
        "updated_at": _now(),
        "extra": dict(extra or {}),
        "event_token": uuid4().hex[:10],
    }


def upsert_candidates(
    store: Optional[Iterable[Dict[str, Any]]],
    candidates: Iterable[Dict[str, Any]],
    *,
    preserve_decided: bool = True,
) -> List[Dict[str, Any]]:
    """合并候选；已 VERIFIED/REJECTED 的同 id 默认保留人工决定。"""
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in store or []:
        if isinstance(row, dict) and row.get("candidate_id"):
            by_id[str(row["candidate_id"])] = deepcopy(row)

    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        cid = str(cand.get("candidate_id") or "")
        if not cid:
            continue
        existing = by_id.get(cid)
        if (
            preserve_decided
            and existing
            and str(existing.get("status") or "").upper() in {"VERIFIED", "REJECTED"}
        ):
            for k in ("payload", "evidence", "note", "trigger_reasons", "verify"):
                if cand.get(k) is not None:
                    existing[k] = deepcopy(cand.get(k))
            existing["updated_at"] = _now()
            continue
        by_id[cid] = deepcopy(cand)
    return list(by_id.values())


def decide_candidate(
    store: List[Dict[str, Any]],
    candidate_id: str,
    status: CandidateStatus,
    *,
    actor: str = "manual",
    reason: str = "",
    resolve_same_fingerprint: bool = True,
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    status_n = str(status or "").upper()
    if status_n not in {"VERIFIED", "REJECTED", "PROPOSED"}:
        raise ValueError(f"非法候选状态: {status_n}")
    target = next(
        (r for r in store if str(r.get("candidate_id")) == str(candidate_id)),
        None,
    )
    if target is None:
        return store, None, None
    if str(target.get("status") or "").upper() == "DROPPED":
        raise ValueError("DROPPED 候选不可人工升格为正式状态")

    sibling_ids = {str(candidate_id)}
    if resolve_same_fingerprint and status_n in {"VERIFIED", "REJECTED"}:
        fp = str(target.get("fingerprint") or "")
        task = str(target.get("task_type") or "").upper()
        if fp:
            for row in store:
                if str(row.get("task_type") or "").upper() != task:
                    continue
                if str(row.get("fingerprint") or "") != fp:
                    continue
                if str(row.get("status") or "").upper() != "PROPOSED":
                    continue
                sibling_ids.add(str(row.get("candidate_id")))

    out: List[Dict[str, Any]] = []
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    for row in store:
        cid = str(row.get("candidate_id"))
        if cid not in sibling_ids:
            out.append(row)
            continue
        if cid == str(candidate_id):
            before = deepcopy(row)
        if str(row.get("status") or "").upper() == "DROPPED":
            out.append(row)
            continue
        updated = deepcopy(row)
        updated["status"] = status_n
        updated["actor"] = actor
        updated["updated_at"] = _now()
        if reason:
            updated["note"] = reason
        elif cid != str(candidate_id):
            updated["note"] = (updated.get("note") or "") + "；同源指纹一并决议"
        if cid == str(candidate_id):
            after = updated
        out.append(updated)
    return out, before, after


def pending_proposed(store: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return [
        r
        for r in (store or [])
        if isinstance(r, dict) and str(r.get("status") or "").upper() == "PROPOSED"
    ]


def summary_counts(store: Optional[Iterable[Dict[str, Any]]]) -> Dict[str, int]:
    counts = {
        "PROPOSED": 0,
        "VERIFIED": 0,
        "REJECTED": 0,
        "DROPPED": 0,
        "total": 0,
    }
    for r in store or []:
        if not isinstance(r, dict):
            continue
        st = str(r.get("status") or "PROPOSED").upper()
        if st not in counts:
            st = "PROPOSED"
        counts[st] += 1
        counts["total"] += 1
    return counts


def invalidation_targets_for(
    candidate: Optional[Dict[str, Any]],
) -> List[str]:
    if not isinstance(candidate, dict):
        return []
    return [t for t in (candidate.get("invalidates") or []) if t in INVALIDATE_TARGETS]


def default_invalidates_for_task(task_type: str) -> List[str]:
    """按已接线 task_type 给出默认脏范围（可被调用方覆盖）。"""
    t = str(task_type or "").upper()
    mapping = {
        # 字段类主张：脏下游测项；Gate3 是否保留由 apply_advisory 软路径决定（已确认则刷新签名）
        "FIELD_GAP_FILL": ["evidence", "amount", "cutoff", "terms", "three_way", "gate5"],
        "MATCHING_DISAMBIGUATION": ["evidence", "gate5"],
        "AMOUNT_GAP_FILL": ["amount", "gate5"],
        "CONTRACT_CLARITY_REVIEW": ["terms", "gate5"],
        "CUTOFF_SEMANTIC_EXTRACTION": ["cutoff", "three_way", "gate5"],
        "CONCLUSION_INTERPRETATION": [],  # 旁路解读，不脏规则终态
    }
    return list(mapping.get(t, ["gate5"]))
