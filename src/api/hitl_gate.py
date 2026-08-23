"""API 侧可选 HITL 门禁：字段确认 / 匹配确认 / 结论确认。

正式审计：禁止 OCR Mock（AUDIT_ALLOW_OCR_MOCK=0）时，REQUIRE_* 默认自动开启；
显式设为 0/false 可关闭（批测/本地）。开启后优先用 job_id 校验任务内真实确认态；
仅 Header/Query 自证视为兼容旁路，须显式 HITL_TRUST_CLIENT_HEADER=1。
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Header, HTTPException, Query

from config.settings import settings


def _truthy(raw: str) -> bool:
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _falsy(raw: str) -> bool:
    return str(raw).strip().lower() in {"0", "false", "no", "off"}


def allow_ocr_mock() -> bool:
    raw = os.getenv("AUDIT_ALLOW_OCR_MOCK")
    if raw is None or str(raw).strip() == "":
        raw = getattr(settings, "AUDIT_ALLOW_OCR_MOCK", "0") or "0"
    return _truthy(str(raw))


def formal_ocr_mode() -> bool:
    """正式 OCR 口径：禁止 Mock。"""
    return not allow_ocr_mock()


def _resolve_require_flag(name: str) -> bool:
    """解析 REQUIRE_*：1=开，0=关，空/auto=随正式 OCR 口径。"""
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        raw = getattr(settings, name, "auto")
    text = str(raw or "").strip().lower()
    if text in {"", "auto"}:
        return formal_ocr_mode()
    if _falsy(text):
        return False
    return _truthy(text)


def _flag(name: str, default: str = "0") -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        raw = getattr(settings, name, default) or default
    return _truthy(str(raw))


def fields_confirmed_api_required() -> bool:
    return _resolve_require_flag("REQUIRE_FIELDS_CONFIRMED_API")


def matching_confirmed_api_required() -> bool:
    return _resolve_require_flag("REQUIRE_MATCHING_CONFIRMED_API")


def conclusion_confirmed_api_required() -> bool:
    return _resolve_require_flag("REQUIRE_CONCLUSION_CONFIRMED_API")


def trust_client_header() -> bool:
    """是否允许仅凭客户端 Header/Query 自证（不安全，仅兼容旧脚本）。"""
    return _flag("HITL_TRUST_CLIENT_HEADER", "0")


def _header_or_query_ok(header_val: Optional[str], query_val: bool) -> bool:
    header_ok = str(header_val or "").strip().lower() in {"1", "true", "yes", "on"}
    return header_ok or bool(query_val)


def _job_flag(job_id: Optional[str], key: str) -> Optional[bool]:
    """读取任务确认态；job 不存在返回 False；未传 job_id 返回 None。"""
    if not job_id or not str(job_id).strip():
        return None
    try:
        from src.workflow.job_store import JOB_STORE
    except Exception:
        return False
    job = JOB_STORE.get(str(job_id).strip())
    if not job:
        return False
    return bool(job.get(key))


def _enforce(
    *,
    required: bool,
    job_id: Optional[str],
    job_key: str,
    header_val: Optional[str],
    query_val: bool,
    detail: str,
) -> None:
    if not required:
        return
    state = _job_flag(job_id, job_key)
    if state is True:
        return
    if state is False:
        raise HTTPException(
            status_code=403,
            detail=f"任务 {job_id} 尚未完成{job_key}，拒绝执行。",
        )
    # 未传 job_id：仅在显式信任客户端自证时放行
    if trust_client_header() and _header_or_query_ok(header_val, query_val):
        return
    raise HTTPException(status_code=403, detail=detail)


def enforce_fields_confirmed_header(
    x_fields_confirmed: Optional[str] = Header(
        default=None, alias="X-Fields-Confirmed"
    ),
    fields_confirmed: bool = Query(default=False),
    job_id: Optional[str] = Query(default=None, description="优先按任务真实确认态校验"),
) -> None:
    """作为路由 dependency：开启强制时，请求须带确认标志。"""
    _enforce(
        required=fields_confirmed_api_required(),
        job_id=job_id,
        job_key="fields_confirmed",
        header_val=x_fields_confirmed,
        query_val=fields_confirmed,
        detail=(
            "已启用 REQUIRE_FIELDS_CONFIRMED_API："
            "请传 Query job_id=…（服务端校验 fields_confirmed），"
            "或在 HITL_TRUST_CLIENT_HEADER=1 时使用 fields_confirmed=true / "
            "Header X-Fields-Confirmed"
        ),
    )


def enforce_matching_confirmed_header(
    x_matching_confirmed: Optional[str] = Header(
        default=None, alias="X-Matching-Confirmed"
    ),
    matching_confirmed: bool = Query(default=False),
    job_id: Optional[str] = Query(default=None, description="优先按任务真实确认态校验"),
) -> None:
    """Gate4 API 门禁（可选）。"""
    _enforce(
        required=matching_confirmed_api_required(),
        job_id=job_id,
        job_key="matching_confirmed",
        header_val=x_matching_confirmed,
        query_val=matching_confirmed,
        detail=(
            "已启用 REQUIRE_MATCHING_CONFIRMED_API："
            "请传 Query job_id=…（服务端校验 matching_confirmed），"
            "或在 HITL_TRUST_CLIENT_HEADER=1 时使用 matching_confirmed=true / "
            "Header X-Matching-Confirmed"
        ),
    )


def enforce_conclusion_confirmed_header(
    x_conclusion_confirmed: Optional[str] = Header(
        default=None, alias="X-Conclusion-Confirmed"
    ),
    conclusion_confirmed: bool = Query(default=False),
    job_id: Optional[str] = Query(default=None, description="优先按任务真实确认态校验"),
) -> None:
    """Gate5 API 门禁（可选）。"""
    _enforce(
        required=conclusion_confirmed_api_required(),
        job_id=job_id,
        job_key="conclusion_confirmed",
        header_val=x_conclusion_confirmed,
        query_val=conclusion_confirmed,
        detail=(
            "已启用 REQUIRE_CONCLUSION_CONFIRMED_API："
            "请传 Query job_id=…（服务端校验 conclusion_confirmed），"
            "或在 HITL_TRUST_CLIENT_HEADER=1 时使用 conclusion_confirmed=true / "
            "Header X-Conclusion-Confirmed"
        ),
    )
