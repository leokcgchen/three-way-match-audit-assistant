"""单据候选关系：PROPOSED / VERIFIED / REJECTED。

规则引擎不因 PROPOSED 改 PASS/FAIL；仅 VERIFIED 视为人工确认的正式边。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha1
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

RelationStatus = str  # PROPOSED | VERIFIED | REJECTED

VALID_STATUSES = frozenset({"PROPOSED", "VERIFIED", "REJECTED"})


def make_relation_id(
    *,
    from_id: str,
    to_id: str,
    rel_type: str,
    shared_key: str = "",
) -> str:
    a, b = sorted([str(from_id or ""), str(to_id or "")])
    raw = f"{a}|{b}|{rel_type}|{shared_key}"
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def new_relation(
    *,
    from_id: str,
    to_id: str,
    rel_type: str,
    status: RelationStatus = "PROPOSED",
    source_doc: str = "",
    page: Optional[int] = None,
    excerpt: str = "",
    actor: str = "system",
    shared_keys: Optional[List[str]] = None,
    note: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    keys = [str(k) for k in (shared_keys or []) if k]
    rid = make_relation_id(
        from_id=from_id,
        to_id=to_id,
        rel_type=rel_type,
        shared_key=keys[0] if keys else "",
    )
    status = str(status or "PROPOSED").upper()
    if status not in VALID_STATUSES:
        status = "PROPOSED"
    return {
        "relation_id": rid,
        "from_id": str(from_id or ""),
        "to_id": str(to_id or ""),
        "rel_type": str(rel_type or "RELATED"),
        "status": status,
        "source_doc": str(source_doc or from_id or ""),
        "page": page,
        "excerpt": str(excerpt or ""),
        "actor": str(actor or "system"),
        "shared_keys": keys,
        "note": str(note or ""),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "extra": extra or {},
    }


def upsert_relations(
    store: Optional[Iterable[Dict[str, Any]]],
    candidates: Iterable[Dict[str, Any]],
    *,
    preserve_decided: bool = True,
) -> List[Dict[str, Any]]:
    """合并新候选；已 VERIFIED/REJECTED 的同 id 默认保留人工决定。"""
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in store or []:
        if isinstance(row, dict) and row.get("relation_id"):
            by_id[str(row["relation_id"])] = deepcopy(row)
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        rid = str(cand.get("relation_id") or "")
        if not rid:
            continue
        existing = by_id.get(rid)
        if (
            preserve_decided
            and existing
            and str(existing.get("status") or "").upper() in {"VERIFIED", "REJECTED"}
        ):
            # 仅刷新展示字段，不改状态
            for k in ("excerpt", "shared_keys", "note", "source_doc", "page"):
                if cand.get(k) is not None:
                    existing[k] = deepcopy(cand.get(k))
            continue
        by_id[rid] = deepcopy(cand)
    return list(by_id.values())


def decide_relation(
    store: List[Dict[str, Any]],
    relation_id: str,
    status: RelationStatus,
    *,
    actor: str = "manual",
    reason: str = "",
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """更新关系状态；返回 (新列表, before, after)。"""
    status = str(status or "").upper()
    if status not in {"VERIFIED", "REJECTED", "PROPOSED"}:
        raise ValueError(f"非法关系状态: {status}")
    out: List[Dict[str, Any]] = []
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    for row in store:
        if str(row.get("relation_id")) != str(relation_id):
            out.append(row)
            continue
        before = deepcopy(row)
        updated = deepcopy(row)
        updated["status"] = status
        updated["actor"] = actor
        updated["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if reason:
            updated["note"] = reason
        after = updated
        out.append(updated)
    return out, before, after


def pending_proposed(store: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return [
        r
        for r in (store or [])
        if isinstance(r, dict)
        and str(r.get("status") or "PROPOSED").upper() == "PROPOSED"
    ]


def summary_counts(store: Optional[Iterable[Dict[str, Any]]]) -> Dict[str, int]:
    counts = {"PROPOSED": 0, "VERIFIED": 0, "REJECTED": 0, "total": 0}
    for r in store or []:
        if not isinstance(r, dict):
            continue
        st = str(r.get("status") or "PROPOSED").upper()
        if st not in counts:
            st = "PROPOSED"
        counts[st] += 1
        counts["total"] += 1
    return counts


def new_event_token() -> str:
    return uuid4().hex[:10]
