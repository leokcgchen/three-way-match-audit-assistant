"""审阅流程引擎（与 Streamlit 同源，无 UI 依赖）。"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.workflow.classify import (
    DOC_TYPE_TO_OCR,
    classify_document,
    fallback_fields_from_filename,
    merge_fields,
)

_COMPLETENESS_KEYS: dict[str, tuple[str, ...]] = {
    "contract": (
        "paymentTerms",
        "contractNo",
        "documentNo",
        "documentDate",
        "controlTransferTerms",
        "transportTerms",
    ),
    "order": ("totalAmount", "supplierName", "documentNo", "paymentTerms", "quantity"),
    "delivery": ("documentDate", "documentNo", "deliveryDate", "quantity"),
    "receipt": (
        "documentDate",
        "deliveryDate",
        "acceptanceDate",
        "documentNo",
        "supplierName",
        "quantity",
    ),
    "invoice": (
        "postingDate",
        "totalAmount",
        "invoiceNo",
        "documentNo",
        "documentDate",
        "supplierName",
    ),
    "payment": ("documentDate", "totalAmount", "documentNo"),
}

_PAYMENT_TERM_RE = re.compile(
    r"(?:"
    r"签收后\s*\d+\s*[日天]"
    r"|验收后\s*\d+\s*[日天]"
    r"|票到\s*\d+\s*[日天]"
    r"|开票后\s*\d+\s*[日天]"
    r"|(?:增值税)?发票开具之日起\s*\d+\s*[日天]内?"
    r"|开具之日起\s*\d+\s*[日天]内?"
    r"|付款期限[^\n。；]{0,40}\d+\s*[日天]"
    r")"
)

DEFAULT_CUTOFF_JOB_ROOT = "D:/Dev/Temp/cutoff_jobs"
# pipeline.py → src/workflow → 仓库根
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def job_root() -> Path:
    """任务根目录；每次读取环境变量，便于单测 monkeypatch，避免写进正式库。"""
    return Path(os.getenv("CUTOFF_JOB_ROOT") or DEFAULT_CUTOFF_JOB_ROOT)


# 兼容旧引用；值在导入时冻结，新代码请用 job_root()
_JOB_ROOT = job_root()


def job_workdir(job_id: str) -> Path:
    """任务工作目录；优先 D:/Dev/Temp/cutoff_jobs/{job_id}（上传/OCR 中间件）。"""
    try:
        root = job_root()
        root.mkdir(parents=True, exist_ok=True)
        work = root / job_id
        work.mkdir(parents=True, exist_ok=True)
        return work
    except OSError:
        base = Path(tempfile.gettempdir()) / "cutoff_jobs" / job_id
        base.mkdir(parents=True, exist_ok=True)
        return base


def workbook_output_dir() -> Path:
    """底稿 xlsx 输出目录：默认项目根；可用 WORKBOOK_OUTPUT_DIR 覆盖。"""
    raw = (os.getenv("WORKBOOK_OUTPUT_DIR") or "").strip()
    if raw:
        out = Path(raw)
    else:
        out = _PROJECT_ROOT
    out.mkdir(parents=True, exist_ok=True)
    return out


def file_fingerprint(filename: str, content: bytes) -> str:
    raw = f"{filename}:{hashlib.md5(content).hexdigest()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _field_filled(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in {"none", "null", "nan", "-"}


def _completeness_score(doc_type: str, fields: dict[str, Any]) -> int:
    keys = _COMPLETENESS_KEYS.get(doc_type, ())
    return sum(1 for k in keys if _field_filled(fields.get(k)))


def merge_same_type_docs(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for item in items:
        doc_type = item.get("doc_type") or "other"
        if doc_type == "other":
            continue
        score = _completeness_score(doc_type, item.get("fields") or {})
        prev = best.get(doc_type)
        if prev is None or score > prev.get("score", 0):
            best[doc_type] = {**item, "score": score}
    return best


def _extract_payment_terms_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = _PAYMENT_TERM_RE.search(str(text))
    return m.group(0).replace(" ", "") if m else None


def ensure_payment_terms(fields: dict[str, Any], raw_text: str = "") -> dict[str, Any]:
    out = dict(fields or {})

    def _valid_payment(val: Any) -> bool:
        if not _field_filled(val):
            return False
        return bool(_PAYMENT_TERM_RE.search(str(val)))

    if _valid_payment(out.get("paymentTerms")) or _valid_payment(out.get("settlementTerms")):
        if not _valid_payment(out.get("paymentTerms")) and _valid_payment(
            out.get("settlementTerms")
        ):
            out["paymentTerms"] = out["settlementTerms"]
        return out
    for bad_key in ("paymentTerms", "settlementTerms"):
        if bad_key in out and not _valid_payment(out.get(bad_key)):
            out.pop(bad_key, None)
    found = _extract_payment_terms_from_text(raw_text)
    if not found:
        for key in ("remarks", "controlTransferTerms"):
            found = _extract_payment_terms_from_text(str(out.get(key) or ""))
            if found:
                break
    if found:
        out["paymentTerms"] = found
        out.setdefault("settlementTerms", found)
    return out


def _pick_payment_terms(*field_dicts: dict[str, Any]) -> Optional[str]:
    for fields in field_dicts:
        val = fields.get("paymentTerms") or fields.get("settlementTerms")
        if _field_filled(val):
            return str(val).strip()
        for key in ("remarks", "rawText", "controlTransferTerms"):
            found = _extract_payment_terms_from_text(str(fields.get(key) or ""))
            if found:
                return found
    return None


def _pick_receipt_date(fields: dict[str, Any]) -> Optional[str]:
    from src.utils.date_extractor import pick_receipt_date_from_fields

    return pick_receipt_date_from_fields(fields)


def find_latest_receipt_index(classified: list[dict[str, Any]]) -> Optional[int]:
    best_idx: Optional[int] = None
    best_date = ""
    for i, item in enumerate(classified):
        if item.get("doc_type") != "receipt":
            continue
        name = str(item.get("file_name") or "")
        if "验收" in name or "签收" in name:
            rd = _pick_receipt_date(item.get("fields") or {}) or ""
            if rd >= best_date:
                best_date = rd
                best_idx = i
    if best_idx is not None:
        return best_idx
    for i, item in enumerate(classified):
        if item.get("doc_type") != "receipt":
            continue
        rd = _pick_receipt_date(item.get("fields") or {}) or ""
        if rd >= best_date:
            best_date = rd
            best_idx = i
    return best_idx


def save_bytes_to_workdir(folder: Path, filename: str, content: bytes) -> Path:
    """将上传字节写入任务目录；同名同内容复用路径。"""
    return _save_bytes_to_workdir(folder, filename, content)


def _save_bytes_to_workdir(folder: Path, filename: str, content: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    target = folder / safe_name
    if target.exists():
        try:
            if target.read_bytes() == content:
                return target
        except OSError:
            pass
        stem, suffix = target.stem, target.suffix
        n = 1
        while True:
            candidate = folder / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                target = candidate
                break
            n += 1
    target.write_bytes(content)
    return target


def _process_one_file(
    *,
    filename: str,
    content: bytes,
    folder: Path,
    slot_hint: str = "",
    fingerprint: str = "",
    forced_doc_type: str = "",
    target_fields: Optional[list[str]] = None,
    fast_batch: bool = False,
    allow_llm_field_supplement: bool = False,
    source_packet: Optional[dict[str, Any]] = None,
    packet_keys: Optional[dict[str, Any]] = None,
    declared_business_ids: Optional[list[str]] = None,
    demo_delay_sec: Optional[float] = None,
) -> dict[str, Any]:
    from src.legacy_ocr import LegacyOcrAdapter
    from src.models.field_values import seed_field_meta
    from src.workflow.field_catalog import resolve_target_fields

    fp = fingerprint or file_fingerprint(filename, content)
    try:
        path = _save_bytes_to_workdir(folder, filename, content)
        from src.workflow.demo_ocr_cache import apply_demo_hit, lookup_demo_ocr

        cached = lookup_demo_ocr(filename)
        if cached:
            delay = 0.18 if demo_delay_sec is None else float(demo_delay_sec)
            demo_item = apply_demo_hit(
                filename=filename,
                path=str(path),
                fingerprint=fp,
                slot_hint=slot_hint,
                payload=cached,
                source_packet=source_packet,
                packet_keys=packet_keys,
                delay_sec=delay,
            )
            if declared_business_ids:
                demo_item["declared_business_ids"] = list(declared_business_ids)
            return demo_item
        adapter = LegacyOcrAdapter()
        ocr = adapter.recognize_document(str(path), "other", allow_degraded=True)
        raw_text = str(ocr.get("rawText") or "")
        ocr_source = str(ocr.get("source") or "unknown")
        ocr_error: Optional[str] = None
        if ocr_source == "mock" and adapter.is_api_configured():
            ocr_error = "OCR 降级为 Mock（请检查千帆服务状态）"
        elif ocr_source == "ocr_failed":
            ocr_error = "千帆 OCR 暂时不可用，已用文件名兜底"

        if forced_doc_type and forced_doc_type != "other":
            final_type = forced_doc_type
        else:
            final_type = classify_document(filename, raw_text, slot_hint=slot_hint)
        ocr_type = DOC_TYPE_TO_OCR.get(final_type, "other")
        keys = list(target_fields or resolve_target_fields(final_type, None))
        if ocr_source == "mock" and ocr.get("extractedFields"):
            fields = dict(ocr.get("extractedFields") or {})
        elif ocr_source == "ocr_failed" or not raw_text.strip():
            fields = {}
        else:
            fields = dict(
                adapter.extract_fields(
                    raw_text,
                    ocr_type,
                    target_fields=keys,
                    fast_batch=fast_batch,
                    allow_llm_field_supplement=allow_llm_field_supplement,
                )
                or {}
            )
        fields["documentType"] = ocr_type

        fallback = fallback_fields_from_filename(filename, final_type)
        if packet_keys:
            fallback = merge_fields(dict(packet_keys), fallback)
        fields = merge_fields(fields, fallback)
        if final_type in {"contract", "order"}:
            fields = ensure_payment_terms(fields, raw_text)

        doc_item = {
            "file_name": filename,
            "path": str(path),
            "doc_type": final_type,
            "upload_slot": slot_hint,
            "fields": fields,
            "raw_text": raw_text,
            "ocr_source": ocr_source,
            "confidence": ocr.get("confidence"),
            "text_blocks": ocr.get("textBlocks") or [],
            "error": ocr_error,
            "file_fingerprint": fp,
            "extract_field_keys": keys,
            "type_uncertain": final_type == "other",
        }
        if source_packet:
            doc_item["source_packet"] = dict(source_packet)
        if declared_business_ids:
            doc_item["declared_business_ids"] = list(declared_business_ids)
        if ocr.get("ocr_image_path"):
            doc_item["ocr_image_path"] = str(ocr["ocr_image_path"])
        if ocr.get("preprocess"):
            doc_item["preprocess"] = dict(ocr["preprocess"])
        seed_field_meta(
            doc_item,
            source=ocr_source or "ocr",
            extractor="recognize_then_extract",
        )
        from src.workflow.field_resolution.evidence_inventory import attach_document_evidence

        attach_document_evidence(doc_item)
        try:
            from src.workflow.amount_ambiguity import scan_document

            scan_document(doc_item)
            from config.settings import settings as _settings

            if str(getattr(_settings, "AMOUNT_AMBIGUITY_ENRICH_ON_PROCESS", "0") or "0").strip().lower() not in {
                "0",
                "false",
                "off",
                "no",
            }:
                from src.workflow.amount_ambiguity import enrich_document_ambiguities

                enrich_document_ambiguities(doc_item)
        except Exception:  # noqa: BLE001
            pass
        return doc_item
    except Exception as exc:  # noqa: BLE001
        final_type = forced_doc_type or classify_document(filename, "", slot_hint=slot_hint)
        fields = fallback_fields_from_filename(filename, final_type)
        failed_item = {
            "file_name": filename,
            "path": "",
            "doc_type": final_type,
            "upload_slot": slot_hint,
            "fields": fields,
            "raw_text": "",
            "ocr_source": "failed",
            "error": f"处理异常：{exc}",
            "file_fingerprint": fp,
            "type_uncertain": final_type == "other",
        }
        if declared_business_ids:
            failed_item["declared_business_ids"] = list(declared_business_ids)
        from src.workflow.field_resolution.evidence_inventory import attach_document_evidence

        attach_document_evidence(failed_item)
        return failed_item


def process_uploaded_files(
    job_id: str,
    files: list[dict[str, Any]],
    *,
    existing: Optional[list[dict[str, Any]]] = None,
    force: bool = False,
    field_plan: Optional[dict[str, Any]] = None,
    progress_callback: Optional[Any] = None,
    skip_gap_fill: Optional[bool] = None,
    allow_llm_field_supplement: bool = False,
) -> list[dict[str, Any]]:
    """保存文件 → OCR → 分类 → 抽字段 → seed_field_meta；并发处理。

    追加语义：本批未触及的旧单据一律保留；同指纹复用；同名不同内容则替换。
    禁止只返回本批结果而丢掉已有 classified（否则 GOSPD 分笔第二笔会盖掉第一笔）。
    """
    from src.workflow.field_catalog import resolve_target_fields

    folder = job_workdir(job_id)
    existing = list(existing or [])
    by_fp = {
        str(item.get("file_fingerprint") or ""): item
        for item in existing
        if item.get("file_fingerprint")
    }

    reused: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    seen: set[str] = set()
    batch_fps: set[str] = set()
    batch_names: set[str] = set()

    for spec in files:
        filename = str(spec.get("filename") or spec.get("file_name") or "")
        content = spec.get("content")
        if not filename or content is None:
            continue
        if not isinstance(content, (bytes, bytearray)):
            content = bytes(content)
        fp = file_fingerprint(filename, bytes(content))
        if fp in seen:
            continue
        seen.add(fp)
        batch_fps.add(fp)
        batch_names.add(filename)
        slot_hint = str(spec.get("slot_hint") or "")
        forced_type = str(spec.get("doc_type") or "").strip()
        prev = None if force else by_fp.get(fp)
        if prev is not None:
            item = dict(prev)
            item["file_name"] = filename
            item["file_fingerprint"] = fp
            path = str(item.get("path") or "")
            if not path or not Path(path).exists():
                item["path"] = str(_save_bytes_to_workdir(folder, filename, bytes(content)))
            reused.append(item)
        else:
            pending.append(
                {
                    "filename": filename,
                    "content": bytes(content),
                    "slot_hint": slot_hint,
                    "fingerprint": fp,
                    "doc_type": forced_type,
                    "source_packet": spec.get("source_packet"),
                    "packet_keys": spec.get("keys") or spec.get("packet_keys"),
                    "declared_business_ids": list(spec.get("declared_business_ids") or []),
                }
            )

    processed: list[dict[str, Any]] = []
    batch_fast = os.getenv("OCR_BATCH_FAST", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    if skip_gap_fill is None:
        skip_gap_fill = batch_fast
    if pending:
        from src.workflow.demo_ocr_cache import demo_file_delay_sec, lookup_demo_ocr

        workers = min(6, len(pending))
        done = 0
        total = len(pending)
        cache_hits = sum(1 for p in pending if lookup_demo_ocr(str(p.get("filename") or "")))
        demo_delay = demo_file_delay_sec(total, workers=workers) if cache_hits == total else None

        def _submit_one(p: dict[str, Any]):
            return _process_one_file(
                filename=p["filename"],
                content=p["content"],
                folder=folder,
                slot_hint=p["slot_hint"],
                fingerprint=p["fingerprint"],
                forced_doc_type=p.get("doc_type") or "",
                source_packet=p.get("source_packet") if isinstance(p.get("source_packet"), dict) else None,
                packet_keys=p.get("packet_keys") if isinstance(p.get("packet_keys"), dict) else None,
                declared_business_ids=list(p.get("declared_business_ids") or []),
                target_fields=resolve_target_fields(
                    p.get("doc_type")
                    or classify_document(p["filename"], "", slot_hint=p["slot_hint"]),
                    field_plan,
                ),
                fast_batch=batch_fast,
                allow_llm_field_supplement=allow_llm_field_supplement,
                demo_delay_sec=demo_delay,
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_submit_one, p): p["filename"] for p in pending}
            for fut in as_completed(futures):
                fname = futures[fut]
                item = fut.result()
                processed.append(item)
                done += 1
                if progress_callback:
                    progress_callback(done, total, fname)

    # 保留本批未触及的旧单据（同名/同指纹由 reused|processed 覆盖）
    kept: list[dict[str, Any]] = []
    for item in existing:
        fp = str(item.get("file_fingerprint") or "")
        name = str(item.get("file_name") or "")
        if fp and fp in batch_fps:
            continue
        if name and name in batch_names:
            continue
        kept.append(item)

    merged = kept + reused + processed
    if processed and all(x.get("demo_ocr_cache") for x in processed):
        skip_gap_fill = True
    # 批处理默认跳过 gap-fill（字段页可再补抽），避免 OCR 后第三遍 LLM 拖慢
    if skip_gap_fill:
        return merged
    # 有缺失关键字段时再跑一轮 LLM 补抽（启发式/首轮 LLM 漏提时的兜底）
    try:
        from src.workflow.field_gap_fill import gap_fill_classified_documents

        # 仅对本批新处理的单据补抽，避免全量重跑拖慢追加上传
        if processed:
            by_name = {str(x.get("file_name") or ""): i for i, x in enumerate(merged)}
            only = [x for x in processed if x.get("file_name")]
            filled, _summary = gap_fill_classified_documents(only, field_plan=field_plan)
            for item in filled:
                idx = by_name.get(str(item.get("file_name") or ""))
                if idx is not None:
                    merged[idx] = item
    except TypeError:
        # 旧签名无 field_plan 时降级
        try:
            from src.workflow.field_gap_fill import gap_fill_classified_documents

            if processed:
                by_name = {str(x.get("file_name") or ""): i for i, x in enumerate(merged)}
                only = [x for x in processed if x.get("file_name")]
                filled, _summary = gap_fill_classified_documents(only)
                for item in filled:
                    idx = by_name.get(str(item.get("file_name") or ""))
                    if idx is not None:
                        merged[idx] = item
        except Exception:
            pass
    except Exception:
        pass
    return merged


def strip_invoice_ocr_posting(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in classified:
        if item.get("doc_type") != "invoice":
            continue
        fields = dict(item.get("fields") or {})
        fields.pop("postingDate", None)
        item["fields"] = fields
    return classified


def apply_ledger_to_classified_list(
    classified: list[dict[str, Any]],
    ledger_rows: list[dict[str, Any]],
    ledger_mapping: dict[str, Any],
    *,
    sample_population: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    import pandas as pd

    from src.legacy_ocr.ledger_parser import (
        apply_ledger_to_classified,
        build_ledger_index,
        collect_workflow_biz_keys,
    )

    if not ledger_rows or not ledger_mapping:
        for item in classified:
            item["ledger_evaluated"] = False
        return classified
    if sample_population:
        from src.workflow.sample_scope import resolve_sample_business_identity

        for item in classified:
            identity = resolve_sample_business_identity(item, sample_population)
            if identity.get("status") != "MATCHED":
                continue
            item["sample_business_id"] = identity["sample_business_id"]
            item["business_index_source"] = identity["source"]
            item["business_index_candidates"] = identity["candidates"]
    df = pd.DataFrame(ledger_rows)
    index = build_ledger_index(df, ledger_mapping)
    workflow_keys = collect_workflow_biz_keys(classified)
    cleaned = strip_invoice_ocr_posting(list(classified))
    updated = apply_ledger_to_classified(
        cleaned,
        index,
        order_biz_keys=workflow_keys,
    )
    for item in updated:
        item["ledger_evaluated"] = True
    return updated


def run_evidence(
    classified: list[dict[str, Any]],
    *,
    existing_advisory: Optional[list[dict[str, Any]]] = None,
    with_llm_disambiguation: Optional[bool] = None,
    require_contract: bool = True,
    require_ledger: bool = True,
) -> dict[str, Any]:
    from config.settings import settings
    from src.evidence_match import build_evidence_chain
    from src.legacy_ocr.ledger_parser import (
        collect_document_biz_keys,
        compact_biz_id,
        extract_biz_ids_from_filename,
    )
    from src.llm.verifier import evidence_blob_from_documents

    active = [x for x in classified if not x.get("excluded_from_match")]
    manual_groups: dict[str, list[dict[str, Any]]] = {}
    for item in active:
        group_id = str(item.get("business_group_id") or "").strip()
        if group_id:
            manual_groups.setdefault(group_id, []).append(item)

    def _group_matches_ledger(group_docs: list[dict[str, Any]], ledger_id: Any) -> bool:
        ledger_key = compact_biz_id(ledger_id)
        if not ledger_key:
            return False
        for doc in group_docs:
            direct_keys: list[Any] = [
                doc.get("business_group_id"),
                doc.get("sample_business_id"),
            ]
            business_ids = doc.get("business_ids")
            if isinstance(business_ids, (list, tuple, set)):
                direct_keys.extend(business_ids)
            elif business_ids:
                direct_keys.append(business_ids)
            if any(compact_biz_id(key) == ledger_key for key in direct_keys if key):
                return True
            fields = dict(doc.get("fields") or {})
            keys = collect_document_biz_keys(fields)
            keys.extend(extract_biz_ids_from_filename(str(doc.get("file_name") or "")))
            # Keep the selection check aligned with evidence linker: an
            # explicit field is a valid cross-reference even if it is shorter
            # than the OCR parser's business-id heuristic.
            keys.extend(
                fields.get(name)
                for name in ("documentNo", "contractNo", "orderNo", "invoiceNo", "warehouseNo")
                if fields.get(name) is not None
            )
            if any(compact_biz_id(key) == ledger_key for key in keys):
                return True
        return False

    verified_groups: dict[str, list[str]] = {}
    for group_id, group_docs in manual_groups.items():
        for doc in group_docs:
            ledger_id = doc.get("ledger_matched_biz_id")
            if ledger_id and _group_matches_ledger(group_docs, ledger_id):
                verified_groups.setdefault(group_id, []).append(str(ledger_id))

    selection_issue = ""
    selected_ledger_id: Optional[str] = None
    if len(verified_groups) == 1:
        target_group_id, ledger_ids = next(iter(verified_groups.items()))
        active = list(manual_groups[target_group_id])
        selected_ledger_id = ledger_ids[0]
    elif len(verified_groups) > 1:
        active = []
        selection_issue = "人工业务分组存在多个可验证序时账匹配，无法确定当前证据链（歧义）"
    else:
        # Unassigned documents are a separate machine-matching population and
        # must not supplement a manually assigned packet.
        active = [
            x for x in active if not str(x.get("business_group_id") or "").strip()
        ]
    inv = next((x for x in active if x.get("doc_type") == "invoice"), None)
    result = build_evidence_chain(
        active,
        ledger_matched_biz_id=selected_ledger_id or (inv or {}).get("ledger_matched_biz_id"),
        ledger_posting_date=(inv or {}).get("ledger_posting_date"),
        require_contract=require_contract,
        require_ledger=require_ledger,
    )
    if selection_issue:
        result.status = "FAIL"
        result.issue_description = selection_issue
        result.human_readable_summary = selection_issue
    payload = result.model_dump()
    if with_llm_disambiguation is None:
        flag = (
            os.getenv("MATCHING_LLM_DISAMBIGUATION")
            or getattr(settings, "MATCHING_LLM_DISAMBIGUATION", "1")
            or "1"
        )
        with_llm_disambiguation = str(flag).strip().lower() not in {
            "0",
            "false",
            "off",
            "no",
        }
    if with_llm_disambiguation:
        try:
            from src.evidence_match.disambiguation import llm_matching_disambiguation

            payload["llm_disambiguation"] = llm_matching_disambiguation(active, payload)
        except Exception as exc:  # noqa: BLE001
            payload["llm_disambiguation"] = {
                "ran": False,
                "proposals": [],
                "notes": [f"消歧异常：{exc}"],
                "blocks_downstream": False,
            }
    else:
        payload["llm_disambiguation"] = {
            "ran": False,
            "proposals": [],
            "notes": ["跳过 LLM 消歧（复跑/显式关闭）"],
            "blocks_downstream": False,
        }
    # 受控补缺链：消歧主张统一经 verifier 闸门写入 advisory_candidates
    if isinstance(payload.get("llm_disambiguation"), dict):
        try:
            from src.audit.gap_fill_orchestrator import ingest_verified_claims

            dis = payload["llm_disambiguation"]
            claims = list(dis.get("proposals") or []) + list(dis.get("rejected") or [])
            if claims:
                blob = evidence_blob_from_documents(
                    [
                        {
                            "file_name": x.get("file_name"),
                            "doc_type": x.get("doc_type"),
                            "raw_text": x.get("raw_text") or x.get("ocr_text") or "",
                        }
                        for x in active
                    ]
                )
                ingest = ingest_verified_claims(
                    existing_advisory or [],
                    task_type="MATCHING_DISAMBIGUATION",
                    claims=claims,
                    full_text=blob,
                    trigger_reasons=["MATCHING_AMBIGUITY"],
                    business_id=",".join(
                        str(k) for k in (payload.get("anchor_keys") or [])[:3]
                    ),
                    kind="fact",
                    require_excerpt=True,
                )
                payload["advisory_candidates"] = ingest["store"]
                payload["advisory_ingest"] = {
                    "proposed": len(ingest.get("proposed") or []),
                    "dropped": len(ingest.get("dropped") or []),
                    "counts": ingest.get("counts") or {},
                }
            elif existing_advisory is not None:
                payload["advisory_candidates"] = list(existing_advisory)
        except Exception as exc:  # noqa: BLE001
            payload["advisory_ingest"] = {"error": str(exc)}
            if existing_advisory is not None:
                payload["advisory_candidates"] = list(existing_advisory)
    elif existing_advisory is not None:
        payload["advisory_candidates"] = list(existing_advisory)
    return payload


def seed_phase2(
    classified: list[dict[str, Any]],
    evidence: dict[str, Any],
    existing_relations: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    from config.settings import settings
    from src.audit.duplicate_detector import detect_duplicates
    from src.audit.relation_proposer import propose_relations_from_evidence

    rel_flag = (
        os.getenv("ENABLE_RELATION_CANDIDATES")
        or getattr(settings, "ENABLE_RELATION_CANDIDATES", "1")
        or "1"
    )
    dup_flag = (
        os.getenv("ENABLE_DUPLICATE_DETECTION")
        or getattr(settings, "ENABLE_DUPLICATE_DETECTION", "1")
        or "1"
    )
    relations: list[dict[str, Any]] = []
    if str(rel_flag).strip().lower() not in {"0", "false", "off", "no"}:
        relations = propose_relations_from_evidence(
            classified,
            evidence,
            existing=existing_relations or [],
        )
    duplicates: dict[str, Any] = {"ran": False, "findings": [], "summary": {"total": 0}}
    if str(dup_flag).strip().lower() not in {"0", "false", "off", "no"}:
        duplicates = detect_duplicates(classified)
    return {"relations": relations, "duplicates": duplicates}


def run_amount(
    classified: list[dict[str, Any]],
    *,
    existing_advisory: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    from src.amount_test import run_amount_test

    inv = next((x for x in classified if x.get("doc_type") == "invoice"), None)
    order = next((x for x in classified if x.get("doc_type") == "order"), None)
    ledger_amt = None
    sales_order_no = ""
    voucher_no = ""
    customer_name = ""
    if inv and inv.get("ledger_amount") is not None:
        ledger_amt = inv.get("ledger_amount")
    if inv:
        sales_order_no = str(
            (inv.get("fields") or {}).get("orderNo")
            or (inv.get("fields") or {}).get("salesOrderNo")
            or ""
        )
        voucher_no = str(inv.get("ledger_voucher") or "")
        customer_name = str((inv.get("fields") or {}).get("buyerName") or "")
    if order and not sales_order_no:
        sales_order_no = str(
            (order.get("fields") or {}).get("orderNo")
            or (order.get("fields") or {}).get("documentNo")
            or ""
        )
    result = run_amount_test(
        classified,
        ledger_amount=ledger_amt,
        sales_order_no=sales_order_no,
        voucher_no=voucher_no,
        customer_name=customer_name,
        existing_advisory=existing_advisory,
    )
    dump = result.model_dump()
    if "advisory_candidates" not in dump or dump.get("advisory_candidates") is None:
        dump["advisory_candidates"] = list(existing_advisory or [])
    return dump


def run_contract(
    classified: list[dict[str, Any]],
    *,
    existing_advisory: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    from src.contract_terms import run_contract_terms_test

    result = run_contract_terms_test(
        classified,
        existing_advisory=existing_advisory,
    )
    dump = result.model_dump()
    extracted = dump.get("extracted") if isinstance(dump.get("extracted"), dict) else {}
    adv = extracted.get("advisory_candidates")
    if isinstance(adv, list):
        dump["advisory_candidates"] = adv
    elif existing_advisory is not None:
        dump["advisory_candidates"] = list(existing_advisory)
    return dump


def assemble_three_way_request(
    merged: dict[str, dict[str, Any]],
    *,
    selected_receipt_idx: Optional[int] = None,
    classified: Optional[list[dict[str, Any]]] = None,
    manual: Optional[dict[str, Any]] = None,
) -> Any:
    from src.three_way_match.matcher import build_request_from_ocr_fields

    manual = manual or {}
    contract_fields = dict((merged.get("contract") or {}).get("fields") or {})
    order_fields = dict((merged.get("order") or {}).get("fields") or {})
    receipt_fields = dict((merged.get("receipt") or {}).get("fields") or {})
    invoice_fields = dict((merged.get("invoice") or {}).get("fields") or {})

    if not receipt_fields and merged.get("delivery"):
        receipt_fields = dict((merged.get("delivery") or {}).get("fields") or {})

    if (
        selected_receipt_idx is not None
        and classified is not None
        and 0 <= selected_receipt_idx < len(classified)
    ):
        chosen = classified[selected_receipt_idx]
        if chosen.get("doc_type") in {"receipt", "delivery"}:
            receipt_fields = dict(chosen.get("fields") or {})

    payment = manual.get("payment_terms") or _pick_payment_terms(
        contract_fields, order_fields
    )
    if payment:
        order_fields["paymentTerms"] = payment
        contract_fields["paymentTerms"] = payment

    contract_no = (
        manual.get("contract_no")
        or contract_fields.get("contractNo")
        or contract_fields.get("documentNo")
        or order_fields.get("contractNo")
    )
    if contract_no:
        order_fields["contractNo"] = contract_no
        contract_fields["contractNo"] = contract_no

    if manual.get("supplier"):
        order_fields["supplierName"] = manual["supplier"]
        receipt_fields.setdefault("supplierName", manual["supplier"])
        invoice_fields.setdefault("supplierName", manual["supplier"])

    if manual.get("order_amount") is not None:
        order_fields["totalAmount"] = manual["order_amount"]
    if manual.get("receipt_date"):
        receipt_fields["deliveryDate"] = manual["receipt_date"]
        receipt_fields["documentDate"] = manual["receipt_date"]
    if manual.get("receipt_amount") is not None:
        receipt_fields["totalAmount"] = manual["receipt_amount"]
    # 禁止用订单金额填补签收/发票（避免同源制造三单一致）
    if manual.get("posting_date"):
        invoice_fields["postingDate"] = manual["posting_date"]
    if manual.get("invoice_amount") is not None:
        invoice_fields["totalAmount"] = manual["invoice_amount"]

    if not order_fields.get("paymentTerms") and contract_fields.get("paymentTerms"):
        order_fields["paymentTerms"] = contract_fields["paymentTerms"]

    if not manual.get("receipt_date") and not _pick_receipt_date(receipt_fields):
        docs: list[dict[str, Any]] = []
        sources = list(classified or [])
        if not sources:
            for role_key in ("contract", "order", "receipt", "delivery", "invoice"):
                item = merged.get(role_key)
                if item:
                    sources.append(item)
        for item in sources:
            text = str(item.get("raw_text") or "")
            if not text.strip():
                continue
            docs.append(
                {
                    "doc_type": item.get("doc_type") or "",
                    "file_name": item.get("file_name") or "",
                    "raw_text": text,
                }
            )
        if docs:
            try:
                from src.llm.batch_assist import enrich_receipt_fields_with_cutoff_llm

                receipt_fields, llm_notes = enrich_receipt_fields_with_cutoff_llm(
                    receipt_fields,
                    docs,
                    contract_fields=contract_fields,
                    business_id=str(
                        order_fields.get("documentNo")
                        or order_fields.get("contractNo")
                        or ""
                    ),
                )
                if llm_notes:
                    receipt_fields["_cutoffLlmNotes"] = llm_notes
            except Exception:  # noqa: BLE001
                pass

    return build_request_from_ocr_fields(order_fields, receipt_fields, invoice_fields)


def serialize_three_way_result(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw)
    for key in ("match_result", "match_request", "cutoff_result"):
        val = data.get(key)
        if val is not None and hasattr(val, "model_dump"):
            data[key] = val.model_dump()
    return data


def _apply_fulfillment_decision(
    result: dict[str, Any], fulfillment: dict[str, Any]
) -> dict[str, Any]:
    """用一对多履约语义协调旧标量结论，但不掩盖无关的明确失败。"""

    if not fulfillment.get("rows"):
        return result
    match_result = result.get("match_result")
    if not isinstance(match_result, dict):
        return result
    flags = {str(flag) for flag in (fulfillment.get("flags") or [])}
    light = str(fulfillment.get("light") or "").upper()
    comparisons = list(match_result.get("comparisons") or [])
    failed_fields = {
        str(item.get("field_name") or "")
        for item in comparisons
        if isinstance(item, dict) and item.get("is_consistent") is False
    }
    only_fulfillment_failures = failed_fields <= {"quantity", "total_amount"}
    reasons = list(match_result.get("decision_reasons") or [])

    status: Optional[str] = None
    hold_code: Optional[str] = None
    reason: Optional[str] = None
    if light == "RED":
        status = "FAIL"
        if "SET_CLAIMED_INCOMPLETE" in flags:
            hold_code = "PARTIAL_SET"
            reason = "已声明齐套但资料仍不完整"
        else:
            hold_code = "PAPER_FIELD"
            reason = "一对多累计存在超额或重复分配"
    elif light == "YELLOW" and ({"AMBIGUOUS_LINK", "UNBOUND"} & flags):
        if only_fulfillment_failures:
            status = "WARNING"
            hold_code = "AMBIGUOUS_BINDING"
            reason = "一对多来源行无法唯一绑定，须人工复核"
    elif light == "YELLOW" and only_fulfillment_failures:
        status = "WARNING"
        hold_code = "PARTIAL_SET"
        partial = []
        if "PARTIAL_FULFILLMENT" in flags:
            partial.append("签收")
        if "PARTIAL_INVOICE" in flags or "PARTIAL_INVOICE_AMT" in flags:
            partial.append("开票")
        reason = f"当前资料部分{'、'.join(partial) or '履约'}"

    if status and hold_code and reason:
        match_result["overall_status"] = status
        match_result["decision"] = "HOLD_REVIEW"
        match_result["hold_reason_code"] = hold_code
        match_result["decision_reasons"] = list(dict.fromkeys([reason, *reasons]))
        match_result["summary"] = f"三单匹配待复核（HOLD_REVIEW）：{reason}"
        result["match_result"] = match_result
        result["decision"] = "HOLD_REVIEW"
        result["hold_reason_code"] = hold_code
        result["decision_reasons"] = match_result["decision_reasons"]
    return result


def cutoff_calendar_mode(goal_ids: list[str], configured: Optional[str]) -> Optional[str]:
    """01030 的本财年年末边界测试默认仅按 period_end 分侧。"""
    return configured or ("period_end_only" if "gospd01030" in goal_ids else None)


def _explicit_group_filter(
    classified: list[dict[str, Any]], business_group_id: Optional[str]
) -> Optional[str]:
    """仅当输入确有组元数据时再过滤；API 传入的当前链通常已完成裁剪。"""
    wanted = str(business_group_id or "").strip()
    if not wanted:
        return None
    for item in classified:
        if str(item.get("business_group_id") or "").strip() == wanted:
            return wanted
        ids = {str(value).strip() for value in (item.get("business_ids") or [])}
        if wanted in ids:
            return wanted
    return None


def _number_value(value: Any) -> float:
    try:
        return float(str(value or "").replace(",", "").replace("¥", ""))
    except (TypeError, ValueError):
        return 0.0


def _normalized_reference(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _document_contains_reference(item: dict[str, Any], reference: str) -> bool:
    fields = dict(item.get("fields") or {})
    blob = " ".join(
        str(value or "")
        for value in (
            fields.get("documentNo"), fields.get("orderNo"), fields.get("contractNo"),
            fields.get("remarks"), item.get("raw_text"), item.get("ocr_text"),
        )
    )
    return bool(reference and reference in _normalized_reference(blob))


def _contract_order_anchor(
    classified: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """无订单时，只允许唯一、可复算且被全部凭证引用的合同充当内部锚点。"""
    active = [item for item in classified if not item.get("excluded_from_match")]
    if any(str(item.get("doc_type") or "").lower() == "order" for item in active):
        return None
    contracts = [
        item for item in active
        if str(item.get("doc_type") or "").lower() == "contract"
    ]
    if len(contracts) != 1:
        return None
    contract = contracts[0]
    fields = dict(contract.get("fields") or {})
    contract_no = str(fields.get("contractNo") or fields.get("documentNo") or "").strip()
    customer = str(
        fields.get("customerName") or fields.get("buyerName")
        or fields.get("supplierName") or ""
    ).strip()
    items = [row for row in (fields.get("items") or []) if isinstance(row, dict)]
    quantity = _number_value(fields.get("quantity")) or sum(
        _number_value(row.get("quantity") or row.get("qty")) for row in items
    )
    amount = _number_value(fields.get("totalAmount") or fields.get("amount")) or sum(
        _number_value(
            row.get("totalAmount")
            if row.get("totalAmount") not in (None, "")
            else _number_value(row.get("amount")) + _number_value(row.get("taxAmount"))
        )
        for row in items
    )
    reference = _normalized_reference(contract_no)
    evidence_docs = [
        item for item in active
        if str(item.get("doc_type") or "").lower()
        in {"receipt", "delivery", "invoice"}
    ]
    has_receipt = any(
        str(item.get("doc_type") or "").lower() in {"receipt", "delivery"}
        for item in evidence_docs
    )
    has_invoice = any(
        str(item.get("doc_type") or "").lower() == "invoice"
        for item in evidence_docs
    )
    if (
        not contract_no or not customer or (quantity <= 0 and amount <= 0)
        or not has_receipt or not has_invoice
        or any(not _document_contains_reference(item, reference) for item in evidence_docs)
    ):
        return None
    anchor_fields = dict(fields)
    anchor_fields["orderNo"] = contract_no
    anchor_fields["documentNo"] = contract_no
    anchor_fields["contractNo"] = contract_no
    if quantity > 0:
        anchor_fields["quantity"] = quantity
    if amount > 0:
        anchor_fields["totalAmount"] = amount
    return {
        **contract,
        "file_name": str(contract.get("file_name") or "合同") + "#合同锚点",
        "doc_type": "order",
        "fields": anchor_fields,
        "_anchor_source": "CONTRACT_AS_ORDER_ANCHOR",
    }


def _document_customer(fields: dict[str, Any]) -> str:
    return str(
        fields.get("customerName") or fields.get("buyerName")
        or fields.get("clientName") or fields.get("supplierName") or ""
    ).strip()


def _document_gross_amount(fields: dict[str, Any]) -> float:
    direct = _number_value(fields.get("totalAmount"))
    if direct > 0:
        return direct
    rows = [row for row in (fields.get("items") or []) if isinstance(row, dict)]
    total = 0.0
    for row in rows:
        gross = _number_value(row.get("totalAmount"))
        if gross <= 0:
            gross = _number_value(row.get("amount") or row.get("netAmount"))
            if gross > 0:
                gross += _number_value(row.get("taxAmount"))
        total += gross
    return round(total, 2)


def _apply_per_invoice_checks(
    result: dict[str, Any],
    classified: list[dict[str, Any]],
    request: Any,
    matcher: Any,
    *,
    period_end: Optional[str],
    calendar_mode: Optional[str],
    fiscal_year_start: Optional[str],
) -> dict[str, Any]:
    """逐张发票校验购方、业务引用和截止性，禁止汇总值掩盖单张异常。"""
    from src.utils.supplier_normalize import suppliers_are_consistent

    invoices = [
        item for item in classified
        if str(item.get("doc_type") or "").lower() == "invoice"
        and not item.get("excluded_from_match")
    ]
    if not invoices:
        result["invoice_checks"] = []
        result["invoice_cutoff_status"] = "SKIPPED"
        return result

    anchor_customer = str(request.order.supplier_name or "").strip()
    anchor_refs = {
        _normalized_reference(value)
        for value in (request.order.order_no, request.order.contract_no)
        if _normalized_reference(value) not in {"", "UNKNOWN", "POMANUAL"}
    }
    receipt_date = str(request.warehouse_receipt.receipt_date or "").strip()
    checks: list[dict[str, Any]] = []
    failed_comparisons: list[dict[str, Any]] = []

    for item in invoices:
        fields = dict(item.get("fields") or {})
        file_name = str(item.get("file_name") or "未命名发票")
        invoice_no = str(
            fields.get("invoiceNo") or fields.get("documentNo") or file_name
        ).strip()
        invoice_customer = _document_customer(fields)
        customer_ok = bool(
            anchor_customer and invoice_customer
            and suppliers_are_consistent(anchor_customer, invoice_customer)
        )
        reference_ok = bool(
            anchor_refs
            and any(_document_contains_reference(item, ref) for ref in anchor_refs)
        )

        if not customer_ok:
            failed_comparisons.append(
                {
                    "field_name": "invoice_customer",
                    "order_value": anchor_customer,
                    "receipt_value": None,
                    "invoice_value": invoice_customer,
                    "is_consistent": False,
                    "diff_description": f"发票 {file_name} 的购方名称缺失或与业务锚点不一致",
                    "auditor_explain": "每张发票必须分别与订单或合格合同的客户名称勾稽。",
                }
            )
        if not reference_ok:
            failed_comparisons.append(
                {
                    "field_name": "invoice_business_reference",
                    "order_value": sorted(anchor_refs),
                    "receipt_value": None,
                    "invoice_value": fields.get("orderNo") or fields.get("contractNo") or fields.get("remarks"),
                    "is_consistent": False,
                    "diff_description": f"发票 {file_name} 未包含与业务锚点完全一致的订单号或合同号",
                    "auditor_explain": "发票必须逐张确认归属于当前抽样业务，不能仅凭汇总金额放行。",
                }
            )

        posting_date = str(fields.get("postingDate") or "").strip()
        cutoff_status = "SKIPPED"
        cutoff_result: Optional[dict[str, Any]] = None
        cutoff_reason: Optional[str] = None
        if not posting_date:
            cutoff_reason = "该发票缺少入账日期"
        elif not receipt_date or receipt_date in {"UNRESOLVED", "1970-01-01", "None", "null"}:
            cutoff_reason = "当前业务缺少控制权转移/签收日期"
        else:
            try:
                invoice_request = request.model_copy(
                    update={
                        "invoice": request.invoice.model_copy(
                            update={
                                "invoice_no": invoice_no,
                                "order_no": str(
                                    fields.get("orderNo") or fields.get("contractNo")
                                    or request.order.order_no
                                ),
                                "supplier_name": invoice_customer,
                                "total_amount": _document_gross_amount(fields),
                                "invoice_date": fields.get("documentDate"),
                                "posting_date": posting_date,
                            }
                        )
                    }
                )
                payload = matcher.build_cutoff_payload(
                    invoice_request, period_end=period_end
                )
                response = matcher._call_cutoff_inprocess(
                    payload,
                    calendar_mode=calendar_mode,
                    fiscal_year_start=fiscal_year_start,
                )
                cutoff_status = str(response.测试状态)
                cutoff_result = response.model_dump()
            except Exception as exc:
                cutoff_reason = f"该发票截止性未执行：{exc}"

        checks.append(
            {
                "file_name": file_name,
                "invoice_no": invoice_no,
                "customer_status": "PASS" if customer_ok else "FAIL",
                "business_reference_status": "PASS" if reference_ok else "FAIL",
                "cutoff_status": cutoff_status,
                "cutoff_result": cutoff_result,
                "cutoff_skipped_reason": cutoff_reason,
            }
        )

    cutoff_rank = {"SKIPPED": 0, "PASS": 1, "WARNING": 2, "FAIL": 3}
    worst = max(checks, key=lambda row: cutoff_rank.get(str(row["cutoff_status"]), 0))
    worst_status = str(worst["cutoff_status"])
    result["invoice_checks"] = checks
    result["invoice_cutoff_status"] = worst_status
    result["cutoff_status"] = worst_status
    result["cutoff_result"] = worst.get("cutoff_result")
    result["cutoff_available"] = any(
        row["cutoff_status"] != "SKIPPED" for row in checks
    )
    result["cutoff_skipped_reason"] = (
        worst.get("cutoff_skipped_reason") if worst_status == "SKIPPED" else None
    )
    if worst.get("cutoff_result"):
        result["cutoff_summary"] = str(
            worst["cutoff_result"].get("问题描述") or "逐张发票截止性已执行"
        )

    if failed_comparisons:
        match_result = dict(result.get("match_result") or {})
        comparisons = list(match_result.get("comparisons") or [])
        comparisons.extend(failed_comparisons)
        reason = "至少一张发票的购方名称或业务引用未通过逐张校验"
        match_result.update(
            {
                "overall_status": "FAIL",
                "decision": "HOLD_REVIEW",
                "hold_reason_code": "PAPER_FIELD",
                "comparisons": comparisons,
                "decision_reasons": list(dict.fromkeys(
                    [reason, *(match_result.get("decision_reasons") or [])]
                )),
                "summary": f"三单匹配待复核（HOLD_REVIEW）：{reason}",
            }
        )
        result["match_result"] = match_result
        result["decision"] = "HOLD_REVIEW"
        result["hold_reason_code"] = "PAPER_FIELD"
        result["decision_reasons"] = match_result["decision_reasons"]
    return result


def run_three_way(
    classified: list[dict[str, Any]],
    manual: Optional[dict[str, Any]] = None,
    selected_receipt_idx: Optional[int] = None,
    *,
    period_end: Optional[str] = None,
    calendar_mode: Optional[str] = None,
    fiscal_year_start: Optional[str] = None,
    complete_set: bool = False,
    business_group_id: Optional[str] = None,
    business_binding_confirmed: bool = False,
) -> dict[str, Any]:
    from src.three_way_match.matcher import ThreeWayMatcher
    from src.three_way_match.one_to_many import run_one_to_many

    manual = manual or {}
    # 入账日来自序时账匹配结果，权威性高于 OCR 猜测。只在单据字段缺失时补入，
    # 且复制输入，避免测试流程反向污染原始识别结果。
    prepared_classified: list[dict[str, Any]] = []
    for source in classified:
        item = dict(source)
        fields = dict(source.get("fields") or {})
        if source.get("doc_type") == "invoice" and not fields.get("postingDate"):
            ledger_posting_date = source.get("ledger_posting_date")
            if ledger_posting_date:
                fields["postingDate"] = ledger_posting_date
        item["fields"] = fields
        prepared_classified.append(item)
    classified = prepared_classified
    contract_anchor = _contract_order_anchor(classified)
    working_classified = [*classified, contract_anchor] if contract_anchor else classified
    merged = merge_same_type_docs(working_classified)
    work_merged = dict(merged)

    missing_roles: list[str] = []
    if "order" not in work_merged and not (
        manual.get("order_amount") is not None or manual.get("supplier")
    ):
        missing_roles.append("订单")
    if (
        "receipt" not in work_merged
        and "delivery" not in work_merged
        and not manual.get("receipt_date")
    ):
        missing_roles.append("签收/发货")
    if "invoice" not in work_merged and not (
        manual.get("invoice_amount") is not None or manual.get("posting_date")
    ):
        missing_roles.append("发票")

    if missing_roles:
        reason = f"缺少必要单据：{'、'.join(missing_roles)}，无法执行三单（资料不足）"
        incomplete = {
            "status": "INCOMPLETE",
            # overall_status 保留给旧调用方；新界面使用独立的 three_way_status/cutoff_status。
            "overall_status": "FAIL",
            "summary": reason,
            "human_readable_summary": reason,
            "risks": [reason],
            "cutoff_available": False,
            "cutoff_skipped_reason": reason,
            "match_result": None,
            "incomplete": True,
            "missing_roles": missing_roles,
            "decision": "HOLD_REVIEW",
            "hold_reason_code": "DOCUMENT_MISSING",
            "decision_reasons": [f"D2:缺必要单据：{'、'.join(missing_roles)}"],
            "erp_review": {
                "status": "UNAVAILABLE",
                "note": "未接公司 ERP；缺单据时纸面三单亦不可自动通过。",
            },
        }
        from src.three_way_match.audit_trace import build_three_way_audit_view

        incomplete.update(build_three_way_audit_view(classified, incomplete))
        return incomplete

    # 仅在人工明确提供时补齐角色壳；禁止用订单金额静默填签收/发票
    if "order" not in work_merged and (
        manual.get("order_amount") is not None or manual.get("supplier")
    ):
        work_merged["order"] = {
            "file_name": "(手工)",
            "doc_type": "order",
            "fields": {
                "documentNo": "PO-MANUAL",
                "supplierName": manual.get("supplier") or "",
                "customerName": manual.get("supplier") or "",
                "totalAmount": manual.get("order_amount") or 0,
                "quantity": 1,
                "paymentTerms": manual.get("payment_terms"),
            },
            "score": 0,
        }
    if "receipt" not in work_merged and "delivery" not in work_merged and manual.get(
        "receipt_date"
    ):
        work_merged["receipt"] = {
            "file_name": "(手工)",
            "doc_type": "receipt",
            "fields": {
                "documentNo": "RC-MANUAL",
                "documentDate": manual.get("receipt_date"),
                "deliveryDate": manual.get("receipt_date"),
                "totalAmount": manual.get("receipt_amount"),
            },
            "score": 0,
        }
    if "invoice" not in work_merged and (
        manual.get("invoice_amount") is not None or manual.get("posting_date")
    ):
        work_merged["invoice"] = {
            "file_name": "(手工)",
            "doc_type": "invoice",
            "fields": {
                "documentNo": "INV-MANUAL",
                "postingDate": manual.get("posting_date"),
                "totalAmount": manual.get("invoice_amount"),
            },
            "score": 0,
        }

    if selected_receipt_idx is None:
        selected_receipt_idx = find_latest_receipt_index(working_classified)

    request = assemble_three_way_request(
        work_merged,
        selected_receipt_idx=selected_receipt_idx,
        classified=working_classified,
        manual=manual,
    )

    fulfillment = run_one_to_many(
        working_classified,
        complete_set=complete_set,
        business_group_id=_explicit_group_filter(working_classified, business_group_id),
    )
    quantity_roles = dict(fulfillment.get("quantity_roles") or {})
    amount_roles = dict(fulfillment.get("amount_roles") or {})
    has_fulfillment_rows = bool(fulfillment.get("rows"))
    order_updates: dict[str, Any] = {}
    receipt_updates: dict[str, Any] = {}
    invoice_updates: dict[str, Any] = {}
    if has_fulfillment_rows and quantity_roles:
        order_updates["quantity"] = float(quantity_roles.get("ordered_qty") or 0)
        receipt_updates["quantity"] = float(quantity_roles.get("received_qty") or 0)
        invoice_updates["quantity"] = float(quantity_roles.get("invoiced_qty") or 0)
    if has_fulfillment_rows and float(amount_roles.get("ordered_amount") or 0) > 0:
        order_updates["total_amount"] = float(amount_roles["ordered_amount"])
    if has_fulfillment_rows and float(amount_roles.get("received_amount") or 0) > 0:
        receipt_updates["total_amount"] = float(amount_roles["received_amount"])
    if has_fulfillment_rows and float(amount_roles.get("invoiced_amount") or 0) > 0:
        invoice_updates["total_amount"] = float(amount_roles["invoiced_amount"])
    if order_updates or receipt_updates or invoice_updates:
        request = request.model_copy(
            update={
                "order": request.order.model_copy(update=order_updates),
                "warehouse_receipt": request.warehouse_receipt.model_copy(
                    update=receipt_updates
                ),
                "invoice": request.invoice.model_copy(update=invoice_updates),
            }
        )

    # 贸易模式：外销截止日用装船/交承运人日，禁止仓库签收冒充 FOB/CIF 控制权日
    tm_payload: dict[str, Any] = {}
    try:
        from src.workflow.trading_mode_bridge import (
            control_date_for_cutoff,
            interpret_chain_trading_mode,
            prefers_on_board_cutoff,
        )

        tm_payload = interpret_chain_trading_mode(
            classified,
            transaction_id="three-way-cutoff",
            use_llm=None,
            persist=False,
        )
        ctrl_date, date_meaning = control_date_for_cutoff(tm_payload)
        if prefers_on_board_cutoff(tm_payload) and ctrl_date:
            wr = request.warehouse_receipt.model_copy(update={"receipt_date": ctrl_date})
            request = request.model_copy(update={"warehouse_receipt": wr})
            tm_payload = dict(tm_payload)
            tm_payload["cutoff_date_override"] = ctrl_date
            tm_payload["cutoff_date_meaning"] = date_meaning
    except Exception:
        tm_payload = {}

    matcher = ThreeWayMatcher()
    raw = matcher.match_and_cutoff(
        request,
        inprocess=True,
        period_end=period_end,
        calendar_mode=calendar_mode,
        fiscal_year_start=fiscal_year_start,
    )
    result = serialize_three_way_result(raw)
    result["fulfillment"] = fulfillment
    result = _apply_fulfillment_decision(result, fulfillment)
    if contract_anchor:
        result["anchor_source"] = "CONTRACT_AS_ORDER_ANCHOR"
        result["fulfillment"]["anchor_source"] = "CONTRACT_AS_ORDER_ANCHOR"
    result = _apply_per_invoice_checks(
        result,
        classified,
        request,
        matcher,
        period_end=period_end,
        calendar_mode=calendar_mode,
        fiscal_year_start=fiscal_year_start,
    )
    from src.three_way_match.audit_trace import build_three_way_audit_view

    # 三单的业务分组、字段勾稽与截止性是三个不同的审计判断层，
    # 为兼容旧调用仍保留 overall_status，但新消费者不得再拿它当三单结论。
    result.update(
        build_three_way_audit_view(
            classified,
            result,
            business_binding_confirmed=business_binding_confirmed,
        )
    )
    if tm_payload:
        result["trading_mode"] = tm_payload.get("workbook_view") or {}
        result["trading_mode_gospd"] = tm_payload.get("gospd_cells") or {}
        if tm_payload.get("cutoff_date_override"):
            result["cutoff_control_date"] = tm_payload.get("cutoff_date_override")
            result["cutoff_control_date_meaning"] = tm_payload.get("cutoff_date_meaning")
    return result


def selected_workbook_formats(job: dict[str, Any]) -> list[str]:
    """按用户勾选目标顺序收集官方 workbook_format，不人为偏重某一份。"""
    from src.workflow.recipes import WORKPAPER_RECIPES

    goal_ids = list((job.get("plan") or {}).get("goal_ids") or job.get("goal_ids") or [])
    out: list[str] = []
    seen: set[str] = set()
    for gid in goal_ids:
        fmt = str((WORKPAPER_RECIPES.get(gid) or {}).get("workbook_format") or "").strip()
        if fmt and fmt not in seen:
            seen.add(fmt)
            out.append(fmt)
    return out


def _fill_official_workbook(
    fmt: str, job: dict[str, Any], out_dir: Path, short: str, stamp: str
) -> Path:
    if fmt == "gospd01010":
        from src.reporting.gospd01010_filler import fill_gospd01010_workbook

        out = out_dir / f"GOSPD01010_{short}_{stamp}.xlsx"
        return fill_gospd01010_workbook(job, out)
    if fmt == "gospd01010_2":
        from src.reporting.gospd01010_2_filler import fill_gospd01010_2_workbook

        out = out_dir / f"GOSPD01010.2_{short}_{stamp}.xlsx"
        return fill_gospd01010_2_workbook(job, out)
    if fmt == "gospd01010_3":
        from src.reporting.gospd01010_3_filler import fill_gospd01010_3_workbook

        out = out_dir / f"GOSPD01010.3_{short}_{stamp}.xlsx"
        return fill_gospd01010_3_workbook(job, out)
    if fmt == "gospd01010_4":
        from src.reporting.gospd01010_4_filler import fill_gospd01010_4_workbook

        out = out_dir / f"GOSPD01010.4_{short}_{stamp}.xlsx"
        return fill_gospd01010_4_workbook(job, out)
    if fmt == "gospd01030":
        from src.reporting.gospd01030_filler import fill_gospd01030_workbook

        out = out_dir / f"GOSPD01030_{short}_{stamp}.xlsx"
        return fill_gospd01030_workbook(job, out)
    raise ValueError(f"未知官方底稿格式: {fmt}")


def build_workbooks_for_job(job: dict[str, Any]) -> list[Path]:
    """按所选目标各生成一份底稿（多选官方模板时等价于分别单测再各导出一次）。"""
    from src.audit.coverage_map import build_coverage_map
    from src.reporting.audit_workbook_xlsx import (
        build_audit_workbook_payload,
        generate_audit_workbook_xlsx,
    )
    from src.workflow.recipes import (
        STEP_AMOUNT,
        STEP_CONTRACT,
        STEP_EVIDENCE,
        STEP_THREE_WAY,
    )

    job_id = str(job.get("job_id") or "job")
    out_dir = workbook_output_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short = job_id[:8]
    required = set((job.get("plan") or {}).get("required_steps") or [])

    formats = selected_workbook_formats(job)
    if formats:
        return [
            _fill_official_workbook(fmt, job, out_dir, short, stamp) for fmt in formats
        ]

    evidence = (
        job.get("evidence")
        if STEP_EVIDENCE in required and isinstance(job.get("evidence"), dict)
        else None
    )
    amount = (
        job.get("amount_test")
        if STEP_AMOUNT in required and isinstance(job.get("amount_test"), dict)
        else None
    )
    contract = (
        job.get("contract_terms")
        if STEP_CONTRACT in required and isinstance(job.get("contract_terms"), dict)
        else None
    )
    three_way = (
        job.get("three_way")
        if STEP_THREE_WAY in required and isinstance(job.get("three_way"), dict)
        else None
    )
    relations = (job.get("relations") or []) if STEP_EVIDENCE in required else []
    duplicates = (job.get("duplicates") or {}) if STEP_EVIDENCE in required else {}

    out = out_dir / f"审阅底稿_{short}_{stamp}.xlsx"
    coverage = build_coverage_map(
        classified=job.get("classified"),
        evidence=evidence,
        amount=amount,
        contract=contract,
        three_way=three_way,
        fields_confirmed=bool(job.get("fields_confirmed")),
        matching_confirmed=bool(job.get("matching_confirmed")),
        conclusion_confirmed=bool(job.get("conclusion_confirmed")),
        relations=relations,
        duplicates=duplicates,
    )
    req_dims = set((job.get("plan") or {}).get("required_dimensions") or [])
    if req_dims:
        coverage["dimensions"] = [
            d
            for d in (coverage.get("dimensions") or [])
            if d.get("dimension_id") in req_dims
        ]
    payload = build_audit_workbook_payload(
        evidence=evidence,
        amount=amount,
        contract=contract,
        three_way=three_way,
        coverage=coverage,
        relations=relations,
        duplicates=duplicates,
        advisory_candidates=job.get("advisory_candidates") or [],
        matching_confirmed=bool(job.get("matching_confirmed")),
        conclusion_confirmed=bool(job.get("conclusion_confirmed")),
    )
    return [generate_audit_workbook_xlsx(payload, out)]


def build_workbook_for_job(job: dict[str, Any]) -> Path:
    """兼容旧调用：返回本次生成的第一份（与勾选顺序一致，无格式偏重）。"""
    paths = build_workbooks_for_job(job)
    if not paths:
        raise RuntimeError("未生成任何底稿文件")
    return paths[0]


def ocr_status() -> dict[str, Any]:
    from src.legacy_ocr import LegacyOcrAdapter

    adapter = LegacyOcrAdapter()
    configured = adapter.is_api_configured()
    if configured:
        message = "千帆 OCR 已配置：将调用真实 PaddleOCR（非 Mock）"
    else:
        message = "千帆 OCR 未配置或仍为占位符，将降级 Mock"
    return {"configured": configured, "message": message}


def highlight_preview(
    path: str | Path,
    fields: dict[str, Any],
    selected_key: Optional[str] = None,
    text_blocks: Optional[list[dict[str, Any]]] = None,
) -> tuple[bytes | None, str]:
    """生成高亮 PNG；无命中/无字段时仍尽量返回原件首页，避免前端空白。"""
    from src.ui.field_highlight import (
        build_boxes_for_fields,
        collect_highlight_fields,
        render_pdf_highlighted,
        render_image_highlighted,
    )

    p = Path(path)
    if not p.is_file():
        return None, "文件不存在或路径无效"
    items = collect_highlight_fields(fields or {})
    key = (selected_key or "").strip() or None
    img, note = build_boxes_for_fields(
        path=p,
        items=items,
        selected_key=key,
        text_blocks=text_blocks or [],
    )
    # 无字段条目时 build_boxes 会返回 None——仍渲染原件
    if img is None:
        suffix = p.suffix.lower()
        if suffix == ".pdf":
            img = render_pdf_highlighted(p, {}, page_index=0)
            note = note or "未定位到字段，已显示原件首页"
        elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
            img = render_image_highlighted(p, [])
            note = note or "未定位到字段，已显示原图"
        else:
            return None, note or f"格式 {suffix} 暂不支持预览"
    if img is None:
        return None, note or "预览渲染失败"
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), note or ""


def collect_ocr_issues(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in classified:
        err = item.get("error")
        source = str(item.get("ocr_source") or "")
        if err or source in {"ocr_failed", "failed", "mock"}:
            issues.append(
                {
                    "file_name": item.get("file_name"),
                    "ocr_source": source,
                    "error": err or f"OCR 来源：{source}",
                }
            )
    return issues
