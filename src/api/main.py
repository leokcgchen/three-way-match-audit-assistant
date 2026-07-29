"""截止性测试 API：接收三单系统 JSON，返回标准化截止性结果，并追加底稿 CSV。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from loguru import logger

from src.api.cutoff_runner import perform_cutoff
from src.models.schemas import CutoffRequest, CutoffResponse
from src.three_way_match.api_router import router as three_way_router

app = FastAPI(
    title="合同截止性测试服务",
    description="接收三单系统推送，执行截止性测试并返回标准化结果；支持三单匹配联动",
    version="0.4.0-twm2",
)
app.include_router(three_way_router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "module": "cutoff", "phase": "TWM-2"}


@app.post("/api/v1/cutoff", response_model=CutoffResponse)
def run_cutoff(request: CutoffRequest) -> CutoffResponse:
    """接收三单系统 JSON，执行截止性测试，追加底稿 CSV 并返回结果。"""
    try:
        return perform_cutoff(request, write_workbook=True)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("CutoffChecker 执行异常: {}", exc)
        raise HTTPException(status_code=500, detail=f"截止性测试执行失败: {exc}") from exc
