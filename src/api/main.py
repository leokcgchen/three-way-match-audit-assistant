"""截止性测试 API：接收三单系统 JSON，返回标准化截止性结果，并追加底稿 CSV。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from loguru import logger

from src.api.cutoff_runner import perform_cutoff
from src.api.hitl_gate import enforce_fields_confirmed_header
from src.api.workflow_router import router as workflow_router
from src.models.schemas import CutoffRequest, CutoffResponse
from src.three_way_match.api_router import router as three_way_router
from src.evidence_match.api_router import router as evidence_router
from src.amount_test.api_router import router as amount_router
from src.contract_terms.api_router import router as contract_terms_router

app = FastAPI(
    title="收入抽凭 / 合同合规性审阅工作台",
    description="GOSPD 底稿导向：截止、三单、条款、金额与 HITL；React 工作台为主入口",
    version="0.8.0-baseline",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Highlight-Note", "Content-Type", "Content-Length"],
)
app.include_router(three_way_router)
app.include_router(evidence_router)
app.include_router(amount_router)
app.include_router(contract_terms_router)
app.include_router(workflow_router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    """冷启验收：状态 + 正式审计开关快照（不含密钥）。"""
    from src.api.hitl_gate import (
        conclusion_confirmed_api_required,
        fields_confirmed_api_required,
        matching_confirmed_api_required,
        trust_client_header,
    )
    from config.settings import settings

    allow_mock = str(
        getattr(settings, "AUDIT_ALLOW_OCR_MOCK", "0") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    formal = not allow_mock
    return {
        "status": "ok",
        "module": "workbench",
        "version": app.version,
        "phase": "PHASE-2-BASELINE",
        "ui": "react-workbench",
        "audit": {
            "formal_ocr": formal,
            "allow_ocr_mock": allow_mock,
            "require_fields_confirmed_api": fields_confirmed_api_required(),
            "require_matching_confirmed_api": matching_confirmed_api_required(),
            "require_conclusion_confirmed_api": conclusion_confirmed_api_required(),
            "hitl_trust_client_header": trust_client_header(),
            "field_extract_mode": getattr(settings, "FIELD_EXTRACT_MODE", ""),
            "batch_llm_assist": getattr(settings, "BATCH_LLM_ASSIST", ""),
        },
        "note": (
            "正式审计：AUDIT_ALLOW_OCR_MOCK=0 时 REQUIRE_* 默认 auto 开启；"
            "请用 job_id 校验确认态，勿依赖客户端 Header 自证"
            "（HITL_TRUST_CLIENT_HEADER 仅兼容旧脚本）"
        ),
    }


@app.post(
    "/api/v1/cutoff",
    response_model=CutoffResponse,
    dependencies=[Depends(enforce_fields_confirmed_header)],
)
def run_cutoff(request: CutoffRequest) -> CutoffResponse:
    """接收三单系统 JSON，执行截止性测试并返回结果（默认不写底稿文件）。"""
    try:
        return perform_cutoff(request, write_workbook=False)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("CutoffChecker 执行异常: {}", exc)
        raise HTTPException(status_code=500, detail=f"截止性测试执行失败: {exc}") from exc
