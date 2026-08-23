from __future__ import annotations

from typing import Any

from .constants import DICT_VERSION, LOW_CONFIDENCE


def _slot(
    field_key: str,
    raw_value: Any,
    std_value: Any,
    source_doc: str,
    availability: str,
    source_key: str,
    pick_reason: str,
    excerpt: str,
    confidence: float = 0.99,
) -> dict[str, Any]:
    return {
        "field_key": field_key,
        "raw_value": raw_value,
        "std_value": std_value,
        "source_doc": source_doc,
        "page_no": 1,
        "bbox": None,
        "confidence": confidence,
        "source_system": "ocr",
        "dict_version": DICT_VERSION,
        "availability": availability,
        "source_key": source_key,
        "pick_reason": pick_reason,
        "verbatim_excerpt": excerpt,
    }


def slot_fields(classified: list[dict[str, Any]], harvest: dict[str, Any]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    incoterm_candidates: list[tuple[str, str, str, float]] = []
    for doc in classified:
        doc_id = str(doc.get("document_id") or "")
        fields = doc.get("fields") or {}
        text = str(doc.get("raw_text") or "")
        conf = float(doc.get("confidence") or 1.0)
        for key in ("transportTerms", "incoterms", "tradeTerms"):
            if key in fields and fields[key]:
                incoterm_candidates.append((str(fields[key]), doc_id, key, conf))
        if harvest.get("nominal_code") and doc_id == classified[0].get("document_id"):
            incoterm_candidates.append(
                (
                    str(harvest["nominal_code"]),
                    doc_id,
                    "raw_text",
                    conf,
                )
            )
    codes = []
    for raw, doc_id, key, conf in incoterm_candidates:
        token = raw.strip().split()[0].upper() if raw.strip() else ""
        codes.append((token, raw, doc_id, key, conf))
    unique_codes = {c[0] for c in codes if c[0]}
    if len(unique_codes) > 1:
        for token, raw, doc_id, key, conf in codes:
            slots.append(
                _slot(
                    "incoterms",
                    raw,
                    token,
                    doc_id,
                    "UNRELIABLE",
                    key,
                    "多候选不猜：贸易术语字段冲突",
                    raw,
                    conf,
                )
            )
    elif codes:
        token, raw, doc_id, key, conf = next(c for c in codes if c[0])
        availability = "UNRELIABLE" if conf < LOW_CONFIDENCE else "OK"
        slots.append(
            _slot(
                "incoterms",
                raw,
                token,
                doc_id,
                availability,
                key,
                "别名命中 transportTerms/incoterms/正文术语",
                raw,
                conf,
            )
        )
    if harvest.get("named_place_or_port"):
        slots.append(
            _slot(
                "named_place_or_port",
                harvest["named_place_or_port"],
                harvest["named_place_or_port"],
                str(classified[0].get("document_id") or ""),
                "OK",
                "raw_text",
                "术语后命名地点",
                harvest["named_place_or_port"],
            )
        )
    if harvest.get("version"):
        slots.append(
            _slot(
                "incoterms_version",
                harvest["version"],
                harvest["version"],
                str(classified[0].get("document_id") or ""),
                "OK",
                "raw_text",
                "Incoterms 版本",
                harvest["version"],
            )
        )
    return slots
