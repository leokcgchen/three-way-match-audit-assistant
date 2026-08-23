"""工作台流程 API：底稿目标、任务状态、字段确认（逻辑与 Streamlit 同源）。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware  # noqa: F401 — used from main
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from src.audit.coverage_map import build_coverage_map
from src.audit.hitl_log import append_hitl_event
from src.evidence_match.disambiguation import apply_disambiguation_proposal
from src.llm.prompt_catalog import catalog_summary
from src.models.relation_candidates import decide_relation
from src.workflow.job_store import JOB_STORE
from src.workflow.pipeline import (
    apply_ledger_to_classified_list,
    build_workbooks_for_job,
    collect_ocr_issues,
    highlight_preview,
    job_workdir,
    ocr_status,
    process_uploaded_files,
    run_amount,
    run_contract,
    run_evidence,
    seed_phase2,
    run_three_way,
)

# /chains 在有未关闭金额歧义时的整包扫描限频（秒）
_CHAINS_AMB_SCAN_AT: dict[str, float] = {}
from src.workflow.recipes import list_workpaper_goals, resolve_workflow_plan
from src.workflow.three_way_persist import three_way_sample_patch
from src.workflow.chain_workspace import (
    docs_for_chain,
    get_sample,
    is_gospd_mode,
    list_business_chains,
    resolve_active_chain_id,
    sample_test_complete,
)


def _gospd_docs_and_chain(job: dict[str, Any]) -> tuple[list[dict[str, Any]], Optional[str]]:
    """GOSPD 模式：只取当前业务链单据；其它目标用全量。"""
    classified = list(job.get("classified") or [])
    if not is_gospd_mode(job):
        return classified, None
    cid = resolve_active_chain_id(job)
    if not cid:
        raise ValueError("请先上传单据并识别出业务链，再选择当前笔")
    docs = docs_for_chain(classified, cid)
    if not docs:
        raise ValueError(f"当前业务链无单据: {cid}")
    return docs, cid


router = APIRouter(prefix="/api/v1/workflow", tags=["workflow"])

ROOT = Path(__file__).resolve().parents[2]


class CreateJobBody(BaseModel):
    title: str = ""


class SetGoalsBody(BaseModel):
    goal_ids: list[str] = Field(default_factory=list)
    period_end: Optional[str] = None  # YYYY-MM-DD，GOSPD01030 等截止底稿必用
    entity_name: Optional[str] = None
    calendar_mode: Optional[str] = None  # natural_month | fiscal_445 | period_end_only
    fiscal_year_start: Optional[str] = None  # 4-4-5 财年起点 YYYY-MM-DD


class SamplePopulationBody(BaseModel):
    business_ids: list[str] = Field(default_factory=list)
    source: str = "external_import"
    note: str = ""


class PatchFieldsBody(BaseModel):
    file_name: str
    fields: dict[str, Any]
    doc_type: Optional[str] = None


class ActiveStepBody(BaseModel):
    step_id: str


class PacketAnalyzeBody(BaseModel):
    file_modes: dict[str, str] = Field(default_factory=dict)
    use_vlm: bool = True


class PacketUnitEdit(BaseModel):
    unit_id: str = ""
    source_file: str = ""
    source_path: str = ""
    pages: list[int] = Field(default_factory=list)
    doc_type: str = ""
    card_type: Optional[str] = None
    dropped: bool = False
    chain_id: str = ""
    business_ids: Optional[list[str]] = None
    suggested_doc_type: str = ""
    doc_type_source: Literal["ai", "human"] = "ai"
    boundary_confirmed: Optional[bool] = None
    business_binding_source: Optional[Literal["human"]] = None
    drop_reason: str = ""
    keys: dict[str, Any] = Field(default_factory=dict)


class PacketConfirmBody(BaseModel):
    units: list[PacketUnitEdit] = Field(default_factory=list)
    file_modes: dict[str, str] = Field(default_factory=dict)
    start_ocr: bool = True


class LedgerApplyBody(BaseModel):
    mapping: Optional[dict[str, Any]] = None


class DisambiguationAdoptBody(BaseModel):
    proposal: dict[str, Any]
    candidate_id: Optional[str] = None


class AdvisoryDecideBody(BaseModel):
    status: str
    reason: str = ""
    auto_replay: bool = True


class RelationDecideBody(BaseModel):
    status: str
    reason: str = ""


class ThreeWayCutoffBody(BaseModel):
    manual: Optional[dict[str, Any]] = None
    receipt_idx: Optional[int] = None


class ActiveChainBody(BaseModel):
    chain_id: str


class VisionAmountReviewBody(BaseModel):
    """Advisory input only; field acceptance remains a separate HITL action."""

    field_key: str
    candidates: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    page: int = Field(default=0, ge=0)


class BusinessGroupMoveBody(BaseModel):
    file_name: str
    target_group_id: str
    reason: str = "人工拖拽调整业务组"


class BusinessGroupConfirmBody(BaseModel):
    group_id: str
    reason: str = "审计师确认业务组"


class ManualLedgerMatchBody(BaseModel):
    file_name: str
    row_idx: Optional[int] = None
    label: Optional[str] = None


class ReclassifyBody(BaseModel):
    file_name: str
    doc_type: str


class FieldPlanBody(BaseModel):
    by_type: Optional[dict[str, Any]] = None
    global_extra: Optional[list[str]] = None
    confirmed: Optional[bool] = None


class PendingTypeBody(BaseModel):
    file_name: str
    doc_type: str


class WorkbookRowEditBody(BaseModel):
    format: str = "gospd01030"
    chain_id: str
    edits: dict[str, Any] = Field(default_factory=dict)


class FindingAcknowledgeBody(BaseModel):
    finding_id: str
    genuine: bool = True
    reason: str = ""


class FindingAcknowledgeBatchBody(BaseModel):
    """批量确认当前笔（或全任务）不通过项为单据问题。"""

    genuine: bool = True
    reason: str = ""
    # active=当前笔（GOSPD）；all=全任务
    scope: str = "active"
    chain_id: str = ""


class ChainReleaseBody(BaseModel):
    reason: str = ""
    ack_unacked: bool = True


class FieldRowVerifyBody(BaseModel):
    chain_id: str = ""
    field_key: str
    verified: bool = True
    reason: str = ""


class InterpretBody(BaseModel):
    family: str  # amount | contract | cutoff | three_way
    payload: Optional[dict[str, Any]] = None


class ConfirmReasonBody(BaseModel):
    reason: str = ""
    as_fail: bool = False


class ChainLinkageBody(BaseModel):
    """本笔人工核对：默认自动匹配 + 采纳建议关系后确认 Gate4。"""

    auto_evidence: bool = True
    auto_accept_relations: bool = True


class DuplicateOverrideBody(BaseModel):
    reason: str = ""


def _job_or_404(job_id: str) -> dict[str, Any]:
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def _http_from_value(exc: ValueError, *, job: dict[str, Any] | None = None) -> HTTPException:
    msg = str(exc)
    if job is not None and (
        "字段确认" in msg
        or "Gate3" in msg
        or "匹配确认" in msg
        or "Gate4" in msg
        or "字段相对确认" in msg
        or "匹配结果相对确认" in msg
    ):
        return HTTPException(status_code=400, detail={"message": msg, "job": job})
    return HTTPException(status_code=400, detail=msg)


def _require_fields(job_id: str) -> dict[str, Any]:
    try:
        return JOB_STORE.require_fields_confirmed(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except ValueError as exc:
        raise _http_from_value(exc, job=JOB_STORE.get(job_id)) from exc


def _require_matching_for_gate4(job_id: str) -> dict[str, Any]:
    try:
        return JOB_STORE.require_matching_confirmed(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except ValueError as exc:
        raise _http_from_value(exc, job=JOB_STORE.get(job_id)) from exc


def _find_document(job: dict[str, Any], file_name: str) -> dict[str, Any]:
    from urllib.parse import unquote

    want = unquote(str(file_name or "")).strip()
    want_base = Path(want).name
    pools: list[dict[str, Any]] = []
    pools.extend(job.get("classified") or [])
    pools.extend(job.get("pending_files") or [])
    for meta in (job.get("packet_run") or {}).get("files") or []:
        if isinstance(meta, dict):
            pools.append(meta)
    for item in pools:
        name = str(item.get("file_name") or "")
        if name == want or name == want_base or Path(name).name == want_base:
            return item
        if unquote(name) == want or Path(unquote(name)).name == want_base:
            return item
    raise HTTPException(status_code=404, detail=f"单据不存在: {file_name}")


@router.get("/ocr-status")
def get_ocr_status() -> dict[str, Any]:
    return ocr_status()


@router.get("/vision-status")
def get_vision_status() -> dict[str, Any]:
    """Return configuration status without exposing credentials."""
    from src.llm.baidu_vat_invoice import vat_invoice_status
    from src.llm.qianfan_vision import vision_status

    out = vision_status()
    out["vat_invoice"] = vat_invoice_status()
    return out


@router.post("/jobs/{job_id}/documents/{file_name}/vision/amount-review")
def review_amount_candidates_with_vision(
    job_id: str,
    file_name: str,
    body: VisionAmountReviewBody,
) -> dict[str, Any]:
    """Send one document page plus supplied candidates to Qianfan Vision.

    The response is advisory only. This endpoint never edits fields and never
    accepts a candidate; the subsequent human decision must use the HITL path.
    """
    from src.image_preprocess.preview_path import resolve_document_image_path
    from src.llm.qianfan_vision import review_amount_candidates
    from src.ui.preview_capture import render_preview_page

    job = _job_or_404(job_id)
    doc = _find_document(job, file_name)
    path = resolve_document_image_path(doc)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="原件路径无效")
    try:
        image_png, page_meta = render_preview_page(path, page_index=body.page)
        result = review_amount_candidates(
            image_png=image_png,
            field_key=body.field_key,
            candidates=body.candidates,
            ocr_text=str(doc.get("raw_text") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"千帆视觉复核失败：{exc}") from exc

    append_hitl_event(
        action="amount_ambiguity_ai_recommended",
        entity_type="document_field",
        entity_id=f"{doc.get('file_name')}:{body.field_key}",
        before=None,
        after=result,
        reason="千帆视觉金额候选预判断（仅建议，未确认字段）",
        extra={
            "job_id": job_id,
            "page": page_meta.get("page_index"),
            "candidate_ids": [str(x.get("candidate_id") or "") for x in body.candidates],
        },
    )
    return {"review": result, "page": page_meta}


@router.get("/prompts/catalog")
def get_prompts_catalog() -> dict[str, Any]:
    from src.llm.prompt_catalog import list_prompt_entries, render_sample_user

    summary = catalog_summary()
    enriched = []
    for entry in list_prompt_entries():
        row = {
            "task_type": entry.get("task_type"),
            "title": entry.get("title"),
            "description": entry.get("description"),
            "wired": entry.get("wired"),
            "sample_user": render_sample_user(entry),
        }
        enriched.append(row)
    summary["entries"] = enriched
    return summary


@router.get("/goals")
def get_goals() -> dict[str, Any]:
    return {"goals": list_workpaper_goals()}


@router.post("/plan")
def post_plan(body: SetGoalsBody) -> dict[str, Any]:
    try:
        return resolve_workflow_plan(body.goal_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs")
def create_job(body: CreateJobBody | None = None) -> dict[str, Any]:
    body = body or CreateJobBody()
    return JOB_STORE.create(title=body.title)


@router.get("/jobs")
def list_jobs() -> dict[str, Any]:
    return {"jobs": JOB_STORE.list_jobs()}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.put("/jobs/{job_id}/goals")
def put_goals(job_id: str, body: SetGoalsBody) -> dict[str, Any]:
    before = JOB_STORE.get(job_id)
    if not before:
        raise HTTPException(status_code=404, detail="任务不存在")
    old_period_end = before.get("period_end")
    old_cal = (before.get("calendar_mode"), before.get("fiscal_year_start"))
    try:
        job = JOB_STORE.set_goals(job_id, body.goal_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    patch: dict[str, Any] = {}
    if body.period_end is not None:
        pe = str(body.period_end or "").strip()
        patch["period_end"] = pe or None
    if body.entity_name is not None:
        name = str(body.entity_name or "").strip()
        patch["entity_name"] = name or None
    if body.calendar_mode is not None:
        mode = str(body.calendar_mode or "").strip() or None
        patch["calendar_mode"] = mode
    if body.fiscal_year_start is not None:
        fy = str(body.fiscal_year_start or "").strip() or None
        patch["fiscal_year_start"] = fy
    if patch:
        job = JOB_STORE.update(job_id, **patch)
    # 报告期末/日历变更 → 三单/截止与结论/底稿失效
    new_pe = job.get("period_end")
    new_cal = (job.get("calendar_mode"), job.get("fiscal_year_start"))
    if str(old_period_end or "") != str(new_pe or "") or old_cal != new_cal:
        JOB_STORE.invalidate_by_targets(
            job_id, ["cutoff", "three_way", "gate5", "workbook"]
        )
        job = JOB_STORE.get(job_id) or job
    # 选了 01030 却未配期末：写入计划提示（不硬拦，便于先上传）
    goals = set(job.get("goal_ids") or [])
    if "gospd01030" in goals and not job.get("period_end"):
        plan = dict(job.get("plan") or {})
        note = str(plan.get("note") or "")
        tip = "GOSPD01030 须配置报告期末日（period_end）后再跑截止与导出"
        if tip not in note:
            plan["note"] = (note + "；" + tip).strip("；") if note else tip
            job = JOB_STORE.update(job_id, plan=plan)
    append_hitl_event(
        action="set_workpaper_goals",
        entity_type="job",
        entity_id=job_id,
        before={"period_end": old_period_end},
        after={
            "goal_ids": body.goal_ids,
            "steps": (job.get("plan") or {}).get("required_steps"),
            "period_end": job.get("period_end"),
            "entity_name": job.get("entity_name"),
        },
        reason="工作台选择底稿目标",
    )
    return job


@router.patch("/jobs/{job_id}/active-step")
def patch_active_step(job_id: str, body: ActiveStepBody) -> dict[str, Any]:
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    allowed = set(job.get("plan", {}).get("required_steps") or []) | {
        "goals",
        "sample_desk",
        "packet_unpack",
        "prompts",
        "hard_cases",
    }
    if body.step_id not in allowed and body.step_id != "goals":
        raise HTTPException(
            status_code=400,
            detail=f"步骤不在本次底稿计划内: {body.step_id}",
        )
    return JOB_STORE.update(job_id, touch=False, active_step=body.step_id)


@router.put("/jobs/{job_id}/sample-population")
def put_sample_population(job_id: str, body: SamplePopulationBody) -> dict[str, Any]:
    """导入外部抽样已选业务号（模块30最小接入；不替代抽样设计）。"""
    if not JOB_STORE.get(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    from src.audit.sample_population import build_sample_population

    pop = build_sample_population(
        business_ids=body.business_ids,
        source=body.source,
        note=body.note,
    )
    job = JOB_STORE.update(job_id, sample_population=pop)
    from src.workflow.sample_desk import replay_after_sample_replace

    job = replay_after_sample_replace(job_id)
    append_hitl_event(
        action="import_sample_population",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after={"count": pop.get("count"), "source": pop.get("source")},
        reason="导入上游抽样清单",
    )
    return job


@router.post("/jobs/{job_id}/sample-population/excel")
async def post_sample_population_excel(
    job_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """上传裁剪序时账：立样本笔，同时写入测试用账（上传页不再另传）。"""
    if not JOB_STORE.get(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    from src.audit.sample_population import (
        build_sample_population,
        ledger_patch_from_parsed,
        parse_sample_workbook,
    )
    from src.workflow.pipeline import job_workdir

    name = str(file.filename or "sample.xlsx")
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 裁剪序时账")
    folder = job_workdir(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / "sample_population.xlsx"
    dest.write_bytes(await file.read())
    try:
        parsed = parse_sample_workbook(dest)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pop = build_sample_population(
        business_ids=parsed["business_ids"],
        source="excel",
        note=f"裁剪序时账 {name} · {', '.join(parsed.get('sheets') or [])}",
        rows=parsed.get("rows"),
        sheets=parsed.get("sheets"),
    )
    patch = {
        "sample_population": pop,
        **ledger_patch_from_parsed(parsed, path=str(dest)),
    }
    job = JOB_STORE.update(job_id, **patch)
    from src.workflow.sample_desk import replay_after_sample_replace

    job = replay_after_sample_replace(job_id)
    append_hitl_event(
        action="import_sample_population_excel",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after={"count": pop.get("count"), "sheets": pop.get("sheets"), "ledger_rows": len(patch.get("ledger_rows") or [])},
        reason="导入裁剪序时账（立样本+测试用账）",
    )
    return job


@router.get("/jobs/{job_id}/chains")
def get_chains(job_id: str) -> dict[str, Any]:
    job = _job_or_404(job_id)
    from src.workflow.amount_ambiguity import AMBIGUITY_KEY, OPEN_STATUSES, scan_job_documents
    from src.workflow.sample_desk import build_desk_chains, desk_light_summary

    # 仅当已有未关闭歧义时才重扫；短时去重，避免前端连打 /chains 反复整包扫描
    has_open = any(
        isinstance(row, dict) and str(row.get("status") or "").upper() in OPEN_STATUSES
        for item in (job.get("classified") or [])
        if isinstance(item, dict)
        for row in (item.get(AMBIGUITY_KEY) or [])
    )
    if has_open:
        import time

        now = time.time()
        last = float(_CHAINS_AMB_SCAN_AT.get(job_id) or 0)
        if now - last >= 2.5:
            scan_job_documents(job)
            job = JOB_STORE.update(job_id, classified=list(job.get("classified") or []))
            _CHAINS_AMB_SCAN_AT[job_id] = now
    samples = job.get("gospd_sample_results") or {}
    active = resolve_active_chain_id(job)
    pop = job.get("sample_population")
    enriched = []
    for c in build_desk_chains(job):
        cid = c["chain_id"]
        sample = samples.get(cid) or {}
        enriched.append(
            {
                **c,
                "tested": sample_test_complete(sample, job),
                "has_contract": bool(sample.get("contract_terms")),
                "has_amount": bool(sample.get("amount_test")),
                "has_three_way": bool(sample.get("three_way")),
                "matching_confirmed": bool(sample.get("matching_confirmed"))
                or (
                    cid == active and bool(job.get("matching_confirmed"))
                ),
                "is_active": cid == active,
            }
        )
    return {
        "chains": enriched,
        "lights": desk_light_summary(enriched),
        "active_chain_id": active,
        "gospd_mode": is_gospd_mode(job),
        "sample_population": pop,
    }


@router.put("/jobs/{job_id}/active-chain")
def put_active_chain(job_id: str, body: ActiveChainBody) -> dict[str, Any]:
    if not JOB_STORE.get(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        return JOB_STORE.set_active_chain(job_id, body.chain_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/business-groups/move-document")
def post_move_business_group_document(job_id: str, body: BusinessGroupMoveBody) -> dict[str, Any]:
    """Human grouping adjustment.  This is an override, never an AI decision."""
    from datetime import datetime, timezone

    job = _job_or_404(job_id)
    target = str(body.target_group_id or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="target_group_id is required")
    classified = list(job.get("classified") or [])
    found = None
    before = None
    for doc in classified:
        if str(doc.get("file_name") or "") == body.file_name:
            found = doc
            before = doc.get("business_group_id")
            doc["business_group_id"] = target
            doc["business_group_manual"] = True
            break
    if found is None:
        raise HTTPException(status_code=404, detail=f"document not found: {body.file_name}")
    JOB_STORE.invalidate_downstream_from_fields(job_id)
    updated = JOB_STORE.update(job_id, classified=classified, business_group_confirmations={})
    append_hitl_event(
        action="move_business_group_document",
        entity_type="document",
        entity_id=body.file_name,
        before={"group_id": before},
        after={"group_id": target},
        reason=body.reason or "人工拖拽调整业务组",
        extra={"job_id": job_id, "human_override": True, "at": datetime.now(timezone.utc).isoformat()},
    )
    return updated


@router.post("/jobs/{job_id}/hitl/business-groups/confirm")
def confirm_business_group(job_id: str, body: BusinessGroupConfirmBody) -> dict[str, Any]:
    """Confirm the document binding only; field consistency remains a later gate."""
    from datetime import datetime, timezone

    job = _job_or_404(job_id)
    group_id = str(body.group_id or "").strip()
    group = next(
        (g for g in list_business_chains(list(job.get("classified") or [])) if g["chain_id"] == group_id),
        None,
    )
    if not group:
        raise HTTPException(status_code=404, detail=f"business group not found: {group_id}")
    confirmations = dict(job.get("business_group_confirmations") or {})
    confirmations[group_id] = {
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "reason": body.reason or "审计师确认业务组",
    }
    updated = JOB_STORE.update(job_id, business_group_confirmations=confirmations)
    append_hitl_event(
        action="confirm_business_group",
        entity_type="business_group",
        entity_id=group_id,
        before=None,
        after={"confirmed": True, "document_count": group.get("doc_count")},
        reason=body.reason or "审计师确认业务组",
        extra={"job_id": job_id},
    )
    return updated


def _build_seed_fields(
    path_str: str,
    doc_type: str,
    name: str,
    *,
    fast: bool = False,
) -> tuple[dict[str, Any], str]:
    """演示/快速载入：从 PDF 文字层 + LLM 抽取字段，硬编码仅作兜底。"""
    from src.legacy_ocr.ocr_adapter import LegacyOcrAdapter, _extract_pdf_text_layer
    from src.workflow.classify import DOC_TYPE_TO_OCR
    from src.workflow.pipeline import (
        ensure_payment_terms,
        fallback_fields_from_filename,
        merge_fields,
    )

    raw = _extract_pdf_text_layer(path_str) if path_str else ""
    fallback = _demo_fields(doc_type, name)
    file_fb = fallback_fields_from_filename(name, doc_type)
    if fast:
        # 回归/联调用：禁止碰 LLM（ensure_payment_terms 也可能走模型）
        return merge_fields(dict(file_fb or {}), fallback), raw
    if not raw.strip():
        return fallback, raw

    adapter = LegacyOcrAdapter()
    ocr_type = DOC_TYPE_TO_OCR.get(doc_type, "other")
    try:
        fields = dict(adapter.extract_fields(raw, ocr_type) or {})
    except Exception:
        fields = {}
    fields = merge_fields(fields, file_fb)
    for k, v in fallback.items():
        if v is not None and str(v).strip() and not str(fields.get(k) or "").strip():
            fields[k] = v
    if doc_type in {"contract", "order"}:
        fields = ensure_payment_terms(fields, raw)
    return fields, raw


@router.post("/jobs/{job_id}/seed-demo")
def seed_demo(
    job_id: str,
    extra_dirs: str = Query(
        default="",
        description="可选：逗号分隔的相对 data/mock 子目录，追加为第二笔及以后",
    ),
    fast: bool = Query(default=False, description="跳过 LLM，仅文件名/文字层启发式"),
) -> dict[str, Any]:
    """注入演示单据（不跑 OCR），便于先验工作台交互。"""
    if not JOB_STORE.get(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    import shutil

    from src.models.field_values import seed_field_meta

    type_map = {
        "销售合同": "contract",
        "销售订单": "order",
        "销售发货单": "delivery",
        "出口发货": "delivery",
        "验收": "receipt",
        "签收": "receipt",
        "增值税发票": "invoice",
        "商业发票": "invoice",
        "银行回款": "payment",
        "海运提单": "receipt",
    }

    def _seed_dir(demo_dir: Path, classified: list[dict[str, Any]]) -> None:
        if not demo_dir.is_dir():
            return
        work = job_workdir(job_id)
        work.mkdir(parents=True, exist_ok=True)
        for pdf in sorted(demo_dir.glob("*.pdf")):
            name = pdf.name
            doc_type = "other"
            for tip, dt in type_map.items():
                if tip in name:
                    doc_type = dt
                    break
            dest = work / name
            try:
                shutil.copy2(pdf, dest)
                path_str = str(dest)
            except OSError:
                path_str = str(pdf)
            fields, raw_text = _build_seed_fields(
                path_str, doc_type, name, fast=fast
            )
            doc_item: dict[str, Any] = {
                "file_name": name,
                "path": path_str,
                "doc_type": doc_type,
                "ocr_source": "demo_seed",
                "raw_text": raw_text,
                "text_blocks": [],
                "fields": fields,
            }
            seed_field_meta(doc_item, source="demo_pdf_extract", extractor="seed_from_pdf")
            classified.append(doc_item)

    classified: list[dict[str, Any]] = []
    _seed_dir(ROOT / "data" / "mock" / "SO25-0281", classified)
    for rel in [x.strip() for x in str(extra_dirs or "").split(",") if x.strip()]:
        _seed_dir(ROOT / "data" / "mock" / rel, classified)

    if not classified:
        classified = [
            {
                "file_name": "demo_contract.pdf",
                "path": "",
                "doc_type": "contract",
                "ocr_source": "demo_seed",
                "raw_text": "合同编号 HT25-DEMO 签收后30日付款",
                "text_blocks": [],
                "fields": {
                    "documentNo": "HT25-DEMO",
                    "paymentTerms": "签收后30日付款",
                    "controlTransferTerms": "签收后转移控制权",
                    "totalAmount": 100000,
                },
            },
            {
                "file_name": "demo_invoice.pdf",
                "path": "",
                "doc_type": "invoice",
                "ocr_source": "demo_seed",
                "raw_text": "发票号码 12345678",
                "text_blocks": [],
                "fields": {
                    "invoiceNo": "12345678",
                    "documentDate": "2025-01-15",
                    "postingDate": "2025-01-20",
                    "totalAmount": 100000,
                    "taxAmount": 13000,
                    "amount": 87000,
                },
            },
        ]
    job = JOB_STORE.set_classified(job_id, classified)
    return job


def _demo_fields(doc_type: str, name: str) -> dict[str, Any]:
    base = {"documentNo": "SO25-0281"}
    if doc_type == "contract":
        return {
            **base,
            "contractNo": "HT25-0281",
            "buyerName": "华东某整车制造有限公司",
            "supplierName": "华曜汽车零部件制造有限公司",
            "sellerName": "华曜汽车零部件制造有限公司",
            "paymentTerms": "发票开具之日起30日内以银行转账方式支付全部价款",
            "transportTerms": "卖方送货至买方上海嘉定仓库",
            "controlTransferTerms": "以验收期届满日或验收合格意见签署日（孰早）作为卖方完成交付、货物控制权转移的日期",
            "documentDate": "2025-12-05",
            "totalAmount": 10942.90,
        }
    if doc_type == "order":
        return {
            **base,
            "orderNo": "SO25-0281",
            "contractNo": "HT25-0281",
            "buyerName": "华东某整车制造有限公司",
            "supplierName": "华曜汽车零部件制造有限公司",
            "sellerName": "华曜汽车零部件制造有限公司",
            "totalAmount": 10942.90,
            "quantity": 357,
            "documentDate": "2025-12-12",
            "transportTerms": "卖方送货至买方上海嘉定仓库",
        }
    if doc_type == "invoice":
        return {
            **base,
            "invoiceNo": "25322025000000002811",
            "orderNo": "SO25-0281",
            "contractNo": "HT25-0281",
            "documentDate": "2025-12-20",
            "postingDate": "2025-12-20",
            "totalAmount": 10942.90,
            "taxAmount": 1258.92,
            "amount": 9683.98,
            "quantity": 357,
            "buyerName": "华东某整车制造有限公司",
            "supplierName": "华曜汽车零部件制造有限公司",
        }
    if doc_type == "receipt":
        # 产品验收单 / 客户签收验收单：到货 12-30，验收完成 01-02；原件通常不列金额
        acceptance = "2026-01-02" if "验收" in name or "签收" in name else "2025-12-30"
        fields = {
            **base,
            "orderNo": "SO25-0281",
            "contractNo": "HT25-0281",
            "buyerName": "华东某整车制造有限公司",
            "acceptanceDate": acceptance,
            "documentDate": acceptance,
            "quantity": 357,
        }
        # 客户签收单正文往往不印销方全称，勿硬塞导致点格永远不亮
        if "客户签收" not in name:
            fields["supplierName"] = "华曜汽车零部件制造有限公司"
        return fields
    if doc_type == "delivery":
        return {
            **base,
            "orderNo": "SO25-0281",
            "deliveryDate": "2025-12-30",
            "documentDate": "2025-12-30",
        }
    if doc_type == "payment":
        return {
            **base,
            "orderNo": "SO25-0281",
            "totalAmount": 10942.90,
            "documentDate": "2026-01-18",
        }
    return base


@router.patch("/jobs/{job_id}/documents/fields")
def patch_fields(job_id: str, body: PatchFieldsBody) -> dict[str, Any]:
    if not JOB_STORE.get(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        job = JOB_STORE.patch_document_fields(
            job_id,
            file_name=body.file_name,
            fields=body.fields,
            doc_type=body.doc_type,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    append_hitl_event(
        action="accept_field_batch",
        entity_type="document",
        entity_id=body.file_name,
        before=None,
        after={"fields": body.fields, "doc_type": body.doc_type},
        reason="工作台保存字段",
        extra={"job_id": job_id},
    )
    return job


@router.post("/jobs/{job_id}/hitl/fields/confirm")
def confirm_fields(job_id: str) -> dict[str, Any]:
    if not JOB_STORE.get(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    from src.workflow.packet_engine import packet_needs_review

    job = JOB_STORE.get(job_id) or {}
    if packet_needs_review(job):
        raise HTTPException(status_code=400, detail="请先完成拆包分笔，再确认字段")
    try:
        job = JOB_STORE.confirm_fields(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    append_hitl_event(
        action="confirm_all_fields",
        entity_type="job",
        entity_id=job_id,
        before={"confirmed": False},
        after={"confirmed": True, "sig": job.get("fields_confirm_sig")},
        reason="工作台确认全部字段",
    )
    return job


class AmountAmbiguityDecideBody(BaseModel):
    decision: str
    candidate_id: Optional[str] = None
    value: Optional[Any] = None
    reason: str = ""


@router.get("/jobs/{job_id}/amount-ambiguities")
def get_amount_ambiguities(
    job_id: str,
    chain_id: Optional[str] = Query(default=None),
    rescan: bool = Query(
        default=False,
        description="true=重扫 OCR/字段；默认 false 避免采用后列表横跳",
    ),
) -> dict[str, Any]:
    job = _job_or_404(job_id)
    from src.workflow.amount_ambiguity import list_open_ambiguities, scan_job_documents

    # 轻量重扫（不调视觉），避免 job 里残留旧规则扫出的开放卡
    scan_job_documents(job, chain_id=chain_id)
    JOB_STORE.update(job_id, classified=list(job.get("classified") or []))
    job = _job_or_404(job_id)
    items = list_open_ambiguities(job, chain_id=chain_id)
    return {"items": items, "count": len(items), "chain_id": chain_id}


@router.post("/jobs/{job_id}/amount-ambiguities/scan")
def post_scan_amount_ambiguities(
    job_id: str,
    chain_id: Optional[str] = Query(default=None),
    enrich: bool = Query(default=True, description="扫描后自动增值税/视觉增强"),
) -> dict[str, Any]:
    job = _job_or_404(job_id)
    from src.workflow.amount_ambiguity import (
        enrich_job_ambiguities,
        list_open_ambiguities,
        scan_job_documents,
    )

    opened = scan_job_documents(job, chain_id=chain_id)
    enrich_summary: dict[str, Any] = {}
    if enrich:
        enrich_summary = enrich_job_ambiguities(job, chain_id=chain_id)
    updated = JOB_STORE.update(job_id, classified=list(job.get("classified") or []))
    append_hitl_event(
        action="amount_ambiguity_detected",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after={
            "opened": len(opened),
            "chain_id": chain_id,
            "enrich": enrich_summary,
        },
        reason="扫描金额歧义",
    )
    items = list_open_ambiguities(updated, chain_id=chain_id)
    return {
        "job": updated,
        "items": items,
        "count": len(items),
        "enrich": enrich_summary,
    }


@router.post("/jobs/{job_id}/amount-ambiguities/enrich")
def post_enrich_amount_ambiguities(
    job_id: str,
    chain_id: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    """Run VAT specialist + vision on open ambiguities (advisory only)."""
    job = _job_or_404(job_id)
    from src.workflow.amount_ambiguity import enrich_job_ambiguities, list_open_ambiguities

    summary = enrich_job_ambiguities(job, chain_id=chain_id)
    updated = JOB_STORE.update(job_id, classified=list(job.get("classified") or []))
    append_hitl_event(
        action="amount_ambiguity_enriched",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after=summary,
        reason="金额歧义自动增强（增值税/视觉，仅建议）",
    )
    items = list_open_ambiguities(updated, chain_id=chain_id)
    return {"job": updated, "items": items, "count": len(items), "enrich": summary}


@router.post("/jobs/{job_id}/amount-ambiguities/{ambiguity_id}/decide")
def post_decide_amount_ambiguity(
    job_id: str,
    ambiguity_id: str,
    body: AmountAmbiguityDecideBody,
) -> dict[str, Any]:
    from src.workflow.amount_ambiguity import decide_ambiguity, find_ambiguity

    job = _job_or_404(job_id)
    found = find_ambiguity(job, ambiguity_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"ambiguity not found: {ambiguity_id}")
    item, _row = found
    try:
        decided = decide_ambiguity(
            item,
            ambiguity_id,
            decision=body.decision,
            candidate_id=body.candidate_id,
            value=body.value,
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if str(body.decision).upper() in {"ACCEPT_CANDIDATE", "MANUAL_VALUE"}:
        JOB_STORE.invalidate_downstream_from_fields(job_id)
    updated = JOB_STORE.update(job_id, classified=list(job.get("classified") or []))
    append_hitl_event(
        action=(
            "amount_ambiguity_candidate_accepted"
            if str(body.decision).upper() == "ACCEPT_CANDIDATE"
            else "amount_ambiguity_manual_value_entered"
            if str(body.decision).upper() == "MANUAL_VALUE"
            else "amount_ambiguity_deferred"
        ),
        entity_type="document_field",
        entity_id=f"{item.get('file_name')}:{decided.get('field_key')}",
        before=None,
        after=decided.get("human_decision"),
        reason=body.reason or "金额歧义人工确认",
        extra={"job_id": job_id, "ambiguity_id": ambiguity_id},
    )
    return {"job": updated, "ambiguity": decided}


@router.post("/jobs/{job_id}/amount-ambiguities/{ambiguity_id}/ai-review")
def post_ai_review_amount_ambiguity(
    job_id: str,
    ambiguity_id: str,
    page: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Run Qianfan vision on an open ambiguity; advisory only."""
    from src.llm.qianfan_vision import review_amount_candidates
    from src.ui.preview_capture import render_preview_page
    from src.workflow.amount_ambiguity import apply_ai_recommendation, find_ambiguity

    job = _job_or_404(job_id)
    found = find_ambiguity(job, ambiguity_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"ambiguity not found: {ambiguity_id}")
    item, row = found
    path = Path(str(item.get("path") or ""))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="原件路径无效")
    candidates = list(row.get("candidates") or [])
    if not candidates:
        raise HTTPException(status_code=400, detail="该歧义没有可供复核的候选")
    try:
        image_png, page_meta = render_preview_page(path, page_index=page)
        result = review_amount_candidates(
            image_png=image_png,
            field_key=str(row.get("field_key") or "totalAmount"),
            candidates=candidates,
            ocr_text=str(item.get("raw_text") or ""),
        )
        apply_ai_recommendation(item, ambiguity_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"千帆视觉复核失败：{exc}") from exc
    updated = JOB_STORE.update(job_id, classified=list(job.get("classified") or []))
    append_hitl_event(
        action="amount_ambiguity_ai_recommended",
        entity_type="document_field",
        entity_id=f"{item.get('file_name')}:{row.get('field_key')}",
        before=None,
        after=result,
        reason="千帆视觉金额候选预判断（仅建议）",
        extra={"job_id": job_id, "ambiguity_id": ambiguity_id, "page": page_meta.get("page_index")},
    )
    return {"job": updated, "review": result, "page": page_meta}


@router.post("/jobs/{job_id}/fields/gap-fill")
def post_fields_gap_fill(
    job_id: str,
    scope: str = Query(default="active", description="active=仅当前笔；all=全部单据"),
) -> dict[str, Any]:
    """对缺失关键字段补抽：启发式优先，语义缺口才调 LLM；默认只处理当前笔。"""
    job = _job_or_404(job_id)
    classified = list(job.get("classified") or [])
    if not classified:
        raise HTTPException(status_code=400, detail="尚无识别单据，请先上传并处理")
    from src.workflow.field_gap_fill import gap_fill_classified_documents

    field_plan = job.get("field_plan")
    scope_n = str(scope or "active").strip().lower()
    chain_id = resolve_active_chain_id(job) if is_gospd_mode(job) else None
    work = classified
    used_scope = "all"
    if scope_n != "all" and chain_id:
        subset = docs_for_chain(classified, chain_id)
        if subset:
            work = subset
            used_scope = "active"

    filled_work, summary = gap_fill_classified_documents(work, field_plan=field_plan)
    if used_scope == "active":
        by_name = {str(d.get("file_name") or ""): d for d in filled_work}
        filled = [
            by_name.get(str(d.get("file_name") or ""), d) for d in classified
        ]
    else:
        filled = filled_work
    summary = {
        **summary,
        "scope": used_scope,
        "active_chain_id": chain_id,
        "docs_in_scope": len(work),
    }
    patch: dict[str, Any] = {"classified": filled}
    if int(summary.get("fields_filled") or 0) > 0 or int(summary.get("text_hydrated") or 0) > 0:
        # 补抽改变字段工作副本：需重新字段确认；保留证据以免 Gate4 永灰
        patch.update(
            {
                "fields_confirmed": False,
                "fields_confirm_sig": None,
                "matching_confirmed": False,
                "matching_confirm_sig": None,
                "conclusion_confirmed": False,
                "conclusion_confirm_sig": None,
            }
        )
    job = JOB_STORE.update(job_id, **patch)
    append_hitl_event(
        action="fields_llm_gap_fill",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after=summary,
        reason="缺失字段补抽" + ("（当前笔）" if used_scope == "active" else ""),
    )
    return {"job": job, "summary": summary}


@router.get("/jobs/{job_id}/coverage")
def get_coverage(job_id: str) -> dict[str, Any]:
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    full = build_coverage_map(
        classified=job.get("classified"),
        evidence=job.get("evidence"),
        amount=job.get("amount_test"),
        contract=job.get("contract_terms"),
        three_way=job.get("three_way"),
        fields_confirmed=bool(job.get("fields_confirmed")),
        matching_confirmed=bool(job.get("matching_confirmed")),
        conclusion_confirmed=bool(job.get("conclusion_confirmed")),
        relations=job.get("relations") or [],
        duplicates=job.get("duplicates") or {},
        sample_population=job.get("sample_population"),
    )
    required = set((job.get("plan") or {}).get("required_dimensions") or [])
    dims = list(full.get("dimensions") or [])
    if required:
        for d in dims:
            if d.get("dimension_id") not in required:
                d["status"] = "NOT_APPLICABLE"
                d["note"] = (d.get("note") or "") + "｜本次底稿目标不要求"
        dims = [
            d
            for d in dims
            if d.get("dimension_id") in required
            or d.get("status") == "NOT_APPLICABLE"
        ]
        # 只展示本次相关 + 被标 N/A 的说明可收进 filtered
        dims = [d for d in dims if d.get("dimension_id") in required]
    full["dimensions"] = dims
    full["filtered_by_goals"] = list((job.get("plan") or {}).get("goal_ids") or [])
    try:
        from src.audit.program_matrix import matrix_for_job

        full["program_matrix"] = matrix_for_job(job)
    except Exception:
        full["program_matrix"] = None
    return full


@router.get("/program-matrix")
def get_program_matrix(goal_id: str | None = None) -> dict[str, Any]:
    """底稿目标 → 程序/认定/证据矩阵（不依赖任务）。"""
    from src.audit.program_matrix import get_program_matrix as _matrix

    return _matrix(goal_id)


@router.post("/jobs/{job_id}/upload")
async def upload_documents(
    job_id: str,
    files: list[UploadFile] = File(...),
    force: bool = Form(False),
    # 默认仅落盘入队，需前端点「开始处理」再 OCR
    process: bool = Form(False),
    slot_hints: str = Form(""),
) -> dict[str, Any]:
    from src.workflow.pipeline import save_bytes_to_workdir

    _job_or_404(job_id)
    hints: dict[str, str] = {}
    if slot_hints.strip():
        try:
            hints = json.loads(slot_hints)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="slot_hints 不是合法 JSON") from exc

    specs: list[dict[str, Any]] = []
    new_pending: list[dict[str, Any]] = []
    workdir = job_workdir(job_id)
    from src.workflow.classify import light_classify_file

    for upload in files:
        content = await upload.read()
        filename = upload.filename or "upload.bin"
        slot = hints.get(filename, "")
        path = save_bytes_to_workdir(workdir, filename, content)
        specs.append({"filename": filename, "content": content, "slot_hint": slot})
        light = light_classify_file(filename, str(path), slot_hint=slot)
        new_pending.append(
            {
                "file_name": filename,
                "path": str(path),
                "slot_hint": slot,
                "size": len(content),
                "doc_type": light.get("doc_type") or "other",
                "doc_type_source": light.get("doc_type_source") or "light",
                "light_confident": bool(light.get("confident")),
            }
        )

    job = JOB_STORE.get(job_id) or {}
    if process:
        from src.workflow.field_catalog import ensure_field_plan

        field_plan = ensure_field_plan(job.get("field_plan"))
        classified = process_uploaded_files(
            job_id,
            specs,
            existing=[] if force else (job.get("classified") or []),
            force=force,
            field_plan=field_plan,
        )
        if job.get("ledger_rows") and job.get("ledger_mapping"):
            classified = apply_ledger_to_classified_list(
                classified,
                job["ledger_rows"],
                job["ledger_mapping"],
            )
        issues = collect_ocr_issues(classified)
        job = JOB_STORE.set_classified(job_id, classified)
        job = JOB_STORE.update(job_id, ocr_issues=issues, pending_files=[])
    else:
        # 追加待处理队列（同名覆盖）
        merged: dict[str, dict[str, Any]] = {
            str(x.get("file_name") or ""): x
            for x in (job.get("pending_files") or [])
            if x.get("file_name")
        }
        for item in new_pending:
            merged[str(item["file_name"])] = item
        from src.workflow.field_catalog import auto_confirm_field_plan, ensure_field_plan
        from src.workflow.packet_engine import annotate_pending_kinds, pending_raw_packets

        plan = auto_confirm_field_plan(job.get("field_plan"))
        pending_list = annotate_pending_kinds(list(merged.values()))
        patch: dict[str, Any] = {
            "pending_files": pending_list,
            "field_plan": plan,
        }
        if new_pending and pending_raw_packets({"pending_files": pending_list}):
            patch["packet_confirmed"] = False
            patch["packet_units"] = []
            patch["packet_run"] = {
                "run_id": "",
                "status": "pending_analyze",
                "created_at": None,
                "confirmed_at": None,
                "files": [],
                "warnings": [],
                "pages": [],
            }
        job = JOB_STORE.update(job_id, **patch)
    return job


@router.get("/field-catalog")
def get_field_catalog() -> dict[str, Any]:
    from src.workflow.field_catalog import catalog_payload

    return catalog_payload()


@router.put("/jobs/{job_id}/field-plan")
def put_field_plan(
    job_id: str,
    body: FieldPlanBody,
    confirm: bool = False,
) -> dict[str, Any]:
    """保存 / 确认 OCR 前字段清单。"""
    from src.workflow.field_catalog import ensure_field_plan, normalize_field_plan_update

    _job_or_404(job_id)
    do_confirm = confirm or bool(body.confirmed)
    raw = {
        "by_type": body.by_type,
        "global_extra": body.global_extra,
    }
    # 合并现有 plan，避免只传部分字段时清空
    job = JOB_STORE.get(job_id) or {}
    base = ensure_field_plan(job.get("field_plan"))
    if body.by_type is not None:
        base["by_type"] = body.by_type
    if body.global_extra is not None:
        base["global_extra"] = body.global_extra
    plan = normalize_field_plan_update(base, confirm=do_confirm)
    return JOB_STORE.update(job_id, field_plan=plan)


@router.patch("/jobs/{job_id}/pending/type")
def patch_pending_type(job_id: str, body: PendingTypeBody) -> dict[str, Any]:
    """轻量分类结果：改待处理单据类型（不跑 OCR）。"""
    from src.workflow.classify import DOC_TYPE_LABELS
    from src.workflow.field_catalog import auto_confirm_field_plan

    job = _job_or_404(job_id)
    dt = (body.doc_type or "").strip()
    if dt not in DOC_TYPE_LABELS:
        raise HTTPException(status_code=400, detail=f"未知单据类型: {dt}")
    pending = list(job.get("pending_files") or [])
    found = False
    for item in pending:
        if str(item.get("file_name") or "") == body.file_name:
            item["doc_type"] = dt
            item["doc_type_source"] = "manual"
            item["light_confident"] = True
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="待处理文件不存在")
    plan = auto_confirm_field_plan(job.get("field_plan"))
    return JOB_STORE.update(job_id, pending_files=pending, field_plan=plan)


@router.post("/jobs/{job_id}/classify-light")
def post_classify_light(job_id: str) -> dict[str, Any]:
    """对当前待处理文件重跑轻量分类。"""
    from src.workflow.classify import light_classify_file
    from src.workflow.field_catalog import auto_confirm_field_plan

    job = _job_or_404(job_id)
    pending = list(job.get("pending_files") or [])
    for item in pending:
        light = light_classify_file(
            str(item.get("file_name") or ""),
            str(item.get("path") or ""),
            slot_hint=str(item.get("slot_hint") or ""),
        )
        item["doc_type"] = light.get("doc_type") or "other"
        item["doc_type_source"] = light.get("doc_type_source") or "light"
        item["light_confident"] = bool(light.get("confident"))
    plan = auto_confirm_field_plan(job.get("field_plan"))
    return JOB_STORE.update(job_id, pending_files=pending, field_plan=plan)


_packet_analyze_lock = threading.Lock()
_packet_analyze_inflight: set[str] = set()


def _execute_packet_analyze(
    job_id: str,
    *,
    use_vlm: bool = True,
    file_modes: dict[str, str] | None = None,
    set_unpack_step: bool = False,
    hitl: bool = False,
) -> dict[str, Any] | None:
    """同一任务同时只跑一趟拆包分析。已在跑则返回 None。"""
    with _packet_analyze_lock:
        if job_id in _packet_analyze_inflight:
            return None
        _packet_analyze_inflight.add(job_id)
    try:
        from src.workflow.packet_engine import analyze_pending_packets, annotate_pending_kinds

        job = _job_or_404(job_id)
        pending = annotate_pending_kinds(list(job.get("pending_files") or []))
        job = JOB_STORE.update(job_id, pending_files=pending)
        analyzing = dict(job.get("packet_run") or {})
        analyzing["status"] = "analyzing"
        JOB_STORE.update(job_id, packet_run=analyzing)
        job = JOB_STORE.get(job_id) or job
        patch = analyze_pending_packets(
            job,
            use_vlm=use_vlm,
            file_modes=dict(file_modes or {}),
        )
        extra: dict[str, Any] = {}
        if set_unpack_step and patch["packet_run"].get("status") == "needs_review":
            extra["active_step"] = "packet_unpack"
        job = JOB_STORE.update(
            job_id,
            packet_run=patch["packet_run"],
            packet_units=patch["packet_units"],
            pending_files=patch["pending_files"],
            packet_confirmed=patch["packet_confirmed"],
            **extra,
        )
        if hitl:
            append_hitl_event(
                action="packet_analyze",
                entity_type="job",
                entity_id=job_id,
                before=None,
                after={
                    "status": (job.get("packet_run") or {}).get("status"),
                    "units": len(job.get("packet_units") or []),
                },
                reason="拆包分笔分析",
            )
        return job
    finally:
        with _packet_analyze_lock:
            _packet_analyze_inflight.discard(job_id)


@router.post("/jobs/{job_id}/packet/analyze")
def post_packet_analyze(job_id: str, body: PacketAnalyzeBody | None = None) -> dict[str, Any]:
    """切分凭证包并给出类型/分笔候选；不写 classified。"""
    body = body or PacketAnalyzeBody()
    job = _execute_packet_analyze(
        job_id,
        use_vlm=bool(body.use_vlm),
        file_modes=dict(body.file_modes or {}),
        set_unpack_step=True,
        hitl=True,
    )
    if job is None:
        return _job_or_404(job_id)
    return job


@router.post("/jobs/{job_id}/packet/confirm")
def post_packet_confirm(job_id: str, body: PacketConfirmBody) -> dict[str, Any]:
    """确认拆包：物化虚拟单据后可开始识别。"""
    from src.workflow.packet_engine import confirm_packet

    job = _job_or_404(job_id)
    edits = [item.model_dump() for item in (body.units or [])]
    path_by_file = {
        str(f.get("file_name") or ""): str(f.get("path") or "")
        for f in ((job.get("packet_run") or {}).get("files") or [])
    }
    for edit in edits:
        if not edit.get("source_path"):
            edit["source_path"] = path_by_file.get(str(edit.get("source_file") or ""), "")
    try:
        patch = confirm_packet(
            job,
            units=edits,
            file_modes=dict(body.file_modes or {}),
            start_ocr=body.start_ocr,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    specs = patch.pop("materialized_specs", [])
    job = JOB_STORE.update(
        job_id,
        packet_run=patch["packet_run"],
        packet_units=patch["packet_units"],
        pending_files=patch["pending_files"],
        packet_confirmed=True,
        active_step="upload_ocr",
    )
    append_hitl_event(
        action="packet_confirm",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after={"units": len(patch.get("packet_units") or []), "files": len(specs)},
        reason="确认拆包并物化单据",
    )
    if body.start_ocr:
        return process_pending(job_id, force=False)
    return job


@router.get("/jobs/{job_id}/chains/preview")
def preview_chains(job_id: str) -> dict[str, Any]:
    """上传/OCR 前按文件名 SO/HT 预览业务笔分组。"""
    from src.reporting.gospd01010_filler import group_classified_by_chain

    job = _job_or_404(job_id)
    pseudo: list[dict[str, Any]] = []
    for p in job.get("pending_files") or []:
        pseudo.append(
            {
                "file_name": p.get("file_name"),
                "doc_type": p.get("doc_type") or "other",
                "fields": {},
            }
        )
    for d in job.get("classified") or []:
        pseudo.append(d)
    grouped = group_classified_by_chain(pseudo)
    chains = []
    for chain_id, docs in grouped:
        chains.append(
            {
                "chain_id": chain_id,
                "doc_count": len(docs),
                "file_names": [str(x.get("file_name") or "") for x in docs],
                "doc_types": [str(x.get("doc_type") or "other") for x in docs],
                "pending_only": all(
                    any(
                        str(p.get("file_name") or "") == str(x.get("file_name") or "")
                        for p in (job.get("pending_files") or [])
                    )
                    for x in docs
                ),
            }
        )
    return {
        "chains": chains,
        "total_files": len(pseudo),
        "gospd_mode": is_gospd_mode(job),
    }


_ocr_threads: dict[str, threading.Thread] = {}


def _run_ocr_background(
    job_id: str,
    specs: list[dict[str, Any]],
    *,
    force: bool,
    field_plan: dict[str, Any],
    existing: list[dict[str, Any]],
    ledger_rows: Any,
    ledger_mapping: Any,
) -> None:
    total = len(specs)

    def on_progress(done: int, _total: int, fname: str) -> None:
        JOB_STORE.update(
            job_id,
            touch=False,
            ocr_processing_message=f"正在识别 ({done}/{total})：{fname}",
            ocr_progress={"done": done, "total": total, "file": fname},
        )

    try:
        classified = process_uploaded_files(
            job_id,
            specs,
            existing=existing,
            force=force,
            field_plan=field_plan,
            progress_callback=on_progress,
        )
        if ledger_rows and ledger_mapping:
            classified = apply_ledger_to_classified_list(
                classified,
                ledger_rows,
                ledger_mapping,
            )
        issues = collect_ocr_issues(classified)
        JOB_STORE.set_classified(job_id, classified)
        # 先结束 OCR busy，再异步分流/审阅，避免前端长时间假死在「识别中」
        JOB_STORE.update(
            job_id,
            ocr_issues=issues,
            ocr_processing=False,
            ocr_processing_message="识别完成，正在后台自动分流与审阅…",
            ocr_progress=None,
            pending_files=[],
            auto_review_processing=True,
        )
        try:
            from src.workflow.sample_desk import finish_after_classify

            finish_after_classify(job_id)
        except Exception:
            pass
        JOB_STORE.update(
            job_id,
            auto_review_processing=False,
            ocr_processing_message=None,
            pending_files=[],
        )
    except Exception as exc:
        JOB_STORE.update(
            job_id,
            ocr_processing=False,
            auto_review_processing=False,
            ocr_processing_message=f"识别失败：{exc}",
            ocr_progress=None,
        )
    finally:
        _ocr_threads.pop(job_id, None)


@router.post("/jobs/{job_id}/process")
def process_pending(
    job_id: str,
    force: bool = False,
) -> dict[str, Any]:
    from src.workflow.field_catalog import auto_confirm_field_plan

    job = _job_or_404(job_id)
    if job.get("ocr_processing"):
        raise HTTPException(
            status_code=409,
            detail="识别仍在进行中，请稍候（可切换菜单，处理不会中断）",
        )
    pending = list(job.get("pending_files") or [])
    if not pending and not force:
        if job.get("classified"):
            return job
        raise HTTPException(status_code=400, detail="没有待处理文件")

    field_plan = auto_confirm_field_plan(job.get("field_plan"))
    if pending:
        job = JOB_STORE.update(job_id, field_plan=field_plan)

    if pending:
        from src.workflow.packet_engine import (
            annotate_pending_kinds,
            packet_blocks_process,
            packet_status,
        )

        pending = annotate_pending_kinds(pending)
        job = JOB_STORE.update(job_id, pending_files=pending)
        if packet_blocks_process(job):
            st = packet_status(job)
            need_analyze = st in {"idle", "pending_analyze", "analyzing", ""} or not (
                job.get("packet_units") or []
            )
            if need_analyze:
                analyzed = _execute_packet_analyze(job_id, use_vlm=True)
                if analyzed is None:
                    raise HTTPException(
                        status_code=409,
                        detail={"message": "拆包分析进行中", "job": _job_or_404(job_id)},
                    )
                job = analyzed
            if packet_blocks_process(job):
                raise HTTPException(
                    status_code=409,
                    detail={"message": "请先完成拆包分笔", "job": job},
                )
            pending = list(job.get("pending_files") or [])

    specs: list[dict[str, Any]] = []
    for item in pending:
        path = Path(str(item.get("path") or ""))
        if not path.is_file():
            continue
        spec = {
            "filename": item.get("file_name") or path.name,
            "content": path.read_bytes(),
            "slot_hint": item.get("slot_hint") or "",
            "doc_type": item.get("doc_type") or "",
        }
        if isinstance(item.get("source_packet"), dict):
            spec["source_packet"] = item["source_packet"]
        keys = item.get("packet_keys") or (item.get("source_packet") or {}).get("keys")
        if isinstance(keys, dict):
            spec["keys"] = dict(keys)
        specs.append(spec)
    if not specs and not (job.get("classified") and force):
        raise HTTPException(status_code=400, detail="待处理文件不存在或已丢失")

    if force and job.get("classified"):
        for doc in job["classified"]:
            p = Path(str(doc.get("path") or ""))
            if p.is_file():
                specs.append(
                    {
                        "filename": doc.get("file_name") or p.name,
                        "content": p.read_bytes(),
                        "slot_hint": doc.get("upload_slot") or "",
                        "doc_type": doc.get("doc_type") or "",
                    }
                )

    JOB_STORE.update(
        job_id,
        ocr_processing=True,
        ocr_processing_message=f"排队识别 {len(specs)} 个文件…",
        ocr_progress={"done": 0, "total": len(specs), "file": ""},
    )
    thread = threading.Thread(
        target=_run_ocr_background,
        kwargs={
            "job_id": job_id,
            "specs": specs,
            "force": force,
            "field_plan": field_plan,
            "existing": [] if force else (job.get("classified") or []),
            "ledger_rows": job.get("ledger_rows"),
            "ledger_mapping": job.get("ledger_mapping"),
        },
        daemon=True,
        name=f"ocr-{job_id}",
    )
    _ocr_threads[job_id] = thread
    thread.start()
    return JOB_STORE.get(job_id) or job


@router.get("/jobs/{job_id}/documents/{file_name}/file")
def download_document(job_id: str, file_name: str) -> FileResponse:
    job = _job_or_404(job_id)
    doc = _find_document(job, file_name)
    path = Path(str(doc.get("path") or ""))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或路径无效")
    # inline：工作台 iframe 预览；attachment 会导致浏览器空白/下载
    return FileResponse(
        path,
        filename=Path(file_name).name,
        content_disposition_type="inline",
    )


@router.get("/jobs/{job_id}/documents/{file_name}/highlight")
def document_highlight(
    job_id: str,
    file_name: str,
    field: Optional[str] = None,
    value: Optional[str] = None,
) -> Response:
    job = _job_or_404(job_id)
    doc = _find_document(job, file_name)
    from src.image_preprocess.preview_path import resolve_document_image_path

    path = str(resolve_document_image_path(doc))
    if not path or not Path(path).is_file():
        raise HTTPException(
            status_code=404,
            detail="原件路径无效（请重新上传或重新载入演示数据）",
        )
    fields = dict(doc.get("fields") or {})
    # 前端草稿值优先：未保存编辑也能按当前输入定位
    if field and value is not None and str(value).strip():
        fields[field] = str(value).strip()
    elif field:
        # 金额采纳后 accepted 常是裸数字；优先用 meta.highlight_text / raw_value 定位原件
        meta = doc.get("_field_meta") if isinstance(doc.get("_field_meta"), dict) else {}
        slot = meta.get(field) if isinstance(meta, dict) else None
        if isinstance(slot, dict):
            for k in ("highlight_text", "raw_value"):
                ht = slot.get(k)
                if ht is not None and str(ht).strip():
                    fields[field] = str(ht).strip()
                    break
    png, note = highlight_preview(
        path,
        fields,
        selected_key=field,
        text_blocks=doc.get("text_blocks") or [],
    )
    if png is None:
        raise HTTPException(status_code=404, detail=note or "无法生成高亮预览")
    headers = {"Cache-Control": "no-store"}
    if note:
        # latin-1 header；中文用 url 编码
        from urllib.parse import quote

        headers["X-Highlight-Note"] = quote(note, safe="")
    return Response(content=png, media_type="image/png", headers=headers)


class CaptureTextBody(BaseModel):
    page_index: int = 0
    x0: float
    y0: float
    x1: float
    y1: float
    field: Optional[str] = None


@router.get("/jobs/{job_id}/documents/{file_name}/preview-page")
def document_preview_page(
    job_id: str,
    file_name: str,
    page: int = 0,
) -> Response:
    """取证模式用：渲染单页 PNG（可交互框选）。"""
    from urllib.parse import quote

    from src.image_preprocess.preview_path import resolve_document_image_path
    from src.ui.preview_capture import render_preview_page

    job = _job_or_404(job_id)
    doc = _find_document(job, file_name)
    path = resolve_document_image_path(doc)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="原件路径无效")
    try:
        png, meta = render_preview_page(path, page_index=page)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"预览渲染失败：{exc}") from exc
    headers = {
        "Cache-Control": "no-store",
        "X-Page-Index": str(meta.get("page_index", 0)),
        "X-Page-Count": str(meta.get("page_count", 1)),
        "X-Pdf-Width": str(meta.get("pdf_width", "")),
        "X-Pdf-Height": str(meta.get("pdf_height", "")),
        "X-Image-Width": str(meta.get("image_width", "")),
        "X-Image-Height": str(meta.get("image_height", "")),
        "X-Preview-Kind": quote(str(meta.get("kind") or ""), safe=""),
    }
    return Response(content=png, media_type="image/png", headers=headers)


@router.get("/jobs/{job_id}/documents/{file_name}/text-blocks")
def document_text_blocks(
    job_id: str,
    file_name: str,
    page: int = 0,
) -> dict[str, Any]:
    from src.image_preprocess.preview_path import resolve_document_image_path
    from src.ui.preview_capture import list_page_text_blocks

    job = _job_or_404(job_id)
    doc = _find_document(job, file_name)
    path = resolve_document_image_path(doc)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="原件路径无效")
    try:
        return list_page_text_blocks(
            path,
            page_index=page,
            text_blocks=list(doc.get("text_blocks") or []),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/documents/{file_name}/capture-text")
def document_capture_text(
    job_id: str,
    file_name: str,
    body: CaptureTextBody,
) -> dict[str, Any]:
    """拖框取字：坐标为页内归一化 0~1（左上原点）。"""
    from src.image_preprocess.preview_path import resolve_document_image_path
    from src.ui.preview_capture import capture_text_in_rect

    job = _job_or_404(job_id)
    doc = _find_document(job, file_name)
    path = resolve_document_image_path(doc)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="原件路径无效")
    try:
        out = capture_text_in_rect(
            path,
            page_index=int(body.page_index or 0),
            x0=float(body.x0),
            y0=float(body.y0),
            x1=float(body.x1),
            y1=float(body.y1),
            text_blocks=list(doc.get("text_blocks") or []),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    out["field"] = body.field
    out["file_name"] = file_name
    return out


@router.post("/jobs/{job_id}/ledger")
async def upload_ledger(
    job_id: str,
    ledger: UploadFile = File(...),
) -> dict[str, Any]:
    job = _job_or_404(job_id)
    if job.get("sample_population") and job.get("ledger_rows"):
        raise HTTPException(
            status_code=400,
            detail="序时账已在目标页随裁剪账导入，上传页不必再传",
        )
    from src.legacy_ocr.ledger_parser import load_ledger_file, resolve_ledger_column_mapping

    raw = await ledger.read()
    workdir = job_workdir(job_id)
    path = workdir / (ledger.filename or "ledger.xlsx")
    path.write_bytes(raw)
    df = load_ledger_file(raw, filename=ledger.filename or path.name)
    mapping, standard_map, auto_ok = resolve_ledger_column_mapping(list(df.columns))
    rows = df.where(df.notnull(), None).to_dict(orient="records")
    # 始终写入建议映射，供前端预览/手调；仅 auto_ok 时自动套到已识别单据
    patch: dict[str, Any] = {
        "ledger_path": str(path),
        "ledger_rows": rows,
        "ledger_columns": [str(c) for c in df.columns],
        "ledger_mapping": mapping,
        "ledger_auto_ok": bool(auto_ok),
        "ledger_standard_map": standard_map,
    }
    job = JOB_STORE.update(job_id, **patch)
    if job.get("classified") and auto_ok and mapping:
        classified = apply_ledger_to_classified_list(
            job["classified"],
            rows,
            mapping,
        )
        job = JOB_STORE.update(job_id, classified=classified)
        JOB_STORE.invalidate_downstream_from_fields(job_id)
        job = JOB_STORE.get(job_id) or job
    return job


@router.post("/jobs/{job_id}/ledger/apply")
def apply_ledger(job_id: str, body: LedgerApplyBody | None = None) -> dict[str, Any]:
    job = _job_or_404(job_id)
    body = body or LedgerApplyBody()
    if not job.get("ledger_rows"):
        raise HTTPException(status_code=400, detail="请先上传序时账")
    mapping = body.mapping or job.get("ledger_mapping")
    if not mapping:
        raise HTTPException(status_code=400, detail="请提供列映射 mapping")
    classified = apply_ledger_to_classified_list(
        list(job.get("classified") or []),
        job["ledger_rows"],
        mapping,
    )
    JOB_STORE.invalidate_downstream_from_fields(job_id)
    return JOB_STORE.update(
        job_id,
        classified=classified,
        ledger_mapping=mapping,
        ledger_auto_ok=True,
    )


@router.get("/jobs/{job_id}/ledger/options")
def get_ledger_options(job_id: str) -> dict[str, Any]:
    job = _job_or_404(job_id)
    from src.workflow.workbench_extras import ledger_row_options_from_job

    options = ledger_row_options_from_job(job)
    return {"options": options, "count": len(options)}


@router.post("/jobs/{job_id}/ledger/manual-match")
def post_manual_ledger_match(job_id: str, body: ManualLedgerMatchBody) -> dict[str, Any]:
    job = _job_or_404(job_id)
    from src.workflow.workbench_extras import (
        apply_manual_ledger_match,
        ledger_row_options_from_job,
    )

    options = ledger_row_options_from_job(job)
    if not options:
        raise HTTPException(status_code=400, detail="无可用序时账行（请先完成列映射）")
    opt: dict[str, Any] | None = None
    if body.label:
        opt = next((o for o in options if o.get("label") == body.label), None)
    elif body.row_idx is not None:
        opt = next((o for o in options if o.get("row_idx") == body.row_idx), None)
        if opt is None and 0 <= body.row_idx < len(options):
            opt = options[body.row_idx]
    if not opt:
        raise HTTPException(status_code=400, detail="未找到对应序时账行")
    try:
        classified = apply_manual_ledger_match(
            list(job.get("classified") or []),
            file_name=body.file_name,
            option=opt,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    JOB_STORE.invalidate_downstream_from_fields(job_id)
    job = JOB_STORE.update(job_id, classified=classified)
    append_hitl_event(
        action="manual_ledger_match",
        entity_type="document",
        entity_id=body.file_name,
        before=None,
        after=opt,
        reason="工作台人工指定序时账行",
        extra={"job_id": job_id},
    )
    return job


@router.post("/jobs/{job_id}/documents/reclassify")
def post_reclassify(job_id: str, body: ReclassifyBody) -> dict[str, Any]:
    job = _job_or_404(job_id)
    from src.workflow.workbench_extras import reclassify_document

    classified = list(job.get("classified") or [])
    found = False
    for i, item in enumerate(classified):
        if str(item.get("file_name") or "") == body.file_name:
            classified[i] = reclassify_document(item, body.doc_type)
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail=f"document not found: {body.file_name}")
    # 改类型后若有账，重新自动套账
    if job.get("ledger_rows") and job.get("ledger_mapping"):
        classified = apply_ledger_to_classified_list(
            classified,
            job["ledger_rows"],
            job["ledger_mapping"],
        )
    JOB_STORE.invalidate_downstream_from_fields(job_id)
    job = JOB_STORE.update(job_id, classified=classified)
    append_hitl_event(
        action="reclassify_document",
        entity_type="document",
        entity_id=body.file_name,
        before=None,
        after={"doc_type": body.doc_type},
        reason="工作台改类型重抽",
        extra={"job_id": job_id},
    )
    return job


@router.post("/jobs/{job_id}/evidence-match")
def post_evidence_match(job_id: str) -> dict[str, Any]:
    try:
        JOB_STORE.require_step(job_id, "evidence_match")
        _require_fields(job_id)
        job = JOB_STORE.seed_evidence_match(job_id, with_llm_disambiguation=False)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except ValueError as exc:
        raise _http_from_value(exc) from exc

    evidence = job.get("evidence") if isinstance(job.get("evidence"), dict) else {}
    append_hitl_event(
        action="run_evidence_match",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after={
            "status": evidence.get("status"),
            "chain_id": job.get("active_chain_id"),
        },
        reason="工作台运行证据匹配",
    )
    return job


@router.post("/jobs/{job_id}/amount-test")
def post_amount_test(job_id: str) -> dict[str, Any]:
    try:
        JOB_STORE.require_step(job_id, "amount_test")
        job = _require_matching_for_gate4(job_id)
        classified, chain_id = _gospd_docs_and_chain(job)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except ValueError as exc:
        raise _http_from_value(exc) from exc

    result = run_amount(
        classified,
        existing_advisory=list(job.get("advisory_candidates") or []),
    )
    adv_patch: dict[str, Any] = {}
    if "advisory_candidates" in result:
        adv_patch["advisory_candidates"] = result.get("advisory_candidates") or []
        # classified 可能已被 set_candidate 写入三值
        adv_patch["classified"] = classified
    if chain_id:
        job = JOB_STORE.save_chain_sample(job_id, chain_id, {"amount_test": result})
        if adv_patch:
            job = JOB_STORE.update(job_id, **adv_patch)
    else:
        job = JOB_STORE.update(
            job_id,
            amount_test=result,
            conclusion_confirmed=False,
            conclusion_confirm_sig=None,
            finding_acknowledgements={},
            **adv_patch,
        )
    append_hitl_event(
        action="run_amount_test",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after={"status": result.get("status"), "chain_id": chain_id},
        reason="工作台运行金额测试",
    )
    from src.workflow.sample_desk import auto_confirm_passing_conclusions

    return auto_confirm_passing_conclusions(job_id)


@router.post("/jobs/{job_id}/contract-terms")
def post_contract_terms(job_id: str) -> dict[str, Any]:
    try:
        JOB_STORE.require_step(job_id, "contract_terms")
        job = _require_fields(job_id)
        classified, chain_id = _gospd_docs_and_chain(job)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except ValueError as exc:
        raise _http_from_value(exc) from exc

    result = run_contract(
        classified,
        existing_advisory=list(job.get("advisory_candidates") or []),
    )
    adv_patch: dict[str, Any] = {}
    if "advisory_candidates" in result:
        adv_patch["advisory_candidates"] = result.get("advisory_candidates") or []
    if chain_id:
        job = JOB_STORE.save_chain_sample(job_id, chain_id, {"contract_terms": result})
        if adv_patch:
            job = JOB_STORE.update(job_id, **adv_patch)
    else:
        job = JOB_STORE.update(
            job_id,
            contract_terms=result,
            conclusion_confirmed=False,
            conclusion_confirm_sig=None,
            finding_acknowledgements={},
            **adv_patch,
        )
    append_hitl_event(
        action="run_contract_terms",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after={"status": result.get("status"), "chain_id": chain_id},
        reason="工作台运行合同条款测试",
    )
    from src.workflow.sample_desk import auto_confirm_passing_conclusions

    return auto_confirm_passing_conclusions(job_id)


@router.post("/jobs/{job_id}/three-way-cutoff")
def post_three_way_cutoff(job_id: str, body: ThreeWayCutoffBody | None = None) -> dict[str, Any]:
    body = body or ThreeWayCutoffBody()
    try:
        JOB_STORE.require_step(job_id, "three_way_cutoff")
        job = _require_matching_for_gate4(job_id)
        classified, chain_id = _gospd_docs_and_chain(job)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except ValueError as exc:
        raise _http_from_value(exc, job=JOB_STORE.get(job_id)) from exc

    manual = body.manual or job.get("manual_three_way") or {}
    # receipt_idx 相对当前链单据列表
    result = run_three_way(
        classified,
        manual=manual,
        selected_receipt_idx=body.receipt_idx,
        period_end=job.get("period_end"),
        calendar_mode=job.get("calendar_mode"),
        fiscal_year_start=job.get("fiscal_year_start"),
    )
    if chain_id:
        job = JOB_STORE.save_chain_sample(
            job_id,
            chain_id,
            three_way_sample_patch(result, manual),
        )
        job = JOB_STORE.update(job_id, manual_three_way=manual)
    else:
        patch = three_way_sample_patch(result, manual)
        patch.update(
            {
                "conclusion_confirmed": False,
                "conclusion_confirm_sig": None,
                "finding_acknowledgements": {},
            }
        )
        job = JOB_STORE.update(job_id, **patch)
    append_hitl_event(
        action="run_three_way_cutoff",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after={"overall_status": result.get("overall_status"), "chain_id": chain_id},
        reason="工作台运行三单+截止",
    )
    from src.workflow.sample_desk import auto_confirm_passing_conclusions

    return auto_confirm_passing_conclusions(job_id)

@router.post("/jobs/{job_id}/disambiguation/adopt")
def adopt_disambiguation(job_id: str, body: DisambiguationAdoptBody) -> dict[str, Any]:
    job = _job_or_404(job_id)

    # 优先走统一顾问决议链（含定向失效 + 可安全复跑）
    if body.candidate_id:
        from src.audit.gap_fill_replay import apply_advisory_decision

        try:
            out = apply_advisory_decision(
                job_id,
                body.candidate_id,
                "VERIFIED",
                reason="采纳消歧建议",
                auto_replay=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return out["job"] or _job_or_404(job_id)

    classified = apply_disambiguation_proposal(
        list(job.get("classified") or []),
        body.proposal,
    )
    active = [x for x in classified if not x.get("excluded_from_match")] or classified
    evidence = run_evidence(
        active,
        existing_advisory=list(job.get("advisory_candidates") or []),
        with_llm_disambiguation=False,
    )
    phase2 = seed_phase2(classified, evidence, existing_relations=[])
    JOB_STORE.invalidate_downstream_from_fields(job_id)
    # 同步：同文件 PROPOSED 消歧候选标为 VERIFIED（不再二次复跑）
    fname = str(body.proposal.get("file_name") or "")
    adv = list(evidence.get("advisory_candidates") or job.get("advisory_candidates") or [])
    for row in adv:
        if (
            str(row.get("task_type") or "").upper() == "MATCHING_DISAMBIGUATION"
            and str(row.get("status") or "").upper() == "PROPOSED"
            and str((row.get("payload") or {}).get("file_name") or "") == fname
        ):
            row["status"] = "VERIFIED"
            row["actor"] = "manual"
            row["note"] = "adopt_disambiguation"
    evidence = {**evidence, "advisory_candidates": adv}

    chain_id = resolve_active_chain_id(
        {**job, "classified": classified},
    )
    sample_patch = {
        "evidence": evidence,
        "relations": phase2["relations"],
        "duplicates": phase2["duplicates"],
        "matching_confirmed": False,
        "matching_confirm_sig": None,
        "amount_test": None,
        "contract_terms": None,
        "three_way": None,
        "three_way_match": None,
        "cutoff_test": None,
    }
    if chain_id:
        job = JOB_STORE.save_chain_sample(job_id, chain_id, sample_patch)
        job = JOB_STORE.update(
            job_id,
            classified=classified,
            advisory_candidates=adv,
            fields_confirmed=False,
            fields_confirm_sig=None,
        )
    else:
        job = JOB_STORE.update(
            job_id,
            classified=classified,
            evidence=evidence,
            relations=phase2["relations"],
            duplicates=phase2["duplicates"],
            advisory_candidates=adv,
        )
    append_hitl_event(
        action="adopt_matching_proposal",
        entity_type="matching",
        entity_id=str(body.proposal.get("file_name") or ""),
        before=None,
        after=body.proposal,
        reason=str(body.proposal.get("reason") or "采纳消歧建议"),
        extra={"job_id": job_id, "chain_id": chain_id},
    )
    return job


@router.get("/jobs/{job_id}/relations")
def get_relations(job_id: str) -> dict[str, Any]:
    job = _job_or_404(job_id)
    return {
        "relations": job.get("relations") or [],
        "duplicates": job.get("duplicates") or {},
    }


@router.post("/jobs/{job_id}/duplicates/acknowledge")
def acknowledge_duplicates(
    job_id: str, body: DuplicateOverrideBody | None = None
) -> dict[str, Any]:
    """审计师知悉重复票号等强信号后放行 Gate4（写入旁注，不删除 findings）。"""
    from datetime import datetime, timezone

    body = body or DuplicateOverrideBody()
    job = _job_or_404(job_id)
    reason = str(body.reason or "").strip() or "审计师知悉并放行"
    dup = dict(job.get("duplicates") or {})
    if not dup.get("blocks_downstream_hint"):
        return job
    dup["blocks_downstream_hint"] = False
    dup["auditor_override"] = {
        "acknowledged": True,
        "reason": reason,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    chain_id = resolve_active_chain_id(job) if is_gospd_mode(job) else None
    if chain_id:
        job = JOB_STORE.save_chain_sample(
            job_id,
            chain_id,
            {
                "duplicates": dup,
                "matching_confirmed": False,
                "matching_confirm_sig": None,
            },
        )
    else:
        job = JOB_STORE.update(
            job_id,
            duplicates=dup,
            matching_confirmed=False,
            matching_confirm_sig=None,
            conclusion_confirmed=False,
            conclusion_confirm_sig=None,
        )
    append_hitl_event(
        action="acknowledge_duplicate_risk",
        entity_type="duplicates",
        entity_id=job_id,
        before={"blocks_downstream_hint": True},
        after={"blocks_downstream_hint": False, "reason": reason},
        reason=reason,
    )
    return job


@router.get("/jobs/{job_id}/advisory")
def get_advisory_candidates(job_id: str) -> dict[str, Any]:
    from src.audit.gap_fill_orchestrator import queue_snapshot

    job = _job_or_404(job_id)
    snap = queue_snapshot(job)
    return {
        "counts": snap["counts"],
        "pending": snap["pending"],
        "candidates": snap["store"],
    }


class BatchReviewBody(BaseModel):
    force_rerun: bool = False


@router.post("/jobs/{job_id}/batch-review")
def post_batch_review(job_id: str, body: BatchReviewBody | None = None) -> dict[str, Any]:
    """工作台一键审阅：全笔自动匹配 + 可自动勾稽 + 必测。"""
    from src.workflow.batch_review import run_batch_review

    body = body or BatchReviewBody()
    _job_or_404(job_id)
    try:
        out = run_batch_review(job_id, force_rerun=bool(body.force_rerun))
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except ValueError as exc:
        raise _http_from_value(exc, job=JOB_STORE.get(job_id)) from exc
    append_hitl_event(
        action="batch_review",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after={
            "ran": len(out.get("ran") or []),
            "skipped": len(out.get("skipped") or []),
            "failed": len(out.get("failed") or []),
            "need_gate4": out.get("need_gate4") or [],
        },
        reason=out.get("summary") or "一键审阅",
    )
    from src.workflow.sample_desk import auto_confirm_passing_conclusions

    out["job"] = auto_confirm_passing_conclusions(job_id)
    return out


@router.post("/jobs/{job_id}/hitl/matching/confirm-all")
def post_confirm_matching_all(job_id: str) -> dict[str, Any]:
    """一键确认全部可确认业务笔的串单勾稽。"""
    from src.workflow.batch_review import batch_confirm_matching

    _job_or_404(job_id)
    try:
        out = batch_confirm_matching(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在") from None
    except ValueError as exc:
        raise _http_from_value(exc, job=JOB_STORE.get(job_id)) from exc
    append_hitl_event(
        action="confirm_matching_all",
        entity_type="job",
        entity_id=job_id,
        before=None,
        after={
            "confirmed": out.get("confirmed") or [],
            "blocked": out.get("blocked") or [],
        },
        reason=out.get("summary") or "一键串单确认",
    )
    return out


@router.post("/jobs/{job_id}/advisory/{candidate_id}/decide")
def decide_advisory_candidate(
    job_id: str,
    candidate_id: str,
    body: AdvisoryDecideBody,
) -> dict[str, Any]:
    from src.audit.gap_fill_replay import apply_advisory_decision

    _job_or_404(job_id)
    try:
        out = apply_advisory_decision(
            job_id,
            candidate_id,
            body.status,
            reason=body.reason,
            auto_replay=bool(body.auto_replay),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "job": out.get("job"),
        "before": out.get("before"),
        "after": out.get("after"),
        "invalidates": out.get("invalidates") or [],
        "expanded_invalidates": out.get("expanded_invalidates") or [],
        "side_effects": out.get("side_effects") or {},
        "replayed": out.get("replayed") or [],
        "skipped": out.get("skipped") or [],
    }


@router.post("/jobs/{job_id}/relations/verify-all")
def verify_all_relations(
    job_id: str, body: ConfirmReasonBody | None = None
) -> dict[str, Any]:
    """一键确认全部待决关系（PROPOSED → VERIFIED）。"""
    body = body or ConfirmReasonBody()
    job = _job_or_404(job_id)
    relations = list(job.get("relations") or [])
    pending = [
        r
        for r in relations
        if isinstance(r, dict) and str(r.get("status") or "PROPOSED").upper() == "PROPOSED"
    ]
    if not pending:
        return job
    reason = body.reason or "全部确认相关"
    updated = relations
    count = 0
    for rel in pending:
        rid = str(rel.get("relation_id") or "")
        if not rid:
            continue
        try:
            updated, before, after = decide_relation(
                updated, rid, "VERIFIED", reason=reason
            )
        except ValueError:
            continue
        count += 1
        append_hitl_event(
            action="verify_relation",
            entity_type="relation",
            entity_id=rid,
            before=before,
            after=after,
            reason=reason,
            extra={"job_id": job_id, "bulk": True},
        )
    JOB_STORE.invalidate_downstream_from_evidence(job_id)
    job = JOB_STORE.update(job_id, relations=updated)
    append_hitl_event(
        action="verify_all_relations",
        entity_type="job",
        entity_id=job_id,
        before={"pending": len(pending)},
        after={"verified": count},
        reason=reason,
    )
    return job


@router.post("/jobs/{job_id}/relations/{relation_id}/decide")
def decide_relation_endpoint(
    job_id: str,
    relation_id: str,
    body: RelationDecideBody,
) -> dict[str, Any]:
    job = _job_or_404(job_id)
    relations = list(job.get("relations") or [])
    try:
        updated, before, after = decide_relation(
            relations,
            relation_id,
            body.status,
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if before is None:
        raise HTTPException(status_code=404, detail="关系不存在")
    # 关系变更会使 Gate4 签名失效，须清空下游确认与测试缓存
    JOB_STORE.invalidate_downstream_from_evidence(job_id)
    job = JOB_STORE.update(job_id, relations=updated)
    append_hitl_event(
        action="decide_relation",
        entity_type="relation",
        entity_id=relation_id,
        before=before,
        after=after,
        reason=body.reason or body.status,
        extra={"job_id": job_id},
    )
    return job


@router.post("/jobs/{job_id}/hitl/matching/confirm")
def confirm_matching_gate4(job_id: str, body: ConfirmReasonBody | None = None) -> dict[str, Any]:
    body = body or ConfirmReasonBody()
    if not JOB_STORE.get(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        job = JOB_STORE.confirm_matching(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    append_hitl_event(
        action="confirm_matching_gate4",
        entity_type="matching",
        entity_id="gate4",
        before={"confirmed": False},
        after={"confirmed": True, "sig": job.get("matching_confirm_sig")},
        reason=body.reason or "工作台确认 Gate4",
    )
    return job


@router.post("/jobs/{job_id}/hitl/conclusion/confirm")
def confirm_conclusion_gate5(job_id: str, body: ConfirmReasonBody | None = None) -> dict[str, Any]:
    body = body or ConfirmReasonBody()
    if not JOB_STORE.get(job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        job = JOB_STORE.confirm_conclusion(job_id, as_fail=bool(body.as_fail))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    append_hitl_event(
        action="confirm_conclusion_gate5",
        entity_type="job",
        entity_id=job_id,
        before={"confirmed": False},
        after={"confirmed": True, "sig": job.get("conclusion_confirm_sig"), "as_fail": body.as_fail},
        reason=body.reason or ("确认测试不通过" if body.as_fail else "工作台确认 Gate5"),
    )
    return job


@router.get("/jobs/{job_id}/conclusion-trace")
def get_conclusion_trace(
    job_id: str,
    chain_id: Optional[str] = None,
) -> dict[str, Any]:
    """Gate5：失败结论追溯（用了哪些字段、怎么测）。

    可选 chain_id：只扫该笔（结论页加速）；省略则扫全部已测链（兼容旧调用）。
    """
    job = _job_or_404(job_id)
    from src.workflow.conclusion_trace import build_conclusion_trace

    scope = str(chain_id or "").strip() or None
    return build_conclusion_trace(job, chain_id=scope)


@router.post("/jobs/{job_id}/hitl/finding/acknowledge")
def acknowledge_finding_hitl(job_id: str, body: FindingAcknowledgeBody) -> dict[str, Any]:
    """Gate5：将某项不通过结论确认为单据问题（或撤销）。"""
    job = _job_or_404(job_id)
    fid = str(body.finding_id or "").strip()
    if not fid:
        raise HTTPException(status_code=400, detail="缺少 finding_id")
    from src.workflow.conclusion_trace import acknowledge_finding, build_conclusion_trace

    root = acknowledge_finding(
        job,
        finding_id=fid,
        genuine=bool(body.genuine),
        reason=str(body.reason or ""),
    )
    # 改确认 → Gate5 确认失效，须按最新 ack 再确认
    updated = JOB_STORE.update(
        job_id,
        finding_acknowledgements=root,
        conclusion_confirmed=False,
        conclusion_confirm_sig=None,
        workbook_path=None,
        workbook_paths=[],
    )
    append_hitl_event(
        action="acknowledge_finding",
        entity_type="finding",
        entity_id=fid,
        before={},
        after={"genuine": body.genuine, "reason": body.reason},
        reason="Gate5 确认为单据问题" if body.genuine else "Gate5 撤销单据问题确认",
    )
    trace = build_conclusion_trace(updated)
    return {"job": updated, "trace": trace}


@router.post("/jobs/{job_id}/hitl/finding/acknowledge-batch")
def acknowledge_finding_batch_hitl(
    job_id: str, body: FindingAcknowledgeBatchBody | None = None
) -> dict[str, Any]:
    """Gate5：批量将不通过项确认为单据问题（默认真当前笔）。追溯列表仍可点开复查。"""
    body = body or FindingAcknowledgeBatchBody()
    job = _job_or_404(job_id)
    from src.workflow.chain_workspace import is_gospd_mode, resolve_active_chain_id
    from src.workflow.conclusion_trace import (
        acknowledge_findings_batch,
        build_conclusion_trace,
    )

    scope = str(body.scope or "active").strip().lower()
    chain_id: str | None = str(body.chain_id or "").strip() or None
    if scope == "all":
        chain_id = None
    elif is_gospd_mode(job):
        chain_id = chain_id or resolve_active_chain_id(job)
        if not chain_id:
            raise HTTPException(status_code=400, detail="请先选择业务笔")

    root, touched = acknowledge_findings_batch(
        job,
        chain_id=chain_id,
        genuine=bool(body.genuine),
        reason=str(body.reason or ""),
    )
    updated = JOB_STORE.update(
        job_id,
        finding_acknowledgements=root,
        conclusion_confirmed=False,
        conclusion_confirm_sig=None,
        workbook_path=None,
        workbook_paths=[],
    )
    append_hitl_event(
        action="acknowledge_finding_batch",
        entity_type="finding_batch",
        entity_id=chain_id or "all",
        before={},
        after={
            "count": len(touched),
            "finding_ids": touched,
            "genuine": body.genuine,
            "reason": body.reason,
        },
        reason=(
            "Gate5 批量确认为单据问题"
            if body.genuine
            else "Gate5 批量撤销单据问题确认"
        ),
    )
    trace = build_conclusion_trace(updated)
    return {
        "job": updated,
        "trace": trace,
        "acknowledged_finding_ids": touched,
        "message": (
            f"已批量确认 {len(touched)} 项"
            if body.genuine
            else f"已批量撤销 {len(touched)} 项"
        ),
    }


@router.post("/jobs/{job_id}/hitl/chain-linkage/confirm")
def confirm_chain_linkage_hitl(
    job_id: str, body: ChainLinkageBody | None = None
) -> dict[str, Any]:
    """本笔人工核对：字段确认 +（默认）自动匹配 + 采纳建议关系 + Gate4。"""
    body = body or ChainLinkageBody()
    _job_or_404(job_id)
    try:
        out = JOB_STORE.confirm_chain_linkage(
            job_id,
            auto_evidence=bool(body.auto_evidence),
            auto_accept_relations=bool(body.auto_accept_relations),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = out["job"]
    append_hitl_event(
        action="confirm_chain_linkage",
        entity_type="job",
        entity_id=job_id,
        before={},
        after={
            "fields_confirmed": out.get("fields_confirmed"),
            "matching_confirmed": out.get("matching_confirmed"),
            "next_action": out.get("next_action"),
            "evidence_seeded": out.get("evidence_seeded"),
            "pending_relation_count": out.get("pending_relation_count"),
            "active_chain_id": job.get("active_chain_id"),
        },
        reason=str(out.get("message") or "本笔人工核对"),
    )
    return out


@router.post("/jobs/{job_id}/hitl/chain/release")
def release_active_chain_hitl(
    job_id: str, body: ChainReleaseBody | None = None
) -> dict[str, Any]:
    """本笔放行：批量确认当前笔未处理不通过项 + 写本笔 Gate5。不代确认字段/Gate4。"""
    body = body or ChainReleaseBody()
    _job_or_404(job_id)
    try:
        out = JOB_STORE.release_active_chain(
            job_id,
            reason=str(body.reason or ""),
            ack_unacked=bool(body.ack_unacked),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = out["job"]
    append_hitl_event(
        action="release_active_chain",
        entity_type="chain",
        entity_id=str(job.get("active_chain_id") or job_id),
        before={},
        after={
            "acknowledged_finding_ids": out.get("acknowledged_finding_ids") or [],
            "conclusion_confirmed": job.get("conclusion_confirmed"),
        },
        reason=str(out.get("message") or "本笔放行"),
    )
    return out


@router.post("/jobs/{job_id}/hitl/field-row/verify")
def verify_field_row_hitl(job_id: str, body: FieldRowVerifyBody) -> dict[str, Any]:
    """字段对照表：行级「验证通过」写入 HITL 与 job 状态。"""
    from datetime import datetime, timezone

    job = _job_or_404(job_id)
    field_key = str(body.field_key or "").strip()
    if not field_key:
        raise HTTPException(status_code=400, detail="缺少 field_key")
    chain_id = str(body.chain_id or "").strip() or str(job.get("active_chain_id") or "job")
    root = dict(job.get("field_row_verifications") or {})
    chain_map = dict(root.get(chain_id) or {})
    before = dict(chain_map.get(field_key) or {})
    if body.verified:
        chain_map[field_key] = {
            "verified": True,
            "at": datetime.now(timezone.utc).isoformat(),
            "reason": str(body.reason or ""),
        }
    else:
        chain_map.pop(field_key, None)
    if chain_map:
        root[chain_id] = chain_map
    else:
        root.pop(chain_id, None)
    updated = JOB_STORE.update(job_id, field_row_verifications=root)
    append_hitl_event(
        action="verify_field_row",
        entity_type="field_row",
        entity_id=f"{chain_id}:{field_key}",
        before=before,
        after=chain_map.get(field_key) or {"verified": False},
        reason=str(body.reason or "") or ("字段对照行已人工核对" if body.verified else "撤销字段对照行核对"),
        extra={"job_id": job_id, "chain_id": chain_id, "field_key": field_key},
    )
    return updated


@router.get("/jobs/{job_id}/workbook-rows/preview")
def preview_workbook_rows(job_id: str) -> dict[str, Any]:
    """Gate5：预览将写入底稿的样本行结论（含可编辑列 / 公式只读说明）。"""
    job = _job_or_404(job_id)
    from src.workflow.workbook_row_edits import preview_rows_for_gate5

    return preview_rows_for_gate5(job)


@router.put("/jobs/{job_id}/workbook-rows/edits")
def put_workbook_row_edits(job_id: str, body: WorkbookRowEditBody) -> dict[str, Any]:
    """Gate5：覆写单链业务结论列（禁止改 K/S/T/V 公式列）。"""
    job = _job_or_404(job_id)
    from src.workflow.workbook_row_edits import SCHEMA, upsert_chain_edit

    fmt = (body.format or "").strip().lower()
    if fmt not in SCHEMA:
        raise HTTPException(status_code=400, detail=f"不支持的格式: {fmt}")
    if not str(body.chain_id or "").strip():
        raise HTTPException(status_code=400, detail="缺少 chain_id")
    # 拒绝公式列
    forbidden = {"diff_inv", "diff_amt", "diff_qty", "period_ok", "period_ok_formula", "formula_v"}
    bad = [k for k in (body.edits or {}) if k in forbidden]
    if bad:
        raise HTTPException(status_code=400, detail=f"公式列禁止覆写: {', '.join(bad)}")
    try:
        root = upsert_chain_edit(
            job,
            fmt=fmt,
            chain_id=str(body.chain_id).strip(),
            patch=dict(body.edits or {}),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 改结论 → Gate5 / 导出缓存失效
    updated = JOB_STORE.update(
        job_id,
        workbook_row_edits=root,
        conclusion_confirmed=False,
        conclusion_confirm_sig=None,
        workbook_path=None,
        workbook_paths=[],
    )
    append_hitl_event(
        action="edit_workbook_row_conclusion",
        entity_type="workbook_row",
        entity_id=f"{fmt}:{body.chain_id}",
        before={},
        after={"edits": body.edits},
        reason="Gate5 覆写底稿业务结论列",
    )
    return updated


@router.post("/jobs/{job_id}/interpret")
def post_interpret(job_id: str, body: InterpretBody) -> dict[str, Any]:
    job = _job_or_404(job_id)
    from src.workflow.workbench_extras import interpret_test_result

    family = (body.family or "").strip().lower()
    payload = dict(body.payload or {})
    if not payload:
        if family == "amount":
            payload = dict(job.get("amount_test") or {})
        elif family == "contract":
            payload = dict(job.get("contract_terms") or {})
        else:
            payload = dict(job.get("three_way") or {})
    if not payload:
        raise HTTPException(status_code=400, detail="请先运行对应测试再解读")
    try:
        out = interpret_test_result(family, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    patch: dict[str, Any] = {}
    if family == "amount" and job.get("amount_test"):
        amt = dict(job["amount_test"])
        if isinstance(amt.get("accuracy_report"), dict):
            ar = dict(amt["accuracy_report"])
            ar["llm_interpretation"] = out
            amt["accuracy_report"] = ar
        else:
            amt["llm_interpretation"] = out
        patch["amount_test"] = amt
    elif family == "contract" and job.get("contract_terms"):
        ct = dict(job["contract_terms"])
        ct["llm_interpretation"] = out
        patch["contract_terms"] = ct
    elif family in {"cutoff", "three_way"} and job.get("three_way"):
        tw = dict(job["three_way"])
        tw["llm_interpretation"] = out
        patch["three_way"] = tw
    if patch:
        job = JOB_STORE.update(job_id, **patch)
    return {"interpretation": out, "job": job}


@router.get("/jobs/{job_id}/receipt-choices")
def get_receipt_choices(job_id: str) -> dict[str, Any]:
    job = _job_or_404(job_id)
    from src.workflow.workbench_extras import find_receipt_choices

    return {"choices": find_receipt_choices(list(job.get("classified") or []))}


@router.post("/jobs/{job_id}/workbook/export")
def export_workbook(job_id: str) -> dict[str, Any]:
    from src.workflow.export_readiness import build_export_readiness

    job = _job_or_404(job_id)
    readiness = build_export_readiness(job)
    if not readiness["ready"]:
        blocked = [s for s in readiness["stages"] if s["blocking"]]
        detail = "；".join(f"{s['label']}：{s['reason']}" for s in blocked)
        raise HTTPException(status_code=400, detail=f"底稿尚不可生成。{detail}")
    try:
        paths = build_workbooks_for_job(job)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"底稿生成失败：{exc}") from exc
    if not paths:
        raise HTTPException(status_code=500, detail="未生成任何底稿文件")

    entries: list[dict[str, Any]] = []
    for path in paths:
        name = path.name
        fmt = ""
        upper = name.upper()
        # 须先匹配更长后缀，避免被裸 GOSPD01010 前缀误判
        if upper.startswith("GOSPD01010.4") or upper.startswith("GOSPD01010_4"):
            fmt = "gospd01010_4"
            label = "GOSPD01010.4 价格分摊抽凭"
        elif upper.startswith("GOSPD01010.3") or upper.startswith("GOSPD01010_3"):
            fmt = "gospd01010_3"
            label = "GOSPD01010.3 交易价格抽凭"
        elif upper.startswith("GOSPD01010.2") or upper.startswith("GOSPD01010_2"):
            fmt = "gospd01010_2"
            label = "GOSPD01010.2 履约义务抽凭"
        elif upper.startswith("GOSPD01010"):
            fmt = "gospd01010"
            label = "GOSPD01010 期内收入抽凭"
        elif upper.startswith("GOSPD01030"):
            fmt = "gospd01030"
            label = "GOSPD01030 销售截止（期后）"
        else:
            label = "审阅底稿"
        entries.append(
            {
                "format": fmt or "generic",
                "label": label,
                "path": str(path),
                "file_name": name,
            }
        )
    job = JOB_STORE.update(
        job_id,
        workbook_path=str(paths[0]),
        workbook_paths=entries,
    )
    out = dict(job)
    out["workbook_path"] = str(paths[0])
    out["workbook_paths"] = entries
    return out


@router.get("/jobs/{job_id}/export-readiness")
def get_export_readiness(job_id: str) -> dict[str, Any]:
    """Return the exact workflow prerequisites that currently block export."""
    from src.workflow.export_readiness import build_export_readiness

    return build_export_readiness(_job_or_404(job_id))


@router.get("/mes-match-rules")
def get_mes_match_rules() -> dict[str, Any]:
    """可解释三单/截止规则目录（展示用，不替代引擎）。"""
    from src.workflow.test_diff_summary import list_match_rules, load_match_rule_catalog

    load_match_rule_catalog.cache_clear()
    cat = load_match_rule_catalog()
    return {
        "version": cat.get("version"),
        "purpose": cat.get("purpose"),
        "light_legend": cat.get("light_legend") or {},
        "rules": list_match_rules(),
    }


def _resolve_workbook_path(job: dict[str, Any], format: Optional[str] = None) -> Path:
    entries = list(job.get("workbook_paths") or [])
    fmt = (format or "").strip()
    if fmt and entries:
        for e in entries:
            if str(e.get("format") or "") == fmt:
                path = Path(str(e.get("path") or ""))
                if path.is_file():
                    return path
                raise HTTPException(status_code=404, detail=f"底稿文件不存在: {fmt}")
        raise HTTPException(status_code=404, detail=f"未找到格式 {fmt} 的底稿")
    path = Path(str(job.get("workbook_path") or ""))
    if path.is_file():
        return path
    if entries:
        path = Path(str(entries[0].get("path") or ""))
        if path.is_file():
            return path
    raise HTTPException(status_code=404, detail="尚未导出底稿，请先调用 export")


@router.get("/jobs/{job_id}/workbook/preview")
def preview_workbook(
    job_id: str,
    sheet: Optional[str] = None,
    format: Optional[str] = None,
) -> dict[str, Any]:
    job = _job_or_404(job_id)
    path = _resolve_workbook_path(job, format=format)
    from src.workflow.workbench_extras import workbook_sheet_preview

    try:
        payload = workbook_sheet_preview(path, sheet=sheet)
        payload["workbook_format"] = format or ""
        payload["workbook_file"] = path.name
        return payload
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"预览失败：{exc}") from exc


@router.get("/jobs/{job_id}/workbook/download")
def download_workbook(job_id: str, format: Optional[str] = None) -> FileResponse:
    job = _job_or_404(job_id)
    path = _resolve_workbook_path(job, format=format)
    return FileResponse(path, filename=path.name)
