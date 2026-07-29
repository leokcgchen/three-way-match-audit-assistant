"""截止性测试执行器（供 HTTP 端点与三单联动进程内复用）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

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

_checker = CutoffChecker()
_workbook = WorkbookGenerator()


def workbook_absolute_path() -> Path:
    return settings.get_workbook_path()


def workbook_relative_path() -> str:
    return settings.get_workbook_relative_path()


def resolve_payment_days(request: CutoffRequest) -> Optional[int]:
    if request.合同账期天数 is not None:
        return int(request.合同账期天数)
    if request.合同账期描述:
        return extract_days_from_description(request.合同账期描述)
    return None


def build_cutoff_response(
    request: CutoffRequest,
    result: CutoffTestResult,
    payment_days: Optional[int],
) -> CutoffResponse:
    status = result.test_status
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


def perform_cutoff(request: CutoffRequest, *, write_workbook: bool = True) -> CutoffResponse:
    """执行截止性测试并可选写入底稿 CSV（仅截止性列；三单扩展列为空）。

    付款账期仍解析并回填底稿，但不参与应确认日计算。
    """
    payment_days = resolve_payment_days(request)
    result = _checker.check(
        contract_payment_days=payment_days,
        receipt_date=request.签收日期,
        entry_date=request.入账日期,
    )
    response = build_cutoff_response(request, result, payment_days)
    if write_workbook:
        abs_path = _workbook.append_to_workbook(response)
        response.底稿文件路径 = workbook_relative_path()
        logger.info("workbook appended path={}", abs_path)
    logger.info(
        "cutoff done biz={} status={} risk={} payment_days={} workbook={}",
        request.业务编号,
        response.测试状态,
        response.风险等级,
        payment_days,
        response.底稿文件路径,
    )
    return response
