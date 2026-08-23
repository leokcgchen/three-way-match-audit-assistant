"""金额测试报告模型（对齐实施手册 §6 / §12 / §15）。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

AmountStatus = Literal["PASS", "WARNING", "FAIL", "SKIPPED"]
IssueType = Literal[
    "UNIT_PRICE_ENTRY_ERROR",
    "COMMERCIAL_DISCOUNT_ERROR",
    "OUTPUT_VAT_ENTRY_ERROR",
    "AMOUNT_ENTRY_ERROR",
    "LEDGER_BASIS_MISMATCH",
    "NONE",
]
Direction = Literal["BOOK_OVERSTATED", "BOOK_UNDERSTATED", "NONE"]
MatchStatus = Literal["MATCHED", "CONFLICT", "INCOMPLETE", "SKIPPED"]


class SourceValues(BaseModel):
    currency: str = "CNY"
    quantity: Optional[float] = None
    unit_price_excl_tax: Optional[float] = None
    unit_price_incl_tax: Optional[float] = None
    discount_rate: Optional[float] = None
    vat_rate: Optional[float] = None
    price_basis: str = "EXCL_TAX"
    quantity_source: str = ""
    price_source: str = ""


class Recalculation(BaseModel):
    gross_before_discount_excl_tax: Optional[float] = None
    discount_amount_excl_tax: Optional[float] = None
    net_amount_excl_tax: Optional[float] = None
    vat_amount: Optional[float] = None
    gross_amount_incl_tax: Optional[float] = None
    rounding_rule: str = "LINE_LEVEL_2_DECIMALS"
    formula: str = ""
    is_export: bool = False


class LedgerValues(BaseModel):
    voucher_no: str = ""
    posting_date: str = ""
    customer_code: str = ""
    customer_name: str = ""
    sales_order_no: str = ""
    material_code: str = ""
    ledger_ar_debit: Optional[float] = None
    ledger_revenue_credit: Optional[float] = None
    ledger_output_vat_credit: Optional[float] = None
    ledger_debit_total: Optional[float] = None
    ledger_credit_total: Optional[float] = None
    amount_basis: str = "GROSS_AMOUNT_INCL_TAX"


class AmountTestDetail(BaseModel):
    test_status: AmountStatus = "SKIPPED"
    risk_level: str = ""
    issue_type: IssueType = "NONE"
    difference_amount: Optional[float] = None
    difference_rate: Optional[float] = None
    direction: Direction = "NONE"
    issue_description: str = ""
    technical_tolerance: float = 0.02
    layer_diffs: dict = Field(default_factory=dict)


class MatchingInfo(BaseModel):
    status: MatchStatus = "SKIPPED"
    score: int = 0
    matched_document_indexes: List[str] = Field(default_factory=list)
    conflict_note: str = ""


class WorkpaperFill(BaseModel):
    审计结论: str = ""
    差异说明: str = ""
    证据链索引: str = ""
    建议调整金额: Optional[float] = None
    账面金额口径: str = ""
    重算不含税金额: Optional[float] = None
    重算税额: Optional[float] = None
    重算价税合计: Optional[float] = None
    合同单价: Optional[float] = None
    折扣率: Optional[float] = None
    税率: Optional[float] = None
    商品及数量: str = ""
    异常类型: str = ""
    自动测试状态: str = ""


class AmountAccuracyReport(BaseModel):
    """手册 §12 标准输出。"""

    report_id: str = ""
    business_id: str = ""
    voucher_no: str = ""
    contract_no: str = ""
    customer_name: str = ""
    matching: MatchingInfo = Field(default_factory=MatchingInfo)
    source_values: SourceValues = Field(default_factory=SourceValues)
    recalculation: Recalculation = Field(default_factory=Recalculation)
    ledger_values: LedgerValues = Field(default_factory=LedgerValues)
    amount_test: AmountTestDetail = Field(default_factory=AmountTestDetail)
    workpaper_fill: WorkpaperFill = Field(default_factory=WorkpaperFill)
    human_readable_summary: str = ""
    # 旁路：规则结论的 LLM 解读（不改 test_status）
    llm_interpretation: Dict[str, Any] = Field(default_factory=dict)
    # 受控补缺：金额 LLM 主张经 verifier 后的顾问队列快照
    advisory_candidates: List[Dict[str, Any]] = Field(default_factory=list)


class AmountBatchResult(BaseModel):
    total: int = 0
    pass_count: int = 0
    fail_count: int = 0
    warning_count: int = 0
    skipped_count: int = 0
    reports: List[AmountAccuracyReport] = Field(default_factory=list)
