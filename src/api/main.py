"""FastAPI 后端：合同上传、三单数据、健康检查与报告查询。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError

from config.settings import settings
from src.agent import ContractComplianceAgent
from src.models.contract_models import DeliveryReceiptInfo, LedgerEntryInfo
from src.utils.logger import logger, setup_logger

setup_logger(settings.LOG_LEVEL)

app = FastAPI(
    title="合同合规审阅 Agent API",
    description=(
        "上传合同并返回合规审阅报告；可选传入 ledger_entry / delivery_receipt "
        "（JSON字符串）以执行截止性测试，供人工界面与下游 Agent 调用"
    ),
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPPORTED_SUFFIXES = {".pdf", ".docx"}
agent = ContractComplianceAgent()


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """浏览器访问根路径时跳转到 Swagger 文档。"""
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _parse_form_json(raw: Optional[str], field_name: str) -> Any:
    """解析 Form 中的 JSON 字符串；非法则抛出 422。"""
    if raw is None or not str(raw).strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} 不是合法 JSON: {exc}",
        ) from exc


def _parse_ledger_entry(raw: Optional[str]) -> Optional[LedgerEntryInfo]:
    data = _parse_form_json(raw, "ledger_entry")
    if data is None:
        return None
    try:
        return LedgerEntryInfo.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"ledger_entry 校验失败: {exc.errors()}",
        ) from exc


def _parse_delivery_receipt(raw: Optional[str]) -> Optional[DeliveryReceiptInfo]:
    data = _parse_form_json(raw, "delivery_receipt")
    if data is None:
        return None
    try:
        return DeliveryReceiptInfo.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"delivery_receipt 校验失败: {exc.errors()}",
        ) from exc


@app.post("/upload")
async def upload_contract(
    file: UploadFile = File(...),
    ledger_entry: Optional[str] = Form(None),
    delivery_receipt: Optional[str] = Form(None),
) -> JSONResponse:
    """接收合同文件，可选三单 JSON，执行审阅流水线并返回报告。"""
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()

    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {suffix or '(无扩展名)'}，仅支持 .pdf / .docx",
        )

    parsed_ledger = _parse_ledger_entry(ledger_entry)
    parsed_receipt = _parse_delivery_receipt(delivery_receipt)

    tmp_path: Path | None = None
    try:
        content = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        logger.info(
            "收到上传文件: name={}, tmp={}, has_ledger={}, has_receipt={}",
            filename,
            tmp_path,
            parsed_ledger is not None,
            parsed_receipt is not None,
        )
        report = agent.process_contract(
            str(tmp_path),
            ledger_entry=parsed_ledger,
            delivery_receipt=parsed_receipt,
        )
        agent.save_report(report, output_dir=str(settings.REPORTS_DIR))
        return JSONResponse(content=report.model_dump(mode="json"))

    except ValueError as exc:
        logger.error("合同解析失败: {}", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("处理上传文件时发生异常: {}", exc)
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {exc}") from exc
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.warning("临时文件清理失败: {}", tmp_path)


@app.get("/report/{report_id}")
def get_report(report_id: str) -> JSONResponse:
    """从 reports/ 目录读取已保存的报告。"""
    report_path = settings.REPORTS_DIR / f"{report_id}.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail=f"报告不存在: {report_id}")

    try:
        with report_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return JSONResponse(content=data)
    except Exception as exc:
        logger.exception("读取报告失败: {}", exc)
        raise HTTPException(status_code=500, detail=f"读取报告失败: {exc}") from exc
