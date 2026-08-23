"""截止性测试 API / 引擎相关数据模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class CutoffTestResult(BaseModel):
    """CutoffChecker 引擎内部输出（保持与 cutoff_checker 兼容）。"""

    test_status: Literal["PASS", "WARNING", "FAIL"] = Field(
        description="PASS=合规, WARNING=需关注, FAIL=不合规"
    )
    expected_revenue_date: Optional[str] = Field(
        default=None, description="应确认日期"
    )
    actual_entry_date: Optional[str] = Field(default=None, description="实际入账日期")
    deviation_days: Optional[int] = Field(default=None, description="偏差天数")
    issue_description: str = Field(description="问题描述")
    calculation_basis: str = Field(description="计算依据说明")
    calculation_trail: Optional[List[dict]] = Field(
        default=None, description="计算轨迹"
    )


class CutoffRequest(BaseModel):
    """接收三单系统传入的截止性测试请求。"""

    业务编号: str = Field(description="必填，关联主键")
    合同编号: Optional[str] = Field(default=None)
    客户名称: Optional[str] = Field(default=None)
    合同账期描述: Optional[str] = Field(
        default=None,
        description='付款/结算条款，如"签收后10日"；截止性不使用，保留供收款等测试',
    )
    合同账期天数: Optional[int] = Field(
        default=None,
        description="付款账期天数；截止性公式不使用，仍写入底稿供后续测试",
    )
    签收日期: str = Field(description="必填，YYYY-MM-DD")
    入账日期: str = Field(description="必填，YYYY-MM-DD")
    入账金额: float = Field(description="必填")
    报告期末日: Optional[str] = Field(
        default=None,
        description="可选 YYYY-MM-DD；有值时参与截止主判断（报告期末边界）",
    )

    @field_validator("签收日期", "入账日期")
    @classmethod
    def validate_yyyy_mm_dd(cls, value: str) -> str:
        text = str(value).strip()
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("日期格式应为 YYYY-MM-DD") from exc
        return text

    @field_validator("报告期末日")
    @classmethod
    def validate_period_end(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not str(value).strip():
            return None
        text = str(value).strip()
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("报告期末日格式应为 YYYY-MM-DD") from exc
        return text


class CutoffResult(BaseModel):
    """截止性测试结果（计算引擎对外映射结果）。"""

    test_status: Literal["PASS", "WARNING", "FAIL"]
    风险等级: Literal["无异常", "条款模糊-人工复核", "明确错报-需审计调整"]
    应确认日期: Optional[str] = None
    偏差天数: Optional[int] = None
    问题描述: str
    计算依据: str


class CutoffResponse(BaseModel):
    """API 最终输出。"""

    报告ID: str
    业务编号: str
    测试状态: Literal["PASS", "WARNING", "FAIL"]
    风险等级: Literal["无异常", "条款模糊-人工复核", "明确错报-需审计调整"]
    应确认日期: Optional[str] = None
    偏差天数: Optional[int] = None
    问题描述: str
    计算依据: str
    底稿回填: dict = Field(
        default_factory=dict,
        description="含凭证号、客户、合同编号、入账/签收日期、账期、审计结论等",
    )
    底稿文件路径: Optional[str] = Field(
        default=None, description="底稿CSV保存路径"
    )
    LLM解读: Optional[Dict[str, Any]] = Field(
        default=None,
        description="规则结论旁路解读（不改测试状态）",
    )
