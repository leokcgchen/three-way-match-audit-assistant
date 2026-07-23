"""FastAPI 后端：合同上传、健康检查与报告查询。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from config.settings import settings
from src.agent import ContractComplianceAgent
from src.utils.logger import logger, setup_logger

setup_logger(settings.LOG_LEVEL)

app = FastAPI(
    title="合同合规审阅 Agent API",
    description="上传合同并返回合规审阅报告，供人工界面与下游 Agent 调用",
    version="1.0.0",
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


@app.post("/upload")
async def upload_contract(file: UploadFile = File(...)) -> JSONResponse:
    """接收合同文件，执行完整审阅流水线并返回报告 JSON。"""
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()

    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {suffix or '(无扩展名)'}，仅支持 .pdf / .docx",
        )

    tmp_path: Path | None = None
    try:
        content = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        logger.info("收到上传文件: name={}, tmp={}", filename, tmp_path)
        report = agent.process_contract(str(tmp_path))
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
