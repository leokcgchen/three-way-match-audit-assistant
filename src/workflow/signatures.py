"""HITL 签名（从 UI 解耦，供 API / 工作台共用）。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional


def fields_signature(classified: list[dict[str, Any]]) -> str:
    """字段门禁签名：优先用 ACCEPTED 快照，忽略顾问候选对工作副本的扰动。"""
    payload = []
    for item in classified:
        fields = dict(item.get("fields") or {})
        meta = item.get("_field_meta") if isinstance(item.get("_field_meta"), dict) else {}
        signed: dict[str, Any] = {}
        accepted_n = 0
        for key, slot in meta.items():
            if str(key).startswith("_") or not isinstance(slot, dict):
                continue
            if str(slot.get("status") or "").upper() == "ACCEPTED":
                signed[str(key)] = slot.get("accepted_value")
                accepted_n += 1
        if accepted_n == 0:
            signed = {
                str(k): v
                for k, v in fields.items()
                if not str(k).startswith("_")
            }
        payload.append(
            {
                "file": item.get("file_name"),
                "type": item.get("doc_type"),
                "fields": signed,
            }
        )
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def matching_signature(
    *,
    evidence: Optional[dict[str, Any]],
    relations: Optional[list],
    duplicates: Optional[dict[str, Any]],
) -> str:
    payload = {
        "evidence_status": (evidence or {}).get("status"),
        "anchor_keys": (evidence or {}).get("anchor_keys"),
        "links": (evidence or {}).get("links"),
        "relations": [
            {
                "id": r.get("relation_id"),
                "status": r.get("status"),
                "from": r.get("from_id"),
                "to": r.get("to_id"),
            }
            for r in (relations or [])
            if isinstance(r, dict)
        ],
        "dup_findings": [
            f.get("finding_id")
            for f in ((duplicates or {}).get("findings") or [])
            if isinstance(f, dict)
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def conclusion_signature(
    *,
    evidence: Any,
    amount: Any,
    contract: Any,
    three_way: Any,
) -> str:
    def _st(obj: Any, *keys: str) -> str:
        if not isinstance(obj, dict):
            return ""
        for k in keys:
            cur: Any = obj
            for part in k.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                else:
                    cur = None
                    break
            if cur:
                return str(cur)
        return ""

    payload = {
        "evidence": _st(evidence, "status"),
        "amount": _st(amount, "status", "accuracy_report.amount_test.test_status"),
        "contract": _st(contract, "status"),
        "three_way": _st(three_way, "overall_status", "match_result.overall_status"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
