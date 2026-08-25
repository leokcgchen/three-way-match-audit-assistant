"""Deterministic sample-business alias index for document assignment.

The sample business id remains canonical.  Order numbers are aliases that may
locate the canonical business, but an ambiguous or conflicting alias never
auto-assigns a document.
"""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Iterable


_BUSINESS_FIELDS = (
    "businessId",
    "businessID",
    "business_id",
    "businessNo",
    "businessNumber",
    "sampleBusinessId",
    "caseId",
    "caseRef",
)
_ORDER_FIELDS = ("orderNo", "salesOrderNo", "purchaseOrderNo")
_INVOICE_FIELDS = ("invoiceNo", "invoiceNumber", "invoice_no")
_FILENAME_IDENTIFIER = re.compile(
    r"(?i)(?<![A-Z0-9])([A-Z]{1,12}(?:[-_ ]\d[A-Z0-9]{0,19}){1,4})(?![A-Z0-9])"
)


def normalize_alias(value: Any) -> str:
    """Normalize only deterministic formatting differences."""

    return re.sub(r"[-_\s]", "", str(value or "").strip().upper())


def _append_unique(target: list[str], value: str) -> None:
    if value and value not in target:
        target.append(value)


def _population_rows(sample_population: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in (sample_population.get("rows") or []) if isinstance(row, dict)]
    present = {str(row.get("business_id") or "").strip() for row in rows}
    for business_id in sample_population.get("business_ids") or []:
        value = str(business_id or "").strip()
        if value and value not in present:
            rows.append({"business_id": value, "order_numbers": []})
    return rows


def build_alias_index(sample_population: dict[str, Any]) -> dict[str, Any]:
    aliases: dict[str, dict[str, Any]] = {}
    businesses: dict[str, dict[str, Any]] = {}
    for row in _population_rows(sample_population or {}):
        business_id = str(row.get("business_id") or "").strip()
        if not business_id:
            continue
        order_numbers = [
            str(value or "").strip()
            for value in (row.get("order_numbers") or [])
            if str(value or "").strip()
        ]
        invoice_numbers = [
            str(value or "").strip()
            for value in (row.get("invoice_numbers") or [])
            if str(value or "").strip()
        ]
        business = businesses.setdefault(
            business_id,
            {"business_id": business_id, "order_numbers": [], "invoice_numbers": []},
        )
        for order_number in order_numbers:
            _append_unique(business["order_numbers"], order_number)
        for invoice_number in invoice_numbers:
            _append_unique(business["invoice_numbers"], invoice_number)

        for alias_type, values in (
            ("business_id", [business_id]),
            ("order_number", order_numbers),
            ("invoice_number", invoice_numbers),
        ):
            for value in values:
                normalized = normalize_alias(value)
                if not normalized:
                    continue
                record = aliases.setdefault(
                    normalized,
                    {
                        "normalized": normalized,
                        "values": [],
                        "types": [],
                        "business_ids": [],
                    },
                )
                _append_unique(record["values"], value)
                _append_unique(record["types"], alias_type)
                _append_unique(record["business_ids"], business_id)

    ambiguous_aliases = [
        {
            "normalized": normalized,
            "values": list(record["values"]),
            "business_ids": list(record["business_ids"]),
        }
        for normalized, record in aliases.items()
        if len(record["business_ids"]) > 1
    ]
    return {
        "aliases": aliases,
        "businesses": businesses,
        "ambiguous_aliases": ambiguous_aliases,
    }


def _alias_pattern(value: str) -> re.Pattern[str] | None:
    parts = re.findall(r"[A-Z0-9]+", str(value or "").upper())
    if not parts:
        return None
    body = r"[-_\s]*".join(re.escape(part) for part in parts)
    return re.compile(rf"(?i)(?<![A-Z0-9]){body}(?![A-Z0-9])")


def _safe_for_unlabelled_raw_text(record: dict[str, Any], value: str) -> bool:
    """Reject aliases that are indistinguishable from ordinary amounts/counts."""

    normalized = normalize_alias(value)
    if any(ch.isalpha() for ch in normalized):
        return len(normalized) >= 4
    return len(normalized) >= 8 and "invoice_number" in (record.get("types") or [])


def _evidence_for_record(
    *,
    record: dict[str, Any],
    detected: str,
    source: str,
    preferred_type: str = "",
    char_start: int | None = None,
    char_end: int | None = None,
) -> dict[str, Any]:
    alias_type = preferred_type if preferred_type in record.get("types", []) else str((record.get("types") or [""])[0])
    evidence = {
        "type": alias_type,
        "detected": detected,
        "matched": str((record.get("values") or [detected])[0]),
        "source": source,
        "match_method": "normalized_exact",
        "business_ids": list(record.get("business_ids") or []),
    }
    if char_start is not None and char_end is not None:
        evidence["char_start"] = char_start
        evidence["char_end"] = char_end
    return evidence


def _exact_evidence(
    document: dict[str, Any], alias_index: dict[str, Any]
) -> list[dict[str, Any]]:
    aliases = dict(alias_index.get("aliases") or {})
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(
        record: dict[str, Any],
        detected: str,
        source: str,
        preferred_type: str = "",
        char_start: int | None = None,
        char_end: int | None = None,
    ) -> None:
        row = _evidence_for_record(
            record=record,
            detected=detected,
            source=source,
            preferred_type=preferred_type,
            char_start=char_start,
            char_end=char_end,
        )
        key = (str(record.get("normalized") or ""), source, row["type"])
        if key not in seen:
            seen.add(key)
            evidence.append(row)

    file_name = str(document.get("file_name") or "")
    for record in aliases.values():
        for raw_value in record.get("values") or []:
            pattern = _alias_pattern(str(raw_value))
            match = pattern.search(file_name) if pattern else None
            if match:
                add(record, match.group(0), "filename")
                break

    declared_values: Iterable[Any] = document.get("declared_business_ids") or []
    for value in declared_values:
        record = aliases.get(normalize_alias(value))
        if record:
            add(record, str(value), "manual", "business_id")

    fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
    for field_name in _BUSINESS_FIELDS:
        value = fields.get(field_name)
        record = aliases.get(normalize_alias(value))
        if record:
            add(record, str(value), f"ocr_field:{field_name}", "business_id")
    for field_name in _ORDER_FIELDS:
        value = fields.get(field_name)
        record = aliases.get(normalize_alias(value))
        if record:
            add(record, str(value), f"ocr_field:{field_name}", "order_number")
    for field_name in _INVOICE_FIELDS:
        value = fields.get(field_name)
        record = aliases.get(normalize_alias(value))
        if record:
            add(record, str(value), f"ocr_field:{field_name}", "invoice_number")

    raw_text = str(document.get("raw_text") or "")
    if raw_text:
        for record in aliases.values():
            for raw_value in record.get("values") or []:
                if not _safe_for_unlabelled_raw_text(record, str(raw_value)):
                    continue
                pattern = _alias_pattern(str(raw_value))
                match = pattern.search(raw_text) if pattern else None
                if match:
                    add(
                        record,
                        match.group(0),
                        "raw_text",
                        char_start=match.start(),
                        char_end=match.end(),
                    )
                    break
    return evidence


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) <= 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    i = j = differences = 0
    while i < len(short) and j < len(long):
        if short[i] == long[j]:
            i += 1
            j += 1
            continue
        differences += 1
        j += 1
        if differences > 1:
            return False
    return True


def _prefix(value: str) -> str:
    match = re.match(r"^[A-Z]+", value)
    return match.group(0) if match else ""


def _similar_candidates(
    source_texts: Iterable[tuple[str, str]], alias_index: dict[str, Any]
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    aliases = dict(alias_index.get("aliases") or {})
    business_ids: list[str] = []
    candidates: list[dict[str, Any]] = []
    detected_values: list[str] = []
    for source, text in source_texts:
        for match in _FILENAME_IDENTIFIER.finditer(text):
            detected = match.group(1)
            normalized_detected = normalize_alias(detected)
            _append_unique(detected_values, detected)
            for normalized, record in aliases.items():
                if not _prefix(normalized_detected) or _prefix(normalized_detected) != _prefix(normalized):
                    continue
                if not _edit_distance_at_most_one(normalized_detected, normalized):
                    continue
                for business_id in record.get("business_ids") or []:
                    _append_unique(business_ids, business_id)
                candidate = {
                    "detected": detected,
                    "matched": str((record.get("values") or [normalized])[0]),
                    "types": list(record.get("types") or []),
                    "business_ids": list(record.get("business_ids") or []),
                    "match_method": "similar_candidate",
                    "source": source,
                }
                if candidate not in candidates:
                    candidates.append(candidate)
    return business_ids, candidates, detected_values


def resolve_document_business(
    document: dict[str, Any], sample_population: dict[str, Any]
) -> dict[str, Any]:
    alias_index = build_alias_index(sample_population or {})
    evidence = _exact_evidence(document, alias_index)
    owner_ids: list[str] = []
    for row in evidence:
        for business_id in row.get("business_ids") or []:
            _append_unique(owner_ids, business_id)

    if len(owner_ids) == 1:
        evidence_types = {str(row.get("type") or "") for row in evidence}
        return {
            "status": "MATCHED",
            "business_id": owner_ids[0],
            "confidence": "highest" if {"business_id", "order_number"} <= evidence_types else "high",
            "evidence": evidence,
            "candidate_business_ids": owner_ids,
            "similar_candidates": [],
            "detected_identifiers": [str(row.get("detected") or "") for row in evidence],
        }
    if len(owner_ids) > 1:
        has_unique_evidence = any(len(row.get("business_ids") or []) == 1 for row in evidence)
        return {
            "status": "CONFLICT" if has_unique_evidence else "AMBIGUOUS_ALIAS",
            "business_id": None,
            "confidence": "conflict",
            "evidence": evidence,
            "candidate_business_ids": owner_ids,
            "similar_candidates": [],
            "detected_identifiers": [str(row.get("detected") or "") for row in evidence],
        }

    similar_businesses, similar, detected = _similar_candidates(
        (
            ("filename", str(document.get("file_name") or "")),
            ("raw_text", str(document.get("raw_text") or "")),
        ),
        alias_index,
    )
    if similar:
        return {
            "status": "SIMILAR_CANDIDATE",
            "business_id": None,
            "confidence": "review",
            "evidence": [],
            "candidate_business_ids": similar_businesses,
            "similar_candidates": similar,
            "detected_identifiers": detected,
        }
    return {
        "status": "UNASSIGNED",
        "business_id": None,
        "confidence": "none",
        "evidence": [],
        "candidate_business_ids": [],
        "similar_candidates": [],
        "detected_identifiers": detected,
    }


__all__ = ["build_alias_index", "normalize_alias", "resolve_document_business"]
