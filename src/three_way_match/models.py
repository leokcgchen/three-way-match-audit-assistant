"""三单匹配适配器数据模型。"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

TestStatus = Literal["PASS", "WARNING", "FAIL", "SKIPPED"]
MatchStatus = Literal["PASS", "WARNING", "FAIL"]
DecisionStatus = Literal[
    "AUTO_PASS", "HOLD_REVIEW", "PASS_WITH_WARNING", "NOT_APPLICABLE"
]
HoldReasonCode = Literal[
    "PAPER_FIELD",
    "AMBIGUOUS_BINDING",
    "DOCUMENT_MISSING",
    "AWAITING_ERP",
]

class Order(BaseModel):
    """销售订单。"""

    order_no: str = Field(description="订单编号")
    supplier_name: str = Field(
        description="客户名称（历史字段名 supplier_name，销售收入语境）"
    )
    total_amount: float = Field(description="订单总金额，元；缺失时用 0 并在引擎侧标未测")
    quantity: float = Field(description="数量")
    unit: Optional[str] = Field(default=None, description="单位")
    order_date: Optional[str] = Field(default=None, description="订单日期 YYYY-MM-DD")
    payment_terms: Optional[str] = Field(
        default=None, description='付款条款，如"票到30天"'
    )
    contract_no: Optional[str] = Field(default=None, description="合同编号")

    @property
    def customer_name(self) -> str:
        return self.supplier_name


class WarehouseReceipt(BaseModel):
    """客户签收/验收单（历史类名 WarehouseReceipt）。"""

    receipt_no: str = Field(description="签收/验收单编号")
    order_no: str = Field(description="对应订单编号")
    supplier_name: str = Field(description="客户名称（历史字段名）")
    total_amount: float = Field(
        description="签收金额，元；缺失时用 0，引擎不得当作有效业务额"
    )
    quantity: float = Field(description="签收数量")
    receipt_date: str = Field(description="签收/验收日期 YYYY-MM-DD；缺失时为空或 UNRESOLVED")
    receiver: Optional[str] = Field(default=None, description="签收人")

    @property
    def customer_name(self) -> str:
        return self.supplier_name


# 销售侧别名，便于新代码阅读
GoodsReceipt = WarehouseReceipt


class Invoice(BaseModel):
    """销售发票。"""

    invoice_no: str = Field(description="发票编号")
    order_no: str = Field(description="对应订单编号")
    supplier_name: str = Field(description="客户名称（历史字段名）")
    total_amount: float = Field(description="发票金额，元")
    quantity: float = Field(description="发票数量")
    invoice_date: Optional[str] = Field(default=None, description="开票日期 YYYY-MM-DD")
    posting_date: Optional[str] = Field(
        default=None, description="财务入账日期 YYYY-MM-DD"
    )

    @property
    def customer_name(self) -> str:
        return self.supplier_name


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
    auditor_explain: Optional[str] = Field(
        default=None, description="审计师可读解释句（不改结论）"
    )
    pick_reason: Optional[str] = Field(
        default=None, description="为何选这个语义槽（悬停复核）"
    )


class ThreeWayMatchResponse(BaseModel):
    """三单匹配响应。"""

    order_no: str
    overall_status: MatchStatus
    match_score: float = Field(
        default=0.0,
        description="已废弃；兼容旧契约固定为 0，不以得分放行或展示",
    )
    comparisons: List[MatchResult] = Field(description="逐项比对结果")
    summary: str = Field(description="一句话总结")
    risks: List[str] = Field(default_factory=list, description="风险列表")
    decision: DecisionStatus = Field(
        default="HOLD_REVIEW",
        description="决策四态：AUTO_PASS / HOLD_REVIEW / PASS_WITH_WARNING / NOT_APPLICABLE",
    )
    decision_reasons: List[str] = Field(
        default_factory=list, description="判定表原因（如 D3:P0 …）"
    )
    hold_reason_code: Optional[HoldReasonCode] = Field(
        default=None,
        description="HOLD 分因：PAPER_FIELD / AMBIGUOUS_BINDING / DOCUMENT_MISSING / AWAITING_ERP",
    )
    quantity_roles: dict[str, Any] = Field(
        default_factory=dict,
        description="数量角色分槽：ordered_qty / received_qty / invoiced_qty",
    )
    slot_reasons: dict[str, str] = Field(
        default_factory=dict, description="标准槽选值理由（前端悬停）"
    )
    erp_review: dict[str, Any] = Field(
        default_factory=dict,
        description="纸面 vs ERP 分层；缺 ERP 时 status=UNAVAILABLE",
    )
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
