"""三单匹配适配器数据模型。"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

TestStatus = Literal["PASS", "WARNING", "FAIL", "SKIPPED"]
MatchStatus = Literal["PASS", "WARNING", "FAIL"]


class Order(BaseModel):
    """订单。"""

    order_no: str = Field(description="订单编号")
    supplier_name: str = Field(description="供应商名称")
    total_amount: float = Field(description="订单总金额，万元")
    quantity: float = Field(description="数量")
    unit: Optional[str] = Field(default=None, description="单位")
    order_date: Optional[str] = Field(default=None, description="订单日期 YYYY-MM-DD")
    payment_terms: Optional[str] = Field(
        default=None, description='付款条款，如"票到30天"'
    )
    contract_no: Optional[str] = Field(default=None, description="合同编号")


class WarehouseReceipt(BaseModel):
    """入库单 / 签收单。"""

    receipt_no: str = Field(description="入库单编号")
    order_no: str = Field(description="对应订单编号")
    supplier_name: str = Field(description="供应商名称")
    total_amount: float = Field(description="入库金额，万元")
    quantity: float = Field(description="入库数量")
    receipt_date: str = Field(description="入库/签收日期 YYYY-MM-DD")
    receiver: Optional[str] = Field(default=None, description="签收人")


class Invoice(BaseModel):
    """发票。"""

    invoice_no: str = Field(description="发票编号")
    order_no: str = Field(description="对应订单编号")
    supplier_name: str = Field(description="供应商名称")
    total_amount: float = Field(description="发票金额，万元")
    quantity: float = Field(description="发票数量")
    invoice_date: Optional[str] = Field(default=None, description="开票日期 YYYY-MM-DD")
    posting_date: Optional[str] = Field(
        default=None, description="财务入账日期 YYYY-MM-DD"
    )


class ThreeWayMatchRequest(BaseModel):
    """三单匹配请求。"""

    order: Order
    warehouse_receipt: WarehouseReceipt
    invoice: Invoice


class MatchResult(BaseModel):
    """单字段比对结果。"""

    field_name: str = Field(description="字段名")
    order_value: Any = None
    receipt_value: Any = None
    invoice_value: Any = None
    is_consistent: bool = Field(description="三方是否一致")
    diff_description: Optional[str] = Field(default=None, description="差异说明")


class ThreeWayMatchResponse(BaseModel):
    """三单匹配响应。"""

    order_no: str
    overall_status: MatchStatus
    match_score: float = Field(description="匹配得分，0-100")
    comparisons: List[MatchResult] = Field(description="逐项比对结果")
    summary: str = Field(description="一句话总结")
    risks: List[str] = Field(default_factory=list, description="风险列表")
    cutoff_available: bool = Field(
        default=True, description="截止性测试是否已执行/可用"
    )
    cutoff_skipped_reason: Optional[str] = Field(
        default=None, description="跳过截止性测试的原因"
    )
    cutoff_test_status: Optional[TestStatus] = Field(
        default=None,
        description="截止性测试状态；SKIPPED 表示入账日期缺失等原因未执行",
    )
    human_readable_summary: str = Field(
        default="", description="自然语言摘要（含三单+截止性+综合结论）"
    )
