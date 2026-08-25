"""Auditable business-group projection for mixed document batches.

This module deliberately separates *grouping evidence* from later three-way
field checks.  It never asks an LLM to join documents and it never treats a
weak similarity as a released match.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.reporting.gospd01010_filler import group_classified_by_chain


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("¥", "").strip())
    except (TypeError, ValueError):
        return None


def _strong_keys(doc: dict[str, Any]) -> list[str]:
    fields = dict(doc.get("fields") or {})
    keys = []
    for key in ("orderNo", "salesOrderNo", "contractNo", "invoiceNo", "voucherNo"):
        value = _text(fields.get(key)).upper().replace(" ", "")
        if value:
            keys.append(f"{key}:{value}")
    return keys


def _amounts(docs: list[dict[str, Any]]) -> list[float]:
    vals: list[float] = []
    for doc in docs:
        fields = dict(doc.get("fields") or {})
        value = _num(fields.get("totalAmount") or fields.get("amount"))
        if value is not None and value != 0:
            vals.append(value)
    return vals


def _amount_conflict(vals: list[float]) -> bool:
    if len(vals) < 2:
        return False
    lo, hi = min(vals), max(vals)
    # Invoice tax-exclusive amount is a different field; only material, not
    # rounding-level, divergence is a grouping conflict.
    return abs(hi - lo) > max(0.02, max(abs(hi), abs(lo)) * 0.02)


def _weak_profile(docs: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for doc in docs:
        f = dict(doc.get("fields") or {})
        for key in ("buyerName", "supplierName", "customerName"):
            v = _text(f.get(key))
            if v and v not in result["counterparties"]:
                result["counterparties"].append(v)
        for key in ("documentDate", "deliveryDate", "acceptanceDate", "postingDate"):
            v = _text(f.get(key))
            if v and v not in result["dates"]:
                result["dates"].append(v)
    return dict(result)


def group_documents_by_business(
    classified: list[dict[str, Any]],
    *,
    allow_weak_unique_attach: bool = True,
    allow_unique_so_ht_merge: bool = True,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """人工业务框优先；其余单据继续使用既有强主键分组规则。"""

    manual: dict[str, list[dict[str, Any]]] = defaultdict(list)
    canonical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    automatic: list[dict[str, Any]] = []
    for doc in list(classified or []):
        group_id = _text(doc.get("business_group_id"))
        if group_id:
            manual[group_id].append(doc)
        elif _text(doc.get("sample_business_id")):
            canonical[_text(doc.get("sample_business_id"))].append(doc)
        else:
            automatic.append(doc)
    grouped = [(group_id, group_docs) for group_id, group_docs in manual.items()]
    grouped.extend(
        (group_id, group_docs) for group_id, group_docs in canonical.items()
    )
    grouped.extend(
        group_classified_by_chain(
            automatic,
            allow_weak_unique_attach=allow_weak_unique_attach,
            allow_unique_so_ht_merge=allow_unique_so_ht_merge,
        )
    )
    return grouped


def build_business_groups(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build display-ready BusinessGroup objects from OCR-extracted documents.

    Strong identifiers determine automatic joins.  A manually assigned
    ``business_group_id`` has priority and is marked as an auditable human
    override.  Documents without strong evidence remain ``NEEDS_REVIEW``;
    weak features are explanatory only and do not silently merge groups.
    """
    docs = list(classified or [])
    buckets: list[tuple[str, list[dict[str, Any]], bool]] = []
    for group_id, group_docs in group_documents_by_business(
        docs,
        allow_weak_unique_attach=False,
        allow_unique_so_ht_merge=True,
    ):
        manual_override = any(
            _text(doc.get("business_group_id")) == group_id for doc in group_docs
        )
        buckets.append((group_id, group_docs, manual_override))

    out: list[dict[str, Any]] = []
    for group_id, group_docs, manual_override in buckets:
        evidence = sorted({key for doc in group_docs for key in _strong_keys(doc)})
        conflicts: list[str] = []
        field_consistency_alerts: list[str] = []
        vals = _amounts(group_docs)
        if _amount_conflict(vals):
            # This is deliberately not a grouping conflict.  The documents are
            # already connected by an identifier; a value mismatch belongs in
            # the later three-way field-consistency phase.
            field_consistency_alerts.append("组内金额存在超过2%的差异；请进入字段一致性校对，不会自动拆分业务组。")
        if manual_override:
            status, confidence = ("CONFLICT", 1.0) if conflicts else ("MATCHED", 1.0)
            reason = "人工分组调整；请保留该操作的审计轨迹。"
        elif evidence and not conflicts:
            status, confidence = "MATCHED", 0.98
            reason = "已由单据中的订单号/合同号/发票号等强主键建立业务组。"
        elif conflicts:
            status, confidence = "CONFLICT", 0.45
            reason = "强主键关联到同一组，但存在冲突，不能自动放行。"
        else:
            status, confidence = "NEEDS_REVIEW", 0.35
            reason = "缺少足以自动分组的强主键；仅展示弱特征，等待人工确认。"
        out.append(
            {
                "group_id": group_id,
                "confidence_score": confidence,
                "status": status,
                "manual_override": manual_override,
                "source_documents": [
                    {
                        "file_name": _text(doc.get("file_name")),
                        "doc_type": _text(doc.get("doc_type")) or "other",
                        "strong_keys": _strong_keys(doc),
                    }
                    for doc in group_docs
                ],
                "file_names": [_text(doc.get("file_name")) for doc in group_docs],
                "doc_types": sorted({_text(doc.get("doc_type")) for doc in group_docs if _text(doc.get("doc_type"))}),
                "doc_count": len(group_docs),
                "strong_keys": evidence,
                "weak_evidence": _weak_profile(group_docs),
                "conflicts": conflicts,
                "field_consistency_alerts": field_consistency_alerts,
                "reason": reason,
                # This is the only permitted context boundary for later LLM review.
                "llm_context_document_ids": [_text(doc.get("file_name")) for doc in group_docs],
            }
        )
    return sorted(out, key=lambda g: (g["status"] != "CONFLICT", g["group_id"]))
