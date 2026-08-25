"""字段三值模型：raw_value / normalized_candidate / accepted_value。

规则引擎与测试读取 effective 值（优先 accepted，否则回退 fields 工作副本）。
raw 一经写入不可被规范化或 LLM 覆盖；accepted 仅规则批准或人工确认后写入。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

META_KEY = "_field_meta"

FieldStatus = str  # UNRESOLVED | ACCEPTED | REJECTED


def _empty_slot(
    *,
    raw_value: Any = None,
    normalized_candidate: Any = None,
    accepted_value: Any = None,
    source: str = "unknown",
    extractor: str = "",
    status: FieldStatus = "UNRESOLVED",
) -> Dict[str, Any]:
    return {
        "raw_value": raw_value,
        "normalized_candidate": normalized_candidate,
        "accepted_value": accepted_value,
        "source": source,
        "extractor": extractor,
        "status": status,
    }


def get_field_meta(item: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    meta = item.get(META_KEY)
    if not isinstance(meta, dict):
        meta = {}
        item[META_KEY] = meta
    return meta


def seed_field_meta(
    item: Dict[str, Any],
    *,
    fields: Optional[Dict[str, Any]] = None,
    source: str = "ocr",
    extractor: str = "",
    overwrite_raw: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """根据当前 fields 初始化/补齐三值元数据（不覆盖已有 raw）。"""
    meta = get_field_meta(item)
    src_fields = fields if fields is not None else (item.get("fields") or {})
    for key, value in (src_fields or {}).items():
        if str(key).startswith("_"):
            continue
        slot = meta.get(key)
        if not isinstance(slot, dict):
            meta[key] = _empty_slot(
                raw_value=deepcopy(value),
                normalized_candidate=deepcopy(value),
                source=source,
                extractor=extractor,
                status="UNRESOLVED",
            )
            continue
        if overwrite_raw or slot.get("raw_value") is None:
            slot["raw_value"] = deepcopy(value) if slot.get("raw_value") is None else slot["raw_value"]
            if overwrite_raw:
                slot["raw_value"] = deepcopy(value)
        if slot.get("normalized_candidate") is None:
            slot["normalized_candidate"] = deepcopy(value)
        slot.setdefault("source", source)
        slot.setdefault("extractor", extractor)
        slot.setdefault("status", "UNRESOLVED")
    return meta


def set_candidate(
    item: Dict[str, Any],
    key: str,
    value: Any,
    *,
    source: str = "llm",
    extractor: str = "",
) -> None:
    """写入候选值；不改 raw；不自动 accepted。

    若字段已 ACCEPTED：只挂 normalized_candidate 供顾问审阅，**不降级、不改 fields**，
    避免 HITL 签名漂移 →「字段已变化」误伤并清空证据/Gate4。
    未确认字段：同步到 fields 工作副本供预览。
    """
    if str(key).startswith("_"):
        return
    meta = get_field_meta(item)
    slot = meta.get(key)
    if not isinstance(slot, dict):
        slot = _empty_slot(raw_value=None, source=source, extractor=extractor)
        meta[key] = slot
    if slot.get("raw_value") is None and source in {"ocr", "heuristic", "filename"}:
        slot["raw_value"] = deepcopy(value)
    slot["normalized_candidate"] = deepcopy(value)
    if extractor:
        slot["extractor"] = extractor
    if slot.get("status") == "ACCEPTED":
        # 保留正式确认；候选仅旁挂，由顾问队列/人工再决定是否改写
        slot["pending_candidate_source"] = source
        return
    slot["source"] = source
    fields = dict(item.get("fields") or {})
    fields[key] = value
    item["fields"] = fields


def accept_field(
    item: Dict[str, Any],
    key: str,
    value: Any,
    *,
    source: str = "manual",
    extractor: str = "hitl",
    highlight_text: Any = None,
) -> Dict[str, Any]:
    """人工/规则批准采用值。

    highlight_text：原件定位用原文（如金额候选 raw_value）；与 accepted 数值可不同。
    """
    meta = get_field_meta(item)
    slot = meta.get(key)
    if not isinstance(slot, dict):
        slot = _empty_slot(raw_value=deepcopy(value), source=source, extractor=extractor)
        meta[key] = slot
    before = {
        "accepted_value": slot.get("accepted_value"),
        "normalized_candidate": slot.get("normalized_candidate"),
        "status": slot.get("status"),
    }
    if slot.get("raw_value") is None:
        slot["raw_value"] = deepcopy(value)
    slot["normalized_candidate"] = deepcopy(value)
    slot["accepted_value"] = deepcopy(value)
    slot["status"] = "ACCEPTED"
    slot["source"] = source
    if extractor:
        slot["extractor"] = extractor
    ht = highlight_text
    if ht is None:
        ht = slot.get("raw_value")
    if ht is not None and str(ht).strip():
        slot["highlight_text"] = str(ht).strip()
    fields = dict(item.get("fields") or {})
    fields[key] = value
    item["fields"] = fields
    # Keep evidence location and accepted normalization synchronized without
    # changing the immutable raw value held in the field slot.
    from src.workflow.field_resolution.evidence_inventory import attach_document_evidence

    attach_document_evidence(item, changed_keys={key})
    return before


def accept_all_current_fields(
    item: Dict[str, Any],
    *,
    source: str = "manual_confirm",
) -> List[str]:
    """确认时：将当前 fields 全部标为 accepted。"""
    fields = dict(item.get("fields") or {})
    seed_field_meta(item, fields=fields, source=source)
    accepted_keys: List[str] = []
    for key, value in fields.items():
        if str(key).startswith("_"):
            continue
        accept_field(item, key, value, source=source, extractor="confirm_all")
        accepted_keys.append(key)
    return accepted_keys


def effective_value(item: Dict[str, Any], key: str, default: Any = None) -> Any:
    """预览/展示用：accepted → candidate → fields 工作副本。"""
    meta = get_field_meta(item)
    slot = meta.get(key)
    if isinstance(slot, dict):
        if slot.get("status") == "ACCEPTED" and slot.get("accepted_value") is not None:
            return slot.get("accepted_value")
        if slot.get("normalized_candidate") is not None:
            return slot.get("normalized_candidate")
    fields = item.get("fields") or {}
    return fields.get(key, default)


def get_verified_value(item: Dict[str, Any], key: str, default: Any = None) -> Any:
    """规则/底稿唯一取值入口：仅 ACCEPTED；未确认不得进入正式计算。"""
    meta = get_field_meta(item)
    slot = meta.get(key)
    if isinstance(slot, dict):
        if slot.get("status") == "ACCEPTED" and slot.get("accepted_value") is not None:
            return slot.get("accepted_value")
        return default
    # 无三值元数据时：兼容旧数据——仅当整单已字段确认（由调用方保证）才可读 fields
    fields = item.get("fields") or {}
    if item.get("_fields_confirmed") or item.get("fields_confirmed"):
        return fields.get(key, default)
    return default


def verified_fields(item: Dict[str, Any], *, allow_legacy_fields: bool = False) -> Dict[str, Any]:
    """规则引擎正式视图：只含已 ACCEPTED 字段。

    allow_legacy_fields=True：无 _field_meta 时回退 fields（兼容未迁三值的旧样例）。
    """
    meta = get_field_meta(item)
    out: Dict[str, Any] = {}
    has_meta = bool(meta)
    if has_meta:
        for key, slot in meta.items():
            if not isinstance(slot, dict) or str(key).startswith("_"):
                continue
            if slot.get("status") == "ACCEPTED" and slot.get("accepted_value") is not None:
                out[key] = slot["accepted_value"]
        return out
    if allow_legacy_fields:
        return {
            k: v
            for k, v in (item.get("fields") or {}).items()
            if not str(k).startswith("_")
        }
    return out


def effective_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """供 UI/预览使用的字段视图（可含未确认候选）。"""
    fields = dict(item.get("fields") or {})
    meta = get_field_meta(item)
    for key, slot in meta.items():
        if not isinstance(slot, dict) or str(key).startswith("_"):
            continue
        if slot.get("status") == "ACCEPTED" and slot.get("accepted_value") is not None:
            fields[key] = slot["accepted_value"]
        elif key not in fields and slot.get("normalized_candidate") is not None:
            fields[key] = slot["normalized_candidate"]
    return fields


def classified_with_effective_fields(
    classified: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in classified:
        clone = dict(item)
        clone["fields"] = effective_fields(item)
        out.append(clone)
    return out


def rule_readable_fields(item: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """底稿/断言读字段：有三值元数据则只读 ACCEPTED；否则兼容旧 fields。"""
    if not item:
        return {}
    meta = get_field_meta(item)
    if meta:
        return verified_fields(item, allow_legacy_fields=False)
    return {
        k: v
        for k, v in (item.get("fields") or {}).items()
        if not str(k).startswith("_")
    }
