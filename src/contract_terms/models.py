"""合同条款清晰性测试报告模型（对齐实施手册 §9 / §10）。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ClauseStatus = Literal["PASS", "WARNING", "FAIL", "SKIPPED"]
TestDimension = Literal[
    "交易对价",
    "支付条款",
    "履约义务",
    "运输及控制权转移",
    "综合",
    "无",
]


class ClauseEvidence(BaseModel):
    file: str = ""
    page: Optional[int] = None
    clause: Optional[str] = None
    text_excerpt: str = ""


class ContractClarityIssue(BaseModel):
    issue_code: str
    dimension: TestDimension = "综合"
    description: str = ""
    excerpt: str = ""
    source: Literal["rule", "llm"] = "rule"
    confidence: Optional[float] = None


class ContractTestResultBlock(BaseModel):
    test_status: ClauseStatus = "SKIPPED"
    risk_level: str = ""
    test_dimension: TestDimension = "无"
    issue_code: Optional[str] = None
    issue_description: str = ""
    accounting_misstatement_detected: bool = False
    manual_review_required: bool = False
    issue_family: str = "CONTRACT_CLARITY"
    misstatement_status: str = "NOT_DETERMINED_FROM_CONTRACT_ALONE"
    issues: List[ContractClarityIssue] = Field(default_factory=list)


class ContractClarityReport(BaseModel):
    report_id: str = ""
    business_id: str = ""
    voucher_no: str = ""
    contract_id: str = ""
    customer_name: str = ""
    test_result: ContractTestResultBlock = Field(default_factory=ContractTestResultBlock)
    evidence: List[ClauseEvidence] = Field(default_factory=list)
    extracted: Dict[str, Any] = Field(default_factory=dict)
    ledger_check: Dict[str, Any] = Field(default_factory=dict)
    workpaper_fill: Dict[str, Any] = Field(default_factory=dict)
    human_readable_summary: str = ""


class ContractClarityBatchResult(BaseModel):
    total: int = 0
    pass_count: int = 0
    warning_count: int = 0
    fail_count: int = 0
    skipped_count: int = 0
    reports: List[ContractClarityReport] = Field(default_factory=list)
