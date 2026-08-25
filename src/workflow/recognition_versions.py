"""Versioned, local-first recognition reprocessing with human-value protection."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from src.models.field_values import accept_field, seed_field_meta
from src.workflow.field_resolution.evidence_inventory import attach_document_evidence

RECOGNITION_RULE_VERSION = "evidence-first-rules-v2"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def _document_snapshot(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_name": str(document.get("file_name") or ""),
        "file_fingerprint": str(document.get("file_fingerprint") or ""),
        "doc_type": str(document.get("doc_type") or "other"),
        "fields": deepcopy(document.get("fields") or {}),
        "field_meta": deepcopy(document.get("_field_meta") or {}),
        "field_evidence_nodes": deepcopy(document.get("field_evidence_nodes") or []),
        "recognition_version": deepcopy(document.get("recognition_version")),
    }


def snapshot_recognition(job: dict[str, Any]) -> dict[str, Any]:
    core = {
        "schema_version": "recognition_snapshot.v1",
        "job_id": str(job.get("job_id") or ""),
        "documents": [_document_snapshot(doc) for doc in list(job.get("classified") or []) if isinstance(doc, dict)],
        "job_state": {
            "fields_confirmed": bool(job.get("fields_confirmed")),
            "matching_confirmed": bool(job.get("matching_confirmed")),
            "gospd_sample_results": deepcopy(job.get("gospd_sample_results") or {}),
        },
    }
    core["snapshot_hash"] = _stable_hash(core)
    core["snapshot_at"] = _now()
    return core


def _human_values(document: dict[str, Any]) -> dict[str, Any]:
    meta = document.get("_field_meta") if isinstance(document.get("_field_meta"), dict) else {}
    values: dict[str, Any] = {}
    for key, slot in meta.items():
        if not isinstance(slot, dict) or slot.get("status") != "ACCEPTED":
            continue
        source = str(slot.get("source") or "").strip().lower()
        extractor = str(slot.get("extractor") or "").strip().lower()
        if source.startswith(("manual", "human", "auditor", "hitl")) or "hitl" in extractor:
            values[str(key)] = deepcopy(slot.get("accepted_value"))
    return values


def _source_text(document: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    from src.legacy_ocr.ocr_adapter import _extract_pdf_text_evidence

    path = str(document.get("path") or "").strip()
    if path and Path(path).is_file():
        text, blocks = _extract_pdf_text_evidence(path)
        if text.strip():
            return text, blocks
    return str(document.get("raw_text") or ""), list(document.get("text_blocks") or [])


def _clear_derived_state(job: dict[str, Any]) -> None:
    for key, value in {
        "fields_confirmed": False,
        "fields_confirm_sig": None,
        "matching_confirmed": False,
        "matching_confirm_sig": None,
        "evidence": None,
        "relations": [],
        "amount_test": None,
        "contract_terms": None,
        "three_way": None,
        "conclusion_confirmed": False,
        "conclusion_confirm_sig": None,
        "workbook_path": None,
        "workbook_paths": [],
    }.items():
        job[key] = value
    samples = deepcopy(job.get("gospd_sample_results") or {})
    for sample in samples.values():
        if not isinstance(sample, dict):
            continue
        for key, value in {
            "fields_confirmed": False,
            "fields_confirm_sig": None,
            "matching_confirmed": False,
            "matching_confirm_sig": None,
            "evidence": None,
            "relations": [],
            "amount_test": None,
            "contract_terms": None,
            "three_way": None,
            "conclusion_confirmed": False,
            "conclusion_confirm_sig": None,
            "field_resolution": None,
        }.items():
            sample[key] = value
    job["gospd_sample_results"] = samples


def reprocess_classified_documents(
    job: dict[str, Any],
    *,
    allow_llm_field_supplement: bool = False,
) -> dict[str, Any]:
    """Re-extract all stored documents atomically while preserving human authority."""
    from src.legacy_ocr.ocr_adapter import LegacyOcrAdapter

    original = deepcopy(job)
    snapshot = snapshot_recognition(original)
    candidate = deepcopy(original)
    try:
        adapter = LegacyOcrAdapter(use_mock_when_unavailable=False)
        rebuilt: list[dict[str, Any]] = []
        for old_document in list(original.get("classified") or []):
            if not isinstance(old_document, dict):
                continue
            raw_text, text_blocks = _source_text(old_document)
            if not raw_text.strip():
                raise ValueError(f"source text unavailable: {old_document.get('file_name')}")
            doc_type = str(old_document.get("doc_type") or "other")
            extraction_type = doc_type
            custom_name = str(old_document.get("custom_doc_type_name") or "").casefold()
            if doc_type == "other" and (
                "bill of lading" in raw_text.casefold()
                or "海运提单" in custom_name
                or "提单" in custom_name
            ):
                extraction_type = "transport_document"
            new_fields = dict(
                adapter.extract_fields(
                    raw_text,
                    extraction_type,
                    target_fields=list(old_document.get("extract_field_keys") or []),
                    fast_batch=True,
                    allow_llm_field_supplement=allow_llm_field_supplement,
                )
                or {}
            )
            document = deepcopy(old_document)
            history = [deepcopy(entry) for entry in list(document.get("recognition_history") or []) if isinstance(entry, dict)]
            prior = _document_snapshot(old_document)
            prior["superseded_at"] = _now()
            prior["superseded_by"] = RECOGNITION_RULE_VERSION
            history.append(prior)
            document["recognition_history"] = history
            document["raw_text"] = raw_text
            document["text_blocks"] = text_blocks
            document["fields"] = new_fields
            document["_field_meta"] = {}
            document["field_evidence_nodes"] = []
            seed_field_meta(
                document,
                fields=new_fields,
                source="deterministic_reprocess",
                extractor=RECOGNITION_RULE_VERSION,
            )
            conflicts: list[dict[str, Any]] = []
            for key, human_value in _human_values(old_document).items():
                new_value = new_fields.get(key)
                if new_value not in (None, "") and str(new_value) != str(human_value):
                    conflicts.append(
                        {
                            "field_key": key,
                            "human_value": deepcopy(human_value),
                            "new_candidate": deepcopy(new_value),
                            "status": "HUMAN_VALUE_PRESERVED",
                        }
                    )
                accept_field(
                    document,
                    key,
                    human_value,
                    source="human_preserved",
                    extractor="reprocess_human_protection",
                )
            document["reprocess_conflicts"] = conflicts
            document["recognition_version"] = {
                "schema_version": "recognition_version.v1",
                "rule_version": RECOGNITION_RULE_VERSION,
                "processed_at": _now(),
                "allow_llm_field_supplement": bool(allow_llm_field_supplement),
                "source": "native_pdf_text_or_stored_text",
            }
            attach_document_evidence(document)
            rebuilt.append(document)

        candidate["classified"] = rebuilt
        snapshots = [deepcopy(entry) for entry in list(candidate.get("recognition_snapshots") or []) if isinstance(entry, dict)]
        snapshots.append(snapshot)
        candidate["recognition_snapshots"] = snapshots
        _clear_derived_state(candidate)
        candidate["recognition_reprocess"] = {
            "status": "COMPLETED",
            "processed_at": _now(),
            "rule_version": RECOGNITION_RULE_VERSION,
            "allow_llm_field_supplement": bool(allow_llm_field_supplement),
            "snapshot_hash": snapshot["snapshot_hash"],
            "document_count": len(rebuilt),
        }
        return candidate
    except Exception as exc:  # noqa: BLE001
        rollback = deepcopy(original)
        rollback["recognition_reprocess"] = {
            "status": "ERROR_ROLLED_BACK",
            "processed_at": _now(),
            "rule_version": RECOGNITION_RULE_VERSION,
            "allow_llm_field_supplement": bool(allow_llm_field_supplement),
            "snapshot_hash": snapshot["snapshot_hash"],
            "error": str(exc),
        }
        return rollback


__all__ = ["RECOGNITION_RULE_VERSION", "reprocess_classified_documents", "snapshot_recognition"]
