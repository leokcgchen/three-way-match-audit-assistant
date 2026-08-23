"""上传/字段确认后：对缺失关键字段再跑一轮 LLM 补抽。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from src.models.field_values import rule_readable_fields, set_candidate
from src.workflow.classify import DOC_TYPE_TO_OCR

# 业务类型 → 必须尽量具备的字段（签收不要求金额：多数签收单故意不列金额）
_GAP_REQUIRED: dict[str, tuple[str, ...]] = {
    "contract": (
        "documentNo",
        "documentDate",
        "totalAmount",
        "quantity",
        "buyerName",
        "supplierName",
        "paymentTerms",
        "controlTransferTerms",
        "transportTerms",
    ),
    "order": (
        "documentNo",
        "orderNo",
        "documentDate",
        "totalAmount",
        "quantity",
        "supplierName",
        "paymentTerms",
    ),
    "delivery": (
        "documentNo",
        "documentDate",
        "deliveryDate",
        "quantity",
    ),
    "receipt": (
        "documentNo",
        "documentDate",
        "deliveryDate",
        "acceptanceDate",
        "quantity",
    ),
    "invoice": (
        "invoiceNo",
        "documentNo",
        "documentDate",
        "totalAmount",
        "quantity",
        "supplierName",
        "buyerName",
    ),
    "payment": ("documentNo", "documentDate", "totalAmount"),
}

# 启发式通常搞不定、才值得调 LLM 的语义字段
_LLM_WORTHY = frozenset(
    {
        "paymentTerms",
        "settlementTerms",
        "controlTransferTerms",
        "transportTerms",
        "buyerName",
        "supplierName",
        "items",
    }
)


def _filled(value: Any, *, zero_is_empty: bool = False) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan", "-"}:
        return False
    if zero_is_empty and text in {"0", "0.0", "0.00"}:
        return False
    return True


def missing_fields_for_doc(
    item: dict[str, Any],
    *,
    field_plan: Optional[dict[str, Any]] = None,
) -> list[str]:
    doc_type = str(item.get("doc_type") or "other")
    readable = rule_readable_fields(item)
    if field_plan is not None:
        from src.workflow.field_catalog import resolve_target_fields

        required = tuple(resolve_target_fields(doc_type, field_plan))
    else:
        planned = item.get("extract_field_keys")
        if isinstance(planned, list) and planned:
            required = tuple(str(x) for x in planned if str(x).strip())
        else:
            required = _GAP_REQUIRED.get(doc_type, ())
    missing: list[str] = []
    for k in required:
        if k in {"documentType", "items"}:
            continue
        zero_empty = k in {"totalAmount", "amount", "quantity", "taxAmount"}
        if not _filled(readable.get(k), zero_is_empty=zero_empty):
            missing.append(k)
    # 签收：到货/验收有其一即可
    if doc_type == "receipt":
        if _filled(readable.get("deliveryDate")) or _filled(readable.get("acceptanceDate")):
            missing = [k for k in missing if k not in {"deliveryDate", "acceptanceDate"}]
    if doc_type in {"contract", "order"}:
        if _filled(readable.get("paymentTerms")) or _filled(readable.get("settlementTerms")):
            missing = [k for k in missing if k not in {"paymentTerms", "settlementTerms"}]
        if _filled(readable.get("documentNo")) or _filled(readable.get("orderNo")):
            if _filled(readable.get("documentNo")) and "orderNo" in missing:
                missing = [k for k in missing if k != "orderNo"]
            if _filled(readable.get("orderNo")) and "documentNo" in missing:
                missing = [k for k in missing if k != "documentNo"]
    return missing


def _hydrate_raw_text(item: dict[str, Any]) -> str:
    """演示 seed / 仅写字段未跑 OCR 时：从 PDF 文字层补 raw_text。"""
    raw = str(item.get("raw_text") or "").strip()
    if raw:
        return raw
    path = str(item.get("path") or "").strip()
    if not path:
        return ""
    from src.legacy_ocr.ocr_adapter import _extract_pdf_text_layer

    text = _extract_pdf_text_layer(path)
    if text:
        item["raw_text"] = text
    return text


def _apply_heuristic_candidates(
    cur: dict[str, Any],
    raw: str,
    missing: list[str],
    before: dict[str, Any],
) -> list[str]:
    """无 LLM 或 LLM 未命中时，用启发式从正文补数字/编号类字段。"""
    from src.legacy_ocr.ocr_adapter import extract_fields_heuristically

    if not raw.strip() or not missing:
        return []
    heuristic = extract_fields_heuristically(raw)
    got: list[str] = []
    for key in missing:
        val = heuristic.get(key)
        zero_empty = key in {"totalAmount", "amount", "quantity", "taxAmount"}
        if not _filled(val, zero_is_empty=zero_empty):
            continue
        if _filled(before.get(key), zero_is_empty=zero_empty):
            continue
        set_candidate(
            cur,
            key,
            val,
            source="heuristic",
            extractor="field_gap_fill_heuristic",
        )
        got.append(key)
    return got


def _gap_fill_one_doc(
    item: dict[str, Any],
    *,
    field_plan: Optional[dict[str, Any]],
    force_fields: dict[str, list[str]],
    adapter: Any,
    llm_configured: bool,
) -> tuple[dict[str, Any], dict[str, Any], int, int]:
    """处理单份单据。返回 (doc, detail, filled_fields_delta, text_hydrated_delta)。"""
    cur = dict(item)
    name = str(cur.get("file_name") or "")
    doc_type = str(cur.get("doc_type") or "other")
    missing = list(
        force_fields.get(name) or missing_fields_for_doc(cur, field_plan=field_plan)
    )
    if not missing:
        return cur, {}, 0, 0

    raw = _hydrate_raw_text(cur)
    hydrated = 1 if raw and not str(item.get("raw_text") or "").strip() else 0
    if not raw.strip():
        detail = {
            "file_name": name,
            "doc_type": doc_type,
            "missing": missing,
            "filled": [],
            "skipped_reason": "无 OCR 正文（扫描件需先完整识别）",
            "llm_used": False,
        }
        return cur, detail, 0, hydrated

    ocr_type = DOC_TYPE_TO_OCR.get(doc_type, "other")
    before = rule_readable_fields(cur)
    got: list[str] = []

    # 1) 先启发式（快）——金额/编号/日期多数可命中，避免空等 LLM
    for key in _apply_heuristic_candidates(cur, raw, missing, before):
        if key not in got:
            got.append(key)

    still = [k for k in missing if k not in got]
    # 2) 仅对启发式搞不定的「语义字段」调 LLM；数字类再缺也不空等
    llm_need = [k for k in still if k in _LLM_WORTHY]
    llm_used = False
    if llm_need and llm_configured:
        llm_used = True
        before2 = rule_readable_fields(cur)
        patched = adapter.gap_fill_missing_fields(
            raw,
            ocr_type,
            dict(cur.get("fields") or {}),
            only_fields=llm_need,
        )
        for key in llm_need:
            val = patched.get(key)
            zero_empty = key in {"totalAmount", "amount", "quantity", "taxAmount"}
            if not _filled(val, zero_is_empty=zero_empty):
                continue
            if _filled(before2.get(key), zero_is_empty=zero_empty):
                continue
            set_candidate(
                cur,
                key,
                val,
                source="llm",
                extractor="field_gap_fill",
            )
            if key not in got:
                got.append(key)

    detail = {
        "file_name": name,
        "doc_type": doc_type,
        "missing": missing,
        "filled": got,
        "llm_used": llm_used,
        "llm_fields": llm_need if llm_used else [],
    }
    return cur, detail, len(got), hydrated


def gap_fill_classified_documents(
    classified: list[dict[str, Any]],
    *,
    force_fields: Optional[dict[str, list[str]]] = None,
    field_plan: Optional[dict[str, Any]] = None,
    max_workers: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """对列表中每份有正文且缺关键字段的单据补抽。

    策略：启发式优先 → 仅语义缺口调 LLM；多单据并行，避免「跟卡住一样」。
    """
    from config.settings import is_valid_api_credential
    from src.legacy_ocr import LegacyOcrAdapter

    adapter = LegacyOcrAdapter()
    llm_configured = is_valid_api_credential(adapter.llm_api_key)
    force_fields = force_fields or {}
    items = list(classified or [])
    if not items:
        return [], {
            "docs_touched": 0,
            "fields_filled": 0,
            "text_hydrated": 0,
            "llm_configured": llm_configured,
            "skipped_no_text": [],
            "details": [],
        }

    # 保持顺序：先收集需处理的索引
    results: list[Optional[dict[str, Any]]] = [None] * len(items)
    details_by_idx: dict[int, dict[str, Any]] = {}
    filled_docs = 0
    filled_fields = 0
    text_hydrated = 0
    skipped: list[str] = []

    work_indices = list(range(len(items)))
    workers = max(1, min(int(max_workers or 1), len(work_indices), 4))

    def _run(i: int) -> tuple[int, dict[str, Any], dict[str, Any], int, int]:
        doc, detail, n_filled, n_hyd = _gap_fill_one_doc(
            items[i],
            field_plan=field_plan,
            force_fields=force_fields,
            adapter=adapter,
            llm_configured=llm_configured,
        )
        return i, doc, detail, n_filled, n_hyd

    if workers == 1:
        for i in work_indices:
            idx, doc, detail, n_filled, n_hyd = _run(i)
            results[idx] = doc
            if detail:
                details_by_idx[idx] = detail
                if detail.get("skipped_reason"):
                    skipped.append(str(detail.get("file_name") or detail.get("doc_type") or ""))
            if n_filled:
                filled_docs += 1
                filled_fields += n_filled
            text_hydrated += n_hyd
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_run, i) for i in work_indices]
            for fut in as_completed(futs):
                idx, doc, detail, n_filled, n_hyd = fut.result()
                results[idx] = doc
                if detail:
                    details_by_idx[idx] = detail
                    if detail.get("skipped_reason"):
                        skipped.append(
                            str(detail.get("file_name") or detail.get("doc_type") or "")
                        )
                if n_filled:
                    filled_docs += 1
                    filled_fields += n_filled
                text_hydrated += n_hyd

    out = [results[i] if results[i] is not None else items[i] for i in range(len(items))]
    details = [details_by_idx[i] for i in sorted(details_by_idx.keys())]
    summary = {
        "docs_touched": filled_docs,
        "fields_filled": filled_fields,
        "text_hydrated": text_hydrated,
        "llm_configured": llm_configured,
        "skipped_no_text": skipped,
        "details": details,
        "parallel_workers": workers,
    }
    return out, summary
