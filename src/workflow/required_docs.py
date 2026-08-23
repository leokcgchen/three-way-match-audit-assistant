"""底稿/测试必需的凭证类型：按识别内容判定，不用文件名。"""

from __future__ import annotations

from typing import Any

from src.workflow.classify import classify_from_ocr_text
from src.workflow.packet_cards import (
    UNRESOLVED,
    classify_page_text,
    detect_header_card_type,
    map_to_host_type,
)
from src.workflow.recipes import STEP_AMOUNT, STEP_CONTRACT, STEP_THREE_WAY

HOST_LABELS = {
    "contract": "合同",
    "order": "订单",
    "delivery": "发货单",
    "receipt": "签收/验收",
    "invoice": "发票",
    "payment": "回款",
}


def content_host_type(doc: dict[str, Any] | None) -> str:
    """从识别正文/拆包表头判断单据类型。没有正文则不猜文件名。"""
    if not isinstance(doc, dict):
        return ""
    sp = doc.get("source_packet") if isinstance(doc.get("source_packet"), dict) else {}
    card = str(sp.get("card_type") or "").strip()
    if card:
        host = map_to_host_type(card)
        if host and host != UNRESOLVED:
            return host
    raw = str(doc.get("raw_text") or "").strip()
    if not raw:
        return ""
    header = detect_header_card_type(raw)
    if header:
        host = map_to_host_type(header)
        if host and host != UNRESOLVED:
            return host
    primary = str((classify_page_text(raw) or {}).get("primary_type") or "")
    if primary and primary != UNRESOLVED:
        host = map_to_host_type(primary)
        if host and host != UNRESOLVED:
            return host
    ocr_type = classify_from_ocr_text(raw)
    if ocr_type and ocr_type != "other":
        return ocr_type
    return ""


def present_host_types(docs: list[dict[str, Any]] | None) -> list[str]:
    seen: list[str] = []
    for doc in docs or []:
        host = content_host_type(doc)
        if host and host not in seen:
            seen.append(host)
    return seen


def required_doc_slots(job: dict[str, Any] | None) -> list[dict[str, Any]]:
    """测试/出底稿必需的凭证槽：任一 any_of 类型出现即满足。"""
    steps = set((job or {}).get("plan", {}).get("required_steps") or [])
    slots: list[dict[str, Any]] = []

    def add(slot_id: str, label: str, any_of: tuple[str, ...]) -> None:
        if any(s["id"] == slot_id for s in slots):
            return
        slots.append({"id": slot_id, "label": label, "any_of": any_of})

    if STEP_CONTRACT in steps:
        add("contract", "合同", ("contract",))
    if STEP_THREE_WAY in steps or STEP_AMOUNT in steps:
        add("order", "订单", ("order",))
        add("invoice", "发票", ("invoice",))
    if STEP_THREE_WAY in steps:
        add("fulfillment", "签收或发货", ("receipt", "delivery"))
    return slots


def _hosts_for_required_slots(docs: list[dict[str, Any]] | None) -> set[str]:
    """缺单据门禁用：识别正文优先，没有正文时才用已分类的 doc_type。"""
    present = set(present_host_types(docs))
    for doc in docs or []:
        dt = str(doc.get("doc_type") or "")
        if dt in HOST_LABELS:
            present.add(dt)
    return present


def missing_required_docs(
    docs: list[dict[str, Any]] | None,
    job: dict[str, Any] | None,
) -> list[str]:
    present = _hosts_for_required_slots(docs)
    missing: list[str] = []
    for slot in required_doc_slots(job):
        if not present.intersection(slot["any_of"]):
            missing.append(str(slot["label"]))
    return missing


def present_doc_labels(docs: list[dict[str, Any]] | None) -> list[str]:
    labels: list[str] = []
    for host in present_host_types(docs):
        labels.append(HOST_LABELS.get(host, host))
    return labels


def _typed_hosts(docs: list[dict[str, Any]] | None) -> set[str]:
    """仅 doc_type（无正文证据）时的弱命中。"""
    out: set[str] = set()
    for doc in docs or []:
        if content_host_type(doc):
            continue
        dt = str(doc.get("doc_type") or "")
        if dt in HOST_LABELS:
            out.add(dt)
    return out


def has_unresolved_units(docs: list[dict[str, Any]] | None) -> bool:
    """存在未识别/other 且无正文类型命中的单元（黄灯）。"""
    for doc in docs or []:
        if content_host_type(doc):
            continue
        dt = str(doc.get("doc_type") or "").strip().lower()
        sp = doc.get("source_packet") if isinstance(doc.get("source_packet"), dict) else {}
        card = str(sp.get("card_type") or "").strip().lower()
        if card in {UNRESOLVED, "unresolved"} or dt in {UNRESOLVED, "unresolved", "other", ""}:
            # 空 doc_type 且无正文：不算存疑（可能是占位）；有拆包未识别或 other 才黄
            if card in {UNRESOLVED, "unresolved"} or dt in {UNRESOLVED, "unresolved", "other"}:
                return True
    return False


def slot_completeness_matrix(
    docs: list[dict[str, Any]] | None,
    job: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """样本×必需单据槽：present / uncertain / missing。

    - present：正文/表头或已分类 doc_type 命中该槽（与缺单门禁一致）
    - uncertain：该槽未命中，但本笔有未识别单元（可能就是缺的那张）
    - missing：明确没有，也无未识别单元可怀疑
    """
    present = _hosts_for_required_slots(docs)
    unresolved = has_unresolved_units(docs)
    rows: list[dict[str, Any]] = []
    for slot in required_doc_slots(job):
        any_of = set(slot["any_of"])
        if present.intersection(any_of):
            status = "present"
        elif unresolved:
            status = "uncertain"
        else:
            status = "missing"
        rows.append(
            {
                "id": slot["id"],
                "label": slot["label"],
                "status": status,
                "any_of": list(slot["any_of"]),
            }
        )
    return rows


def missing_request_lines(
    chain_id: str,
    matrix: list[dict[str, Any]] | None,
) -> list[str]:
    """向客户索要缺件的可读行。"""
    lines: list[str] = []
    for slot in matrix or []:
        st = str(slot.get("status") or "")
        if st == "missing":
            lines.append(f"{chain_id}：请提供「{slot.get('label')}」")
        elif st == "uncertain":
            lines.append(f"{chain_id}：请核对或补传清晰的「{slot.get('label')}」（当前类型存疑）")
    return lines
