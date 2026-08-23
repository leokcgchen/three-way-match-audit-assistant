"""Packet-unit business relationships and confirmation gates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from src.workflow.packet_cards import UNRESOLVED
from src.workflow.packet_cluster import UNIDENTIFIED_CHAIN

UNIDENTIFIED_BUSINESS_IDS = {
    "",
    UNIDENTIFIED_CHAIN.casefold(),
    "unidentified",
    "unresolved",
}


def _clean_business_ids(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value.casefold() in UNIDENTIFIED_BUSINESS_IDS:
            continue
        if value not in out:
            out.append(value)
    return out


def normalize_business_ids(unit: Mapping[str, Any]) -> list[str]:
    """Return authoritative business IDs, with legacy chain fallback."""
    declared = unit.get("business_ids")
    if declared is not None:
        if isinstance(declared, str):
            return _clean_business_ids([declared])
        return _clean_business_ids(declared)
    return _clean_business_ids([unit.get("chain_id")])


def with_business_ids(
    unit: Mapping[str, Any],
    business_ids: Iterable[Any],
) -> dict[str, Any]:
    """Copy a unit with authoritative IDs and a legacy first-ID mirror."""
    normalized = _clean_business_ids(business_ids)
    return {
        **dict(unit),
        "business_ids": normalized,
        "chain_id": normalized[0] if normalized else UNIDENTIFIED_CHAIN,
    }


def validate_confirmable_units(
    units: Sequence[Mapping[str, Any]],
    *,
    multi_page_files: set[str],
    start_ocr: bool,
) -> None:
    """Reject unresolved relationships, boundaries, and OCR types."""
    for unit in units:
        if unit.get("dropped"):
            continue
        unit_id = str(unit.get("unit_id") or "?")
        source_file = str(unit.get("source_file") or "?")
        if not normalize_business_ids(unit):
            raise ValueError(f"单元 {unit_id}（{source_file}）尚未确认业务归属")
        if source_file in multi_page_files and not bool(unit.get("boundary_confirmed")):
            raise ValueError(f"单元 {unit_id}（{source_file}）尚未确认拆包边界")
        doc_type = str(unit.get("doc_type") or unit.get("host_type") or "").strip()
        if start_ocr and doc_type in {"", "other", UNRESOLVED}:
            raise ValueError(f"单元 {unit_id}（{source_file}）尚未确认单据类型，不能开始 OCR")
