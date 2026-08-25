"""凭证包拆包分笔编排：判定 → 切分 → 聚类 → 人工确认后物化 classified。"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.legacy_ocr.ledger_parser import extract_biz_ids_from_filename
from src.workflow.packet_cards import UNRESOLVED, map_to_host_type
from src.workflow.packet_cluster import UNIDENTIFIED_CHAIN, cluster_units
from src.workflow.packet_relations import (
    normalize_business_ids,
    validate_confirmable_units,
    with_business_ids,
)
from src.workflow.packet_split import (
    UnitDraft,
    load_file_pages,
    pdf_page_count,
    split_pages_into_units,
)
from src.workflow.pipeline import job_workdir

STANDARD = "standard"
PACKET_SINGLE = "packet_single_chain"
PACKET_MULTI = "packet_multi_chain"
_TYPE_TOKENS = (
    "发票",
    "合同",
    "订单",
    "发货",
    "签收",
    "验收",
    "回款",
    "invoice",
    "contract",
    "order",
    "delivery",
    "receipt",
    "payment",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def detect_packet_kind(
    file_name: str,
    path: str,
    *,
    mixed_packet_declared: bool = False,
    light_confident: bool = False,
    doc_type: str = "",
    page_count: int | None = None,
    page_biz_ids: list[str] | None = None,
) -> str:
    """只对审计师明确声明的混装 PDF 开启拆包，不做自动推断。"""
    suffix = Path(file_name).suffix.lower()
    ids = extract_biz_ids_from_filename(file_name)
    n = page_count if page_count is not None else pdf_page_count(path)
    if not mixed_packet_declared or suffix != ".pdf" or n <= 1:
        return STANDARD
    distinct_so = []
    for bid in page_biz_ids or []:
        u = str(bid).upper()
        if u.startswith(("SO", "PO")) and bid not in distinct_so:
            distinct_so.append(bid)
    if len(distinct_so) >= 2:
        return PACKET_MULTI
    name_has_type = any(tok.lower() in (file_name or "").lower() for tok in _TYPE_TOKENS)
    typed = (doc_type or "other") not in {"", "other", UNRESOLVED}
    if light_confident and typed and name_has_type and n <= 4 and ids:
        return STANDARD
    return PACKET_SINGLE


def empty_packet_run() -> dict[str, Any]:
    return {
        "run_id": "",
        "status": "idle",
        "created_at": None,
        "confirmed_at": None,
        "files": [],
        "warnings": [],
        "pages": [],
    }


def packet_status(job: dict[str, Any] | None) -> str:
    run = (job or {}).get("packet_run") or {}
    return str(run.get("status") or "idle")


def pending_raw_packets(job: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (job or {}).get("pending_files") or []:
        if item.get("from_packet"):
            continue
        if item.get("mixed_packet_declared") is not True:
            continue
        kind = str(item.get("packet_kind") or "")
        if kind in {PACKET_SINGLE, PACKET_MULTI}:
            out.append(item)
    return out


def packet_blocks_process(job: dict[str, Any] | None) -> bool:
    """未确认的凭证包不得直接 OCR / 进字段确认。"""
    if pending_raw_packets(job):
        st = packet_status(job)
        return st not in {"confirmed"}
    return False


def packet_needs_review(job: dict[str, Any] | None) -> bool:
    st = packet_status(job)
    if st in {"needs_review", "pending_analyze", "analyzing"}:
        return True
    return bool(pending_raw_packets(job))


def annotate_pending_kinds(pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """上传后轻量打标（页数+文件名），供 UI 显示需拆包。"""
    out = []
    for item in pending or []:
        row = dict(item)
        path = str(row.get("path") or "")
        name = str(row.get("file_name") or "")
        if row.get("from_packet"):
            row["packet_kind"] = STANDARD
            out.append(row)
            continue
        n = pdf_page_count(path) if path else 1
        row["page_count"] = n
        row["packet_kind"] = detect_packet_kind(
            name,
            path,
            mixed_packet_declared=row.get("mixed_packet_declared") is True,
            light_confident=bool(row.get("light_confident")),
            doc_type=str(row.get("doc_type") or ""),
            page_count=n,
        )
        out.append(row)
    return out


def _unit_to_dict(draft: UnitDraft) -> dict[str, Any]:
    pages = list(draft.pages)
    return {
        "unit_id": draft.unit_id,
        "source_file": draft.source_file,
        "source_path": draft.source_path,
        "page_start": pages[0] if pages else 1,
        "page_end": pages[-1] if pages else 1,
        "pages": pages,
        "card_type": draft.card_type,
        "suggested_doc_type": draft.host_type if draft.host_type != UNRESOLVED else UNRESOLVED,
        "doc_type": draft.host_type if draft.host_type != UNRESOLVED else UNRESOLVED,
        "doc_type_source": "ai",
        "host_type": draft.host_type,
        "split_reason": draft.split_reason,
        "uncertain_pages": list(draft.uncertain_pages),
        "boundary": draft.boundary,
        "type_candidates": list(draft.type_candidates),
        "keys": dict(draft.keys),
        "excerpt": draft.excerpt,
        "needs_review": bool(draft.needs_review),
        "review_reasons": list(draft.review_reasons),
        "boundary_confirmed": False,
        "business_ids": [],
        "business_binding_source": None,
        "chain_id": UNIDENTIFIED_CHAIN,
        "text": draft.text,
    }


def analyze_pending_packets(
    job: dict[str, Any],
    *,
    use_vlm: bool = True,
    ocr_low_quality: bool = True,
    file_modes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """对 pending 中的凭证包切分+聚类；标准件跳过。"""
    job_id = str(job.get("job_id") or "")
    workdir = job_workdir(job_id) if job_id else Path(".")
    pending = annotate_pending_kinds(list(job.get("pending_files") or []))
    run_id = uuid.uuid4().hex[:12]
    files_meta: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    page_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    has_packet = False

    for item in pending:
        name = str(item.get("file_name") or "")
        path = str(item.get("path") or "")
        kind = str(item.get("packet_kind") or STANDARD)
        n = int(item.get("page_count") or 0) or pdf_page_count(path)
        meta = {
            "file_name": name,
            "path": path,
            "kind": kind,
            "page_count": n,
            "sha256": file_sha256(path),
            "declared_mode": (file_modes or {}).get(name),
            "declared_business_ids": list(item.get("declared_business_ids") or []),
        }
        files_meta.append(meta)
        if kind == STANDARD:
            continue
        has_packet = True
        pages = load_file_pages(
            name,
            path,
            workdir=workdir,
            ocr_low_quality=ocr_low_quality,
        )
        page_biz: list[str] = []
        for rec in pages:
            page_biz.extend(rec.biz_ids)
            page_rows.append(
                {
                    "source_file": rec.source_file,
                    "page": rec.page,
                    "quality": rec.quality,
                    "page_role": rec.page_role,
                    "extractor": rec.extractor,
                    "needs_review": rec.needs_review,
                    "invoice_no": rec.invoice_no,
                    "biz_ids": rec.biz_ids,
                    "card_type": rec.card_type,
                    "text_preview": (rec.text or "")[:240],
                }
            )
        # 页级多 SO → 升为多笔
        distinct_so = []
        for bid in page_biz:
            u = str(bid).upper()
            if u.startswith(("SO", "PO")) and bid not in distinct_so:
                distinct_so.append(bid)
        if len(distinct_so) >= 2:
            kind = PACKET_MULTI
            meta["kind"] = kind
            item["packet_kind"] = kind
        drafts = split_pages_into_units(pages, use_vlm=use_vlm)
        from src.workflow.packet_split import page_coverage_ok

        ok, cov_warn = page_coverage_ok(pages, drafts)
        if not ok:
            warnings.extend(cov_warn)
        for d in drafts:
            units.append(_unit_to_dict(d))

    filename_ids = {m["file_name"]: extract_biz_ids_from_filename(m["file_name"]) for m in files_meta}
    file_kinds = {m["file_name"]: m["kind"] for m in files_meta}
    clustered, cluster_warnings = cluster_units(
        units,
        file_kinds=file_kinds,
        file_modes=file_modes,
        filename_ids=filename_ids,
    )
    warnings.extend(cluster_warnings)
    declared_by_file = {
        str(item.get("file_name") or ""): list(item.get("declared_business_ids") or [])
        for item in pending
    }
    for item in clustered:
        declared_ids = declared_by_file.get(str(item.get("source_file") or "")) or []
        linked = with_business_ids(item, declared_ids or [item.get("chain_id")])
        item.update(linked)
        item["business_binding_source"] = None
        item.pop("text", None)  # 不把全文塞进 job

    status = "skipped" if not has_packet else "needs_review"
    run = {
        "run_id": run_id,
        "status": status,
        "created_at": _utc_now(),
        "confirmed_at": None,
        "files": files_meta,
        "warnings": warnings,
        "pages": page_rows,
    }
    return {
        "packet_run": run,
        "packet_units": clustered,
        "pending_files": pending,
        "packet_confirmed": status == "skipped",
    }


def _safe_token(value: str, fallback: str = "x") -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", str(value or "").strip())
    text = text.strip("._") or fallback
    return text[:40]


def extract_pdf_page_range(src: Path, pages_1based: list[int], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    reader_pages = [p - 1 for p in pages_1based if p >= 1]
    if not reader_pages:
        raise ValueError("页范围为空")
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(src))
    writer = PdfWriter()
    n = len(reader.pages)
    for idx in reader_pages:
        if idx < 0 or idx >= n:
            raise ValueError(f"页码越界: {idx + 1}/{n}")
        writer.add_page(reader.pages[idx])
    with dest.open("wb") as fh:
        writer.write(fh)


def materialize_units(
    job_id: str,
    units: list[dict[str, Any]],
    *,
    run_id: str,
) -> list[dict[str, Any]]:
    """按单元裁出虚拟 PDF，返回可送入 process_uploaded_files 的 specs。"""
    folder = job_workdir(job_id) / "packet_units"
    folder.mkdir(parents=True, exist_ok=True)
    specs: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for unit in units:
        if unit.get("dropped"):
            continue
        pages = [int(p) for p in (unit.get("pages") or [])]
        if not pages:
            start = int(unit.get("page_start") or 1)
            end = int(unit.get("page_end") or start)
            pages = list(range(start, end + 1))
        src = Path(str(unit.get("source_path") or ""))
        if not src.is_file():
            raise ValueError(f"源文件不存在: {unit.get('source_file')}")
        linked_unit = with_business_ids(unit, normalize_business_ids(unit))
        business_ids = list(linked_unit["business_ids"])
        chain = _safe_token(str(linked_unit.get("chain_id") or UNIDENTIFIED_CHAIN), "unresolved")
        host = str(unit.get("doc_type") or unit.get("host_type") or UNRESOLVED)
        if host == UNRESOLVED:
            type_token = "unresolved"
            forced = "other"
        else:
            type_token = _safe_token(host, "other")
            forced = host
        start, end = pages[0], pages[-1]
        fname = f"{chain}_{type_token}_p{start}-{end}.pdf"
        if fname in used_names:
            fname = f"{chain}_{type_token}_p{start}-{end}_{_safe_token(str(unit.get('unit_id') or '')[:8])}.pdf"
        used_names.add(fname)
        dest = folder / fname
        extract_pdf_page_range(src, pages, dest)
        source_packet = {
            "source_file": unit.get("source_file"),
            "source_hash": file_sha256(str(src)),
            "page_start": start,
            "page_end": end,
            "pages": pages,
            "run_id": run_id,
            "unit_id": unit.get("unit_id"),
            "card_type": unit.get("card_type"),
            "business_ids": business_ids,
            "chain_id": linked_unit.get("chain_id"),
            "suggested_doc_type": unit.get("suggested_doc_type"),
            "doc_type_source": unit.get("doc_type_source"),
            "boundary_confirmed": bool(unit.get("boundary_confirmed")),
            "business_binding_source": unit.get("business_binding_source"),
            "confirmed_at": unit.get("confirmed_at"),
            "confirmed_by": unit.get("confirmed_by"),
            "keys": dict(unit.get("keys") or {}),
        }
        specs.append(
            {
                "filename": fname,
                "content": dest.read_bytes(),
                "slot_hint": "",
                "doc_type": forced,
                "source_packet": source_packet,
                "from_packet": True,
                "path": str(dest),
                "keys": dict(unit.get("keys") or {}),
            }
        )
    return specs


def apply_unit_edits(
    existing: list[dict[str, Any]],
    edits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """人工合并/拆页/改类型/改归属后的单元列表。"""
    by_id = {str(u.get("unit_id") or ""): dict(u) for u in existing}
    path_by_file = {
        str(u.get("source_file") or ""): str(u.get("source_path") or "")
        for u in existing
        if u.get("source_file")
    }
    if not edits:
        return list(by_id.values())
    out: list[dict[str, Any]] = []
    for edit in edits:
        uid = str(edit.get("unit_id") or "")
        base = dict(by_id.get(uid) or {})
        pages = [int(p) for p in (edit.get("pages") or base.get("pages") or [])]
        pages = sorted({p for p in pages if p >= 1})
        if not pages:
            raise ValueError(f"单元 {uid or '?'} 页范围为空白")
        source_file = str(edit.get("source_file") or base.get("source_file") or "")
        source_path = str(
            edit.get("source_path")
            or base.get("source_path")
            or path_by_file.get(source_file)
            or ""
        )
        doc_type = str(edit.get("doc_type") or base.get("doc_type") or UNRESOLVED)
        card_type = str(
            edit["card_type"] if edit.get("card_type") not in (None, "") else base.get("card_type") or ""
        )
        dropped = bool(edit.get("dropped"))
        if doc_type in {"contract", "order", "delivery", "receipt", "invoice", "payment"}:
            host = doc_type
        elif doc_type == "other":
            host = UNRESOLVED
        else:
            host = map_to_host_type(doc_type) if doc_type != UNRESOLVED else UNRESOLVED
            if host != UNRESOLVED:
                doc_type = host
        legacy_chain = str(
            edit.get("chain_id") or base.get("chain_id") or UNIDENTIFIED_CHAIN
        ).strip()
        if edit.get("business_ids") is not None:
            business_ids = normalize_business_ids({"business_ids": edit.get("business_ids")})
        elif base.get("business_ids") is not None:
            business_ids = normalize_business_ids(base)
        else:
            business_ids = normalize_business_ids({"chain_id": legacy_chain})
        if edit.get("boundary_confirmed") is not None:
            boundary_confirmed = bool(edit.get("boundary_confirmed"))
        elif base.get("boundary_confirmed") is not None:
            boundary_confirmed = bool(base.get("boundary_confirmed"))
        else:
            # Legacy clients confirm by submitting the unit; preserve that contract.
            boundary_confirmed = True
        unit_id = uid or "du_" + hashlib.sha256(
            f"{source_file}:{pages}".encode("utf-8")
        ).hexdigest()[:16]
        merged = with_business_ids({
            **base,
            "unit_id": unit_id,
            "source_file": source_file,
            "source_path": source_path,
            "pages": pages,
            "page_start": pages[0],
            "page_end": pages[-1],
            "doc_type": host if host != UNRESOLVED else UNRESOLVED,
            "host_type": host,
            "card_type": card_type or base.get("card_type"),
            "suggested_doc_type": str(
                edit.get("suggested_doc_type")
                or base.get("suggested_doc_type")
                or base.get("doc_type")
                or UNRESOLVED
            ),
            "doc_type_source": str(
                edit.get("doc_type_source") or base.get("doc_type_source") or "ai"
            ),
            "boundary_confirmed": boundary_confirmed,
            "business_binding_source": (
                edit.get("business_binding_source")
                if edit.get("business_binding_source") is not None
                else base.get("business_binding_source")
            ),
            "drop_reason": str(edit.get("drop_reason") or base.get("drop_reason") or ""),
            "keys": dict(edit.get("keys") or base.get("keys") or {}),
            "needs_review": not boundary_confirmed,
            "dropped": dropped,
        }, business_ids)
        out.append(merged)
    return out


def confirm_packet(
    job: dict[str, Any],
    *,
    units: list[dict[str, Any]],
    file_modes: dict[str, str] | None = None,
    start_ocr: bool = False,
) -> dict[str, Any]:
    """确认拆包：物化虚拟文件，替换 pending 中的原包。"""
    job_id = str(job.get("job_id") or "")
    run = dict(job.get("packet_run") or empty_packet_run())
    existing_units = list(job.get("packet_units") or [])
    final_units = apply_unit_edits(existing_units, units) if units else existing_units
    if file_modes:
        manual_links = {
            str(unit.get("unit_id") or ""): normalize_business_ids(unit)
            for unit in final_units
            if unit.get("business_binding_source") == "human"
        }
        final_units, extra = cluster_units(
            final_units,
            file_kinds={
                str(f.get("file_name") or ""): str(f.get("kind") or PACKET_SINGLE)
                for f in (run.get("files") or [])
            },
            file_modes=file_modes,
            filename_ids={
                str(f.get("file_name") or ""): extract_biz_ids_from_filename(
                    str(f.get("file_name") or "")
                )
                for f in (run.get("files") or [])
            },
        )
        final_units = [
            with_business_ids(
                unit,
                manual_links.get(str(unit.get("unit_id") or ""), normalize_business_ids(unit)),
            )
            for unit in final_units
        ]
        run["warnings"] = list(run.get("warnings") or []) + extra

    _assert_pages_covered(job, final_units)
    multi_page_files = {
        str(meta.get("file_name") or "")
        for meta in (run.get("files") or [])
        if int(meta.get("page_count") or 0) > 1
        and str(meta.get("kind") or STANDARD) != STANDARD
    }
    validate_confirmable_units(
        final_units,
        multi_page_files=multi_page_files,
        start_ocr=start_ocr,
    )
    run["dropped_pages"] = [
        {
            "source_file": u.get("source_file"),
            "pages": list(u.get("pages") or []),
            "unit_id": u.get("unit_id"),
        }
        for u in final_units
        if u.get("dropped")
    ]

    run_id = str(run.get("run_id") or uuid.uuid4().hex[:12])
    specs = materialize_units(job_id, final_units, run_id=run_id)
    pending_keep: list[dict[str, Any]] = []
    packet_names = {
        str(f.get("file_name") or "")
        for f in (run.get("files") or [])
        if str(f.get("kind") or STANDARD) != STANDARD
    }
    for item in job.get("pending_files") or []:
        name = str(item.get("file_name") or "")
        if item.get("from_packet"):
            continue
        if name in packet_names:
            continue
        if str(item.get("packet_kind") or STANDARD) in {PACKET_SINGLE, PACKET_MULTI}:
            continue
        pending_keep.append(item)
    for spec in specs:
        pending_keep.append(
            {
                "file_name": spec["filename"],
                "path": spec["path"],
                "slot_hint": "",
                "size": len(spec["content"]),
                "doc_type": spec.get("doc_type") or "other",
                "doc_type_source": "packet",
                "light_confident": spec.get("doc_type") not in {"", "other", UNRESOLVED},
                "from_packet": True,
                "source_packet": spec.get("source_packet"),
                "packet_kind": STANDARD,
                "packet_keys": spec.get("keys"),
            }
        )
    run["status"] = "confirmed"
    run["confirmed_at"] = _utc_now()
    run["run_id"] = run_id
    return {
        "packet_run": run,
        "packet_units": final_units,
        "pending_files": pending_keep,
        "packet_confirmed": True,
        "materialized_specs": specs,
    }


def _assert_pages_covered(job: dict[str, Any], units: list[dict[str, Any]]) -> None:
    """禁止静默丢页：每一页须落在某个单元，或由人工标记 dropped。"""
    run = job.get("packet_run") or {}
    by_file: dict[str, set[int]] = {}
    for unit in units:
        name = str(unit.get("source_file") or "")
        pages = {int(p) for p in (unit.get("pages") or [])}
        by_file.setdefault(name, set()).update(pages)
        # 同一文件内页不得重叠
    overlapped: list[str] = []
    seen_pages: dict[str, set[int]] = {}
    for unit in units:
        name = str(unit.get("source_file") or "")
        pages = {int(p) for p in (unit.get("pages") or [])}
        prev = seen_pages.setdefault(name, set())
        if prev & pages:
            overlapped.append(name)
        prev.update(pages)
    if overlapped:
        raise ValueError(f"同一文件页范围重叠: {', '.join(sorted(set(overlapped)))}")

    missing: list[str] = []
    for meta in run.get("files") or []:
        if str(meta.get("kind") or STANDARD) == STANDARD:
            continue
        name = str(meta.get("file_name") or "")
        n = int(meta.get("page_count") or 0)
        if n <= 0:
            continue
        have = by_file.get(name) or set()
        need = set(range(1, n + 1))
        lost = sorted(need - have)
        if lost:
            missing.append(f"{name} 缺页 {lost}")
    if missing:
        raise ValueError("拆包后仍有未归属页（禁止丢页）：" + "；".join(missing))
