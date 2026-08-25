"""Build field evidence nodes from existing OCR output and field metadata."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
import re
from typing import Any

from src.workflow.field_resolution.contracts import make_evidence_node
from src.workflow.field_resolution.normalizers import parse_decimal


def _non_empty(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _document_id(document: dict[str, Any]) -> str:
    return str(document.get("file_fingerprint") or document.get("file_name") or document.get("path") or "")


def _field_keys(document: dict[str, Any]) -> list[str]:
    fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
    meta = document.get("_field_meta") if isinstance(document.get("_field_meta"), dict) else {}
    return sorted({str(key) for key in [*fields.keys(), *meta.keys()] if not str(key).startswith("_")})


def _field_values(document: dict[str, Any], field_key: str) -> tuple[Any, Any, str, str]:
    fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
    meta = document.get("_field_meta") if isinstance(document.get("_field_meta"), dict) else {}
    slot = meta.get(field_key) if isinstance(meta.get(field_key), dict) else {}
    effective = fields.get(field_key)
    accepted = slot.get("accepted_value") if slot.get("status") == "ACCEPTED" else None
    normalized = accepted if _non_empty(accepted) else slot.get("normalized_candidate")
    if not _non_empty(normalized):
        normalized = effective
    raw_value = slot.get("highlight_text")
    if not _non_empty(raw_value):
        raw_value = slot.get("raw_value")
    if not _non_empty(raw_value):
        raw_value = effective
    source = str(slot.get("source") or document.get("ocr_source") or "unknown")
    extractor = str(slot.get("extractor") or "field_inventory")
    return raw_value, normalized, source, extractor


def _find_block(text_blocks: list[dict[str, Any]], needle: str) -> dict[str, Any] | None:
    if not needle:
        return None
    candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    folded = needle.casefold()
    for index, block in enumerate(text_blocks):
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "")
        if folded not in text.casefold():
            continue
        bbox = block.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
            continue
        exact_rank = 0 if text.strip().casefold() == folded else 1
        candidates.append((exact_rank, len(text), index, block))
    return min(candidates, default=(0, 0, 0, None))[3]


_CONTEXT_LABELS: dict[str, tuple[str, ...]] = {
    "quantity": ("数量", "实收数量", "发货数量", "qty", "quantity"),
    "amount": ("金额", "未税金额", "不含税金额", "amount"),
    "taxAmount": ("税额", "tax amount"),
    "totalAmount": ("价税合计", "含税金额", "总金额", "total amount", "total value"),
    "documentDate": ("日期", "date"),
    "postingDate": ("入账日期", "记账日期", "posting date"),
    "acceptanceDate": ("验收", "签收", "acceptance", "receipt"),
    "documentNo": ("单号", "编号", "invoice no", "order no", "certificate no", "contract no"),
    "orderNo": ("订单号", "order no"),
    "invoiceNo": ("发票号", "invoice no"),
    "contractNo": ("合同号", "contract no", "s/c no"),
}

_NUMERIC_EQUIVALENT_FIELDS = {
    "quantity",
    "amount",
    "taxAmount",
    "totalAmount",
    "unitPrice",
    "unitPriceGross",
    "unitPriceNet",
    "netAmount",
}
_DATE_EQUIVALENT_FIELDS = {"documentDate", "postingDate", "acceptanceDate", "deliveryDate"}
_NUMBER_TOKEN_RE = re.compile(
    r"(?:[¥￥$€]\s*)?[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
)
_DATE_TOKEN_RE = re.compile(
    r"(?<!\d)(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?"
    r"(?:[T\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?(?!\d)"
)
_MONTH_PATTERN = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
_EN_DATE_DAY_FIRST_RE = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s+({_MONTH_PATTERN})\s+(\d{{4}})(?!\d)",
    re.IGNORECASE,
)
_EN_DATE_MONTH_FIRST_RE = re.compile(
    rf"(?<!\d)({_MONTH_PATTERN})\s+(\d{{1,2}}),?\s+(\d{{4}})(?!\d)",
    re.IGNORECASE,
)
_MONTH_NUMBERS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _base_field_key(field_key: str) -> str:
    return field_key.rsplit(".", 1)[-1]


def _field_context_distance(raw_text: str, start: int, end: int, field_key: str) -> int | None:
    labels = _CONTEXT_LABELS.get(_base_field_key(field_key))
    if not labels or start < 0:
        return 0
    folded = raw_text.casefold()
    window_start = max(0, start - 64)
    window_end = min(len(raw_text), end + 32)
    distances: list[int] = []
    for label in labels:
        folded_label = label.casefold()
        offset = window_start
        while True:
            label_start = folded.find(folded_label, offset, window_end)
            if label_start < 0:
                break
            label_end = label_start + len(folded_label)
            if label_end <= start:
                distances.append(start - label_end)
            elif label_start >= end:
                distances.append(label_start - end)
            else:
                distances.append(0)
            offset = label_start + max(1, len(folded_label))
    return min(distances) if distances else None


def _has_field_context(raw_text: str, start: int, end: int, field_key: str) -> bool:
    return _field_context_distance(raw_text, start, end, field_key) is not None


def _has_token_boundaries(raw_text: str, start: int, end: int) -> bool:
    """Reject a short value located inside a longer identifier or number."""
    left = raw_text[start - 1] if start > 0 else ""
    right = raw_text[end] if end < len(raw_text) else ""
    return (not left or not left.isalnum()) and (not right or not right.isalnum())


def _best_text_span(raw_text: str, needle: str, field_key: str) -> tuple[int, int]:
    if not needle:
        return -1, -1
    folded_text = raw_text.casefold()
    folded_needle = needle.casefold()
    spans: set[tuple[int, int]] = set()
    offset = 0
    while True:
        start = folded_text.find(folded_needle, offset)
        if start < 0:
            break
        end = start + len(needle)
        spans.add((start, end))
        offset = start + max(1, len(needle))

    base_key = _base_field_key(field_key)
    if base_key in _NUMERIC_EQUIVALENT_FIELDS:
        target = parse_decimal(needle)
        if target is not None:
            for match in _NUMBER_TOKEN_RE.finditer(raw_text):
                if parse_decimal(match.group(0)) == target:
                    spans.add(match.span())
    if base_key in _DATE_EQUIVALENT_FIELDS:
        target = _DATE_TOKEN_RE.search(needle)
        if target:
            target_date = tuple(int(target.group(i)) for i in range(1, 4))
            target_time = tuple(int(target.group(i)) for i in range(4, 6)) if target.group(4) else None
            for match in _DATE_TOKEN_RE.finditer(raw_text):
                source_date = tuple(int(match.group(i)) for i in range(1, 4))
                source_time = tuple(int(match.group(i)) for i in range(4, 6)) if match.group(4) else None
                if source_date == target_date and (target_time is None or source_time == target_time):
                    spans.add(match.span())
            for match in _EN_DATE_DAY_FIRST_RE.finditer(raw_text):
                source_date = (
                    int(match.group(3)),
                    _MONTH_NUMBERS[match.group(2)[:3].casefold()],
                    int(match.group(1)),
                )
                if source_date == target_date and target_time is None:
                    spans.add(match.span())
            for match in _EN_DATE_MONTH_FIRST_RE.finditer(raw_text):
                source_date = (
                    int(match.group(3)),
                    _MONTH_NUMBERS[match.group(1)[:3].casefold()],
                    int(match.group(2)),
                )
                if source_date == target_date and target_time is None:
                    spans.add(match.span())

    if not spans:
        return -1, -1
    ranked: list[tuple[int, int, int, int, int]] = []
    for start, end in spans:
        context_distance = _field_context_distance(raw_text, start, end, field_key)
        ranked.append(
            (
                0 if context_distance is not None else 1,
                context_distance if context_distance is not None else 10**9,
                0 if _has_token_boundaries(raw_text, start, end) else 1,
                start,
                end,
            )
        )
    _, _, _, start, end = min(ranked)
    return start, end


def _locate(document: dict[str, Any], value: Any, field_key: str = "") -> dict[str, Any]:
    needle = str(value).strip() if _non_empty(value) else ""
    raw_text = str(document.get("raw_text") or "")
    start, end = _best_text_span(raw_text, needle, field_key)
    source_excerpt = raw_text[start:end] if start >= 0 else needle
    block = _find_block(list(document.get("text_blocks") or []), source_excerpt)
    bbox: list[float] | None = None
    page: int | None = None
    block_source = ""
    if block:
        bbox = [float(part) for part in list(block.get("bbox") or [])[:4]]
        try:
            page = int(block.get("page") if block.get("page") is not None else 0) + 1
        except (TypeError, ValueError):
            page = 1
        block_source = str(block.get("source") or "ocr_block")
    if start >= 0 and page is None:
        page = 1
    anchored = start >= 0 or bbox is not None
    has_context = _has_field_context(raw_text, start, end, field_key)
    usable = anchored and (bbox is not None or has_context)
    reason_code = (
        "POSITIONED_TEXT_BLOCK"
        if bbox is not None
        else "TEXT_WITH_FIELD_CONTEXT"
        if anchored and has_context
        else "AMBIGUOUS_TEXT_ONLY_ANCHOR"
        if anchored
        else "EVIDENCE_ANCHOR_MISSING"
    )
    return {
        "excerpt": source_excerpt if anchored else "",
        "page": page,
        "char_start": start if start >= 0 else None,
        "char_end": end if start >= 0 else None,
        "bbox": bbox,
        "anchor_status": "ANCHORED" if anchored else "UNLOCATED",
        "usable_for_decision": usable,
        "metadata": {
            "location_method": "text_block_and_raw_text" if block and start >= 0 else ("text_block" if block else ("raw_text" if start >= 0 else "unlocated")),
            "file_name": str(document.get("file_name") or ""),
            "reason_code": reason_code,
            **({"block_source": block_source} if block_source else {}),
        },
    }


def build_document_evidence(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Create one evidence node per non-empty dynamic field, including unknown keys."""
    nodes: list[dict[str, Any]] = []
    for field_key in _field_keys(document):
        if field_key == "items":
            continue
        raw_value, normalized_value, source, extractor = _field_values(document, field_key)
        if not _non_empty(raw_value) and not _non_empty(normalized_value):
            continue
        evidence_value = raw_value if _non_empty(raw_value) else normalized_value
        location = _locate(document, evidence_value, field_key)
        source_value = location.get("excerpt") if location.get("anchor_status") == "ANCHORED" else evidence_value
        nodes.append(
            make_evidence_node(
                document_id=_document_id(document),
                document_role=str(document.get("doc_type") or "other"),
                field_key=field_key,
                raw_value=source_value,
                normalized_value=normalized_value,
                source=source,
                extractor=extractor,
                **location,
            )
        )
    fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
    rows = fields.get("items") if isinstance(fields.get("items"), list) else []
    meta = document.get("_field_meta") if isinstance(document.get("_field_meta"), dict) else {}
    items_slot = meta.get("items") if isinstance(meta.get("items"), dict) else {}
    source = str(items_slot.get("source") or document.get("ocr_source") or "unknown")
    extractor = str(items_slot.get("extractor") or "line_item_inventory")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        for item_key, value in row.items():
            if str(item_key).startswith("_") or not _non_empty(value) or isinstance(value, (dict, list)):
                continue
            field_key = f"items.{index}.{item_key}"
            location = _locate(document, value, field_key)
            source_value = location.get("excerpt") if location.get("anchor_status") == "ANCHORED" else value
            nodes.append(
                make_evidence_node(
                    document_id=_document_id(document),
                    document_role=str(document.get("doc_type") or "other"),
                    field_key=field_key,
                    raw_value=source_value,
                    normalized_value=value,
                    source=source,
                    extractor=extractor,
                    **location,
                )
            )
    return nodes


def attach_document_evidence(
    document: dict[str, Any], *, changed_keys: Iterable[str] | None = None
) -> dict[str, Any]:
    """Attach current nodes and retain superseded versions for audit replay."""
    existing = [dict(node) for node in list(document.get("field_evidence_nodes") or []) if isinstance(node, dict)]
    rebuilt = build_document_evidence(document)
    changed = {str(key) for key in changed_keys} if changed_keys is not None else None
    if changed is None:
        current = rebuilt
        superseded = [node for node in existing if node.get("evidence_id") not in {x.get("evidence_id") for x in current}]
    else:
        untouched = [node for node in existing if str(node.get("field_key") or "") not in changed]
        changed_nodes = [node for node in rebuilt if str(node.get("field_key") or "") in changed]
        current = [*untouched, *changed_nodes]
        current_ids = {node.get("evidence_id") for node in changed_nodes}
        superseded = [
            node
            for node in existing
            if str(node.get("field_key") or "") in changed and node.get("evidence_id") not in current_ids
        ]
    history = [dict(node) for node in list(document.get("field_evidence_history") or []) if isinstance(node, dict)]
    history_ids = {node.get("evidence_id") for node in history}
    for node in superseded:
        if node.get("evidence_id") not in history_ids:
            history.append(deepcopy(node))
            history_ids.add(node.get("evidence_id"))
    document["field_evidence_nodes"] = current
    if history:
        document["field_evidence_history"] = history
    return document


def evidence_for_field(document: dict[str, Any], field_key: str) -> list[dict[str, Any]]:
    return [
        node
        for node in list(document.get("field_evidence_nodes") or [])
        if isinstance(node, dict) and str(node.get("field_key") or "") == str(field_key)
    ]


__all__ = ["attach_document_evidence", "build_document_evidence", "evidence_for_field"]
