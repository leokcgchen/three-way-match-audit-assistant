"""Hard boundary between the uploaded sample population and voucher evidence.

The sample list is authoritative. OCR may discover identifiers, but it must
never enlarge the audit population. Documents that cannot be safely tied to
that population are retained as explicit exceptions for auditor review.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from src.legacy_ocr.ledger_parser import (
    compact_biz_id,
    extract_biz_ids_from_filename,
    looks_like_biz_id,
    normalize_biz_id,
)
from src.workflow.business_alias_index import (
    build_alias_index,
    normalize_alias,
    resolve_document_business,
)

_STRONG_FIELD_NAMES = ("orderNo", "salesOrderNo", "contractNo")
_BUSINESS_FIELD_NAMES = (
    "businessId",
    "businessID",
    "business_id",
    "businessNo",
    "businessNumber",
    "sampleBusinessId",
    "caseId",
    "caseRef",
)
_SEGMENTED_ID_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9])([A-Z][A-Z0-9]{0,11}(?:[-_]\d[A-Z0-9]{0,19}){1,4})(?![A-Z0-9])"
)
_DOCUMENT_REFERENCE_PATTERN = re.compile(
    r"(?i)(?<![A-Z0-9])([A-Z][A-Z0-9]{0,11}(?:[-_][A-Z0-9]{2,20}){1,5})(?![A-Z0-9])"
)
_DOCUMENT_REFERENCE_PREFIXES = {
    "BL", "BOL", "BK",  # bill of lading
    "SC", "HT", "CT", "CONTRACT",
    "FP", "INV", "INVOICE", "CI",
    "YS", "DN", "DELIVERY", "RECEIPT",
    "CNTR", "CONTAINER", "SEAL",
    "SO", "PO", "ORDER",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_ids(
    values: Iterable[Any],
    *,
    require_legacy_shape: bool = True,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = normalize_biz_id(value)
        compact = compact_biz_id(normalized)
        if (
            not normalized
            or len(normalized) > 80
            or not compact
            or compact in seen
            or (require_legacy_shape and not looks_like_biz_id(normalized))
        ):
            continue
        seen.add(compact)
        result.append(normalized)
    return result


def population_business_ids(sample_population: Optional[dict[str, Any]]) -> list[str]:
    if not isinstance(sample_population, dict):
        return []
    return _normalized_ids(
        sample_population.get("business_ids") or [],
        require_legacy_shape=False,
    )


def declared_business_ids(document: dict[str, Any]) -> list[str]:
    values: list[Any] = list(document.get("declared_business_ids") or [])
    source_packet = document.get("source_packet")
    if isinstance(source_packet, dict):
        values.extend(source_packet.get("business_ids") or [])
    return _normalized_ids(values, require_legacy_shape=False)


def detected_strong_business_ids(document: dict[str, Any]) -> list[str]:
    fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
    values: list[Any] = [fields.get(name) for name in _STRONG_FIELD_NAMES]
    if str(document.get("doc_type") or "") in {"contract", "order"}:
        values.append(fields.get("documentNo"))
    values.extend(extract_biz_ids_from_filename(str(document.get("file_name") or "")))
    return _normalized_ids(value for value in values if value is not None)


def _candidate(value: Any, source: str) -> dict[str, str] | None:
    normalized = normalize_biz_id(value)
    if not normalized or len(normalized) > 80:
        return None
    return {"value": normalized, "source": source}


def _append_candidate(
    target: list[dict[str, str]],
    value: Any,
    source: str,
) -> None:
    item = _candidate(value, source)
    if item and item not in target:
        target.append(item)


def _filename_matches_sample(file_name: str, sample_ids: Iterable[str]) -> list[str]:
    text = normalize_biz_id(file_name)
    result: list[str] = []
    for sample_id in sample_ids:
        pattern = re.compile(
            rf"(?<![A-Z0-9]){re.escape(normalize_biz_id(sample_id))}(?![A-Z0-9])"
        )
        if pattern.search(text):
            result.append(sample_id)
    return result


def _explicit_business_values(document: dict[str, Any]) -> list[str]:
    fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
    return _normalized_ids(
        (fields.get(name) for name in _BUSINESS_FIELD_NAMES),
        require_legacy_shape=False,
    )


def _filename_identifier_candidates(file_name: str) -> list[str]:
    values = extract_biz_ids_from_filename(file_name)
    values.extend(match.group(1) for match in _SEGMENTED_ID_PATTERN.finditer(file_name))
    return _normalized_ids(values, require_legacy_shape=False)


def _raw_text_identifier_candidates(raw_text: str) -> list[str]:
    return _normalized_ids(
        (match.group(1) for match in _SEGMENTED_ID_PATTERN.finditer(raw_text or "")),
        require_legacy_shape=False,
    )


def resolve_sample_business_identity(
    document: dict[str, Any],
    sample_population: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve one canonical business through exact business/order aliases."""

    sample_ids = population_business_ids(sample_population)
    sample_by_compact = _sample_index(sample_ids)
    sample_families = {_id_family(value) for value in sample_ids}
    known_aliases = set(
        (build_alias_index(sample_population or {}).get("aliases") or {}).keys()
    )
    declared = declared_business_ids(document)
    document_for_index = dict(document)
    document_for_index["declared_business_ids"] = declared
    decision = resolve_document_business(document_for_index, sample_population or {})
    evidence = list(decision.get("evidence") or [])
    candidates: list[dict[str, str]] = []
    for item in evidence:
        for value in item.get("business_ids") or []:
            _append_candidate(candidates, value, str(item.get("source") or "alias_index"))

    explicit = _explicit_business_values(document)
    legacy_strong = detected_strong_business_ids(document)
    all_detected = list(dict.fromkeys([*declared, *_filename_identifier_candidates(
        str(document.get("file_name") or "")
    ), *_raw_text_identifier_candidates(str(document.get("raw_text") or "")),
        *explicit, *legacy_strong, *(decision.get("detected_identifiers") or [])]))
    same_family_outside = [
        value
        for value in all_detected
        if normalize_alias(value) not in known_aliases
        and compact_biz_id(value) not in sample_by_compact
        and _id_family(value) in sample_families
    ]

    selected = str(decision.get("business_id") or "")
    selected_source = str((evidence or [{}])[0].get("source") or "")
    if decision.get("status") == "MATCHED" and selected and same_family_outside:
        return {
            "status": "MIXED_SCOPE",
            "sample_business_id": None,
            "source": selected_source,
            "candidates": candidates,
            "detected_business_ids": list(dict.fromkeys([selected, *all_detected])),
            "declared_business_ids": declared,
            "business_index_status": "CONFLICT",
            "business_index_confidence": "conflict",
            "business_index_evidence": evidence,
            "candidate_business_ids": [selected],
            "similar_candidates": [],
        }
    if decision.get("status") == "MATCHED" and selected:
        return {
            "status": "MATCHED",
            "sample_business_id": selected,
            "source": selected_source,
            "candidates": candidates,
            "detected_business_ids": [selected],
            "declared_business_ids": declared,
            "business_index_status": "MATCHED",
            "business_index_confidence": decision.get("confidence"),
            "business_index_evidence": evidence,
            "candidate_business_ids": [selected],
            "similar_candidates": [],
        }
    status_map = {
        "CONFLICT": "INDEX_CONFLICT",
        "AMBIGUOUS_ALIAS": "AMBIGUOUS_ALIAS",
        "SIMILAR_CANDIDATE": "SIMILAR_CANDIDATE",
    }
    if decision.get("status") in status_map:
        return {
            "status": status_map[str(decision.get("status"))],
            "sample_business_id": None,
            "source": selected_source,
            "candidates": candidates,
            "detected_business_ids": all_detected,
            "declared_business_ids": declared,
            "business_index_status": decision.get("status"),
            "business_index_confidence": decision.get("confidence"),
            "business_index_evidence": evidence,
            "candidate_business_ids": list(decision.get("candidate_business_ids") or []),
            "similar_candidates": list(decision.get("similar_candidates") or []),
        }
    if same_family_outside:
        return {
            "status": "OUT_OF_SAMPLE",
            "sample_business_id": None,
            "source": "",
            "candidates": [],
            "detected_business_ids": list(dict.fromkeys(all_detected)),
            "declared_business_ids": declared,
            "business_index_status": "UNASSIGNED",
            "business_index_confidence": "none",
            "business_index_evidence": [],
            "candidate_business_ids": [],
            "similar_candidates": [],
        }
    return {
        "status": "UNASSIGNED",
        "sample_business_id": None,
        "source": "",
        "candidates": [],
        "detected_business_ids": [],
        "declared_business_ids": declared,
        "business_index_status": "UNASSIGNED",
        "business_index_confidence": "none",
        "business_index_evidence": [],
        "candidate_business_ids": [],
        "similar_candidates": [],
    }


def _sample_index(sample_ids: Iterable[str]) -> dict[str, str]:
    return {compact_biz_id(value): value for value in sample_ids if compact_biz_id(value)}


def _id_family(value: str) -> str:
    """Identifier prefix used only to detect contradictory same-kind anchors."""

    match = re.match(r"^[A-Z]+", normalize_biz_id(value))
    return match.group(0) if match else "#NUMERIC"


def _exception_id(document: dict[str, Any]) -> str:
    identity = str(
        document.get("file_fingerprint")
        or document.get("path")
        or document.get("file_name")
        or "unknown-document"
    )
    return "scope-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _build_exception(
    document: dict[str, Any],
    *,
    detected: list[str],
    declared: list[str],
    status: str,
    candidate_business_ids: list[str] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    similar_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    file_name = str(document.get("file_name") or "未命名文件")
    if status == "OUT_OF_SAMPLE":
        reason = "识别到的业务号不在当前抽样清单中，不能进入审阅业务列表。"
    elif status == "MIXED_SCOPE":
        reason = "同一文件同时包含清单内和清单外业务号，不能自动归入任一抽样业务。"
    elif status == "INDEX_CONFLICT":
        reason = "文件中的业务编号或订单号指向不同抽样业务，不能自动归类，请审计师核对。"
    elif status == "AMBIGUOUS_ALIAS":
        reason = "该订单号在抽样清单中对应多笔业务，不能自动归类，请审计师核对。"
    elif status == "SIMILAR_CANDIDATE":
        reason = "仅发现相似编号，系统不会按相似数字自动归类，请审计师确认。"
    else:
        reason = "无法确认该文件属于抽样清单中的哪一笔业务。"
    return {
        "exception_id": _exception_id(document),
        "file_name": file_name,
        "scope_status": status,
        "detected_business_ids": detected,
        "declared_business_ids": declared,
        "reason": reason,
        "candidate_business_ids": list(candidate_business_ids or []),
        "business_index_evidence": list(evidence or []),
        "similar_candidates": list(similar_candidates or []),
        "recommended_action": (
            "review"
            if status in {"INDEX_CONFLICT", "AMBIGUOUS_ALIAS", "SIMILAR_CANDIDATE"}
            else "delete"
        ),
        "created_at": _utc_now(),
        "document": dict(document),
    }


def _document_references(document: dict[str, Any]) -> dict[str, str]:
    """Return auditable exact identifiers that may link two uploaded documents."""

    references: dict[str, str] = {}
    texts = [str(document.get("raw_text") or "")]
    fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
    texts.extend(str(value or "") for value in fields.values() if isinstance(value, (str, int)))
    for text in texts:
        for match in _DOCUMENT_REFERENCE_PATTERN.finditer(text):
            raw = match.group(1)
            prefix_match = re.match(r"(?i)^([A-Z]+)", raw)
            prefix = prefix_match.group(1).upper() if prefix_match else ""
            if prefix not in _DOCUMENT_REFERENCE_PREFIXES:
                continue
            normalized = normalize_alias(raw)
            if normalized and any(ch.isdigit() for ch in normalized):
                references.setdefault(normalized, raw)
    return references


def _apply_document_reference_inheritance(
    documents: list[dict[str, Any]],
    identities: list[dict[str, Any]],
) -> None:
    """Propagate only a unique, already anchored business over an exact shared id."""

    references = [_document_references(document) for document in documents]
    reference_documents: dict[str, list[int]] = {}
    for index, rows in enumerate(references):
        for normalized in rows:
            reference_documents.setdefault(normalized, []).append(index)

    changed = True
    while changed:
        changed = False
        owners: dict[str, dict[str, list[Any]]] = {}
        for normalized, indexes in reference_documents.items():
            if len(indexes) < 2:
                continue
            business_ids: list[str] = []
            source_files: list[str] = []
            for index in indexes:
                identity = identities[index]
                if identity.get("status") != "MATCHED":
                    continue
                business_id = str(identity.get("sample_business_id") or "")
                if business_id and business_id not in business_ids:
                    business_ids.append(business_id)
                file_name = str(documents[index].get("file_name") or "")
                if file_name and file_name not in source_files:
                    source_files.append(file_name)
            if business_ids:
                owners[normalized] = {
                    "business_ids": business_ids,
                    "source_files": source_files,
                }

        for index, identity in enumerate(identities):
            if identity.get("status") == "MATCHED":
                continue
            candidate_ids: list[str] = []
            evidence: list[dict[str, Any]] = []
            for normalized, raw in references[index].items():
                owner = owners.get(normalized)
                if not owner:
                    continue
                for business_id in owner["business_ids"]:
                    if business_id not in candidate_ids:
                        candidate_ids.append(business_id)
                evidence.append(
                    {
                        "type": "document_reference",
                        "reference": raw,
                        "normalized": normalized,
                        "source": "document_reference",
                        "match_method": "shared_exact_reference",
                        "business_ids": list(owner["business_ids"]),
                        "via_files": list(owner["source_files"]),
                    }
                )
            if len(candidate_ids) == 1:
                business_id = candidate_ids[0]
                identities[index] = {
                    **identity,
                    "status": "MATCHED",
                    "sample_business_id": business_id,
                    "source": "document_reference",
                    "candidates": [
                        {"value": business_id, "source": "document_reference"}
                    ],
                    "detected_business_ids": [business_id],
                    "business_index_status": "MATCHED",
                    "business_index_confidence": "inherited_high",
                    "business_index_evidence": evidence,
                    "candidate_business_ids": [business_id],
                }
                changed = True
            elif len(candidate_ids) > 1:
                identities[index] = {
                    **identity,
                    "status": "INDEX_CONFLICT",
                    "sample_business_id": None,
                    "source": "document_reference",
                    "candidates": [
                        {"value": business_id, "source": "document_reference"}
                        for business_id in candidate_ids
                    ],
                    "detected_business_ids": candidate_ids,
                    "business_index_status": "CONFLICT",
                    "business_index_confidence": "conflict",
                    "business_index_evidence": evidence,
                    "candidate_business_ids": candidate_ids,
                }


def partition_documents_by_sample_scope(
    documents: Iterable[dict[str, Any]],
    sample_population: Optional[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split OCR output without ever letting OCR expand a populated sample list."""

    source = list(documents or [])
    sample_ids = population_business_ids(sample_population)
    if not sample_ids:
        return source, []

    identities = [
        resolve_sample_business_identity(document, sample_population)
        for document in source
    ]
    _apply_document_reference_inheritance(source, identities)

    accepted: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    for document, identity in zip(source, identities):
        status = str(identity.get("status") or "UNASSIGNED")
        if status == "MATCHED":
            document["sample_business_id"] = identity["sample_business_id"]
            document["business_index_source"] = identity["source"]
            document["business_index_candidates"] = identity["candidates"]
            document["business_index_status"] = identity["business_index_status"]
            document["business_index_confidence"] = identity["business_index_confidence"]
            document["business_index_evidence"] = identity["business_index_evidence"]
            accepted.append(document)
        else:
            exceptions.append(
                _build_exception(
                    document,
                    detected=list(identity.get("detected_business_ids") or []),
                    declared=list(identity.get("declared_business_ids") or []),
                    status=status,
                    candidate_business_ids=list(identity.get("candidate_business_ids") or []),
                    evidence=list(identity.get("business_index_evidence") or []),
                    similar_candidates=list(identity.get("similar_candidates") or []),
                )
            )
    return accepted, exceptions


def merge_scope_exceptions(
    existing: Iterable[dict[str, Any]],
    incoming: Iterable[dict[str, Any]],
    *,
    accepted_documents: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Merge by stable id, clearing an old exception if the same file is accepted later."""

    accepted_names = {
        str(document.get("file_name") or "")
        for document in accepted_documents
        if str(document.get("file_name") or "")
    }
    merged = {
        str(item.get("exception_id") or _exception_id(item.get("document") or item)): dict(item)
        for item in existing or []
        if str(item.get("file_name") or "") not in accepted_names
    }
    for item in incoming or []:
        merged[str(item.get("exception_id") or _exception_id(item.get("document") or item))] = dict(item)
    return list(merged.values())


def enforce_sample_scope_on_job(job_id: str) -> dict[str, Any]:
    """Migrate documents stored before the sample boundary was enforced."""

    from src.workflow.job_store import JOB_STORE

    job = JOB_STORE.get(job_id) or {}
    classified = list(job.get("classified") or [])
    accepted, incoming = partition_documents_by_sample_scope(
        classified,
        job.get("sample_population"),
    )
    existing = list(job.get("scope_exceptions") or [])
    exceptions = merge_scope_exceptions(
        existing,
        incoming,
        accepted_documents=accepted,
    )
    if accepted == classified and exceptions == existing:
        return job
    return JOB_STORE.update(
        job_id,
        classified=accepted,
        scope_exceptions=exceptions,
    )
