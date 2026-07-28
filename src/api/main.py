"""截止性测试 API：接收三单系统 JSON，返回标准化截止性结果，并追加底稿 CSV。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from loguru import logger

from config.settings import settings
from src.models.schemas import CutoffRequest, CutoffResponse, CutoffTestResult
from src.reporting.workbook_generator import WorkbookGenerator
from src.rules.cutoff_checker import CutoffChecker
from src.utils.date_extractor import extract_days_from_description

RiskLevel = Literal["无异常", "条款模糊-人工复核", "明确错报-需审计调整"]

STATUS_TO_RISK: dict[str, RiskLevel] = {
    "PASS": "无异常",
    "WARNING": "条款模糊-人工复核",
    "FAIL": "明确错报-需审计调整",
}

STATUS_TO_AUDIT: dict[str, str] = {
    "PASS": "无异常",
    "WARNING": "需人工复核条款",
    "FAIL": "建议审计调整",
}

# 底稿 CSV 相对项目根目录（响应中返回相对路径）
WORKBOOK_OUTPUT_DIR = "reports/"
WORKBOOK_FILENAME = "底稿_GOSPD01010.csv"
WORKBOOK_RELATIVE_PATH = f"{WORKBOOK_OUTPUT_DIR}{WORKBOOK_FILENAME}"

app = FastAPI(
    title="合同截止性测试服务",
    description="接收三单系统推送，执行截止性测试并返回标准化结果",
    version="0.3.0-f3",
)

_checker = CutoffChecker()
_workbook = WorkbookGenerator()


def _workbook_absolute_path() -> Path:
    return Path(settings.REPORTS_DIR) / WORKBOOK_FILENAME


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "module": "cutoff", "phase": "F-3"}


def _resolve_payment_days(request: CutoffRequest) -> Optional[int]:
    """优先使用账期天数；否则从描述提取；都没有则返回 None。"""
    if request.合同账期天数 is not None:
        return int(request.合同账期天数)
    if request.合同账期描述:
        return extract_days_from_description(request.合同账期描述)
    return None


def _build_response(
    request: CutoffRequest,
    result: CutoffTestResult,
    payment_days: Optional[int],
) -> CutoffResponse:
    status = result.test_status
    # 底稿回填同时携带原始 request 字段，供 CSV 列映射
    return CutoffResponse(
        报告ID=f"CUTOFF-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}",
        业务编号=request.业务编号,
        测试状态=status,
        风险等级=STATUS_TO_RISK[status],
        应确认日期=result.expected_revenue_date,
        偏差天数=result.deviation_days,
        问题描述=result.issue_description,
        计算依据=result.calculation_basis,
        底稿回填={
            "业务编号": request.业务编号,
            "凭证号": None,
            "客户名称": request.客户名称,
            "合同编号": request.合同编号,
            "入账日期": request.入账日期,
            "入账金额": request.入账金额,
            "签收日期": request.签收日期,
            "合同账期（天）": payment_days,
            "合同账期天数": payment_days,
            "审计结论": STATUS_TO_AUDIT[status],
        },
        底稿文件路径=None,
    )


@app.post("/api/v1/cutoff", response_model=CutoffResponse)
def run_cutoff(request: CutoffRequest) -> CutoffResponse:
    """接收三单系统 JSON，执行截止性测试，追加底稿 CSV 并返回结果。"""
    payment_days = _resolve_payment_days(request)
    try:
        result = _checker.check(
            contract_payment_days=payment_days,
            receipt_date=request.签收日期,
            entry_date=request.入账日期,
        )
    except Exception as exc:
        logger.exception("CutoffChecker 执行异常: {}", exc)
        raise HTTPException(status_code=500, detail=f"截止性测试执行失败: {exc}") from exc

    response = _build_response(request, result, payment_days)

    try:
        abs_path = _workbook_absolute_path()
        _workbook.append_to_workbook(response, str(abs_path))
        response.底稿文件路径 = WORKBOOK_RELATIVE_PATH
    except Exception as exc:
        logger.exception("底稿 CSV 写入失败: {}", exc)
        raise HTTPException(status_code=500, detail=f"底稿CSV写入失败: {exc}") from exc

    logger.info(
        "cutoff done biz={} status={} risk={} payment_days={} workbook={}",
        request.业务编号,
        response.测试状态,
        response.风险等级,
        payment_days,
        response.底稿文件路径,
    )
    return response
