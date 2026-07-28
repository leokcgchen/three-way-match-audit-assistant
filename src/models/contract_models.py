from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class PartyInfo(BaseModel):
    name: str = Field(description="合同当事人名称")
    unified_social_credit_code: Optional[str] = Field(
        default=None, description="统一社会信用代码"
    )
    address: Optional[str] = Field(default=None, description="地址")
    legal_representative: Optional[str] = Field(
        default=None, description="法定代表人"
    )


class PerformanceObligation(BaseModel):
    description: str = Field(description="履约义务描述")
    amount: Optional[float] = Field(default=None, description="履约义务金额")
    delivery_time: Optional[str] = Field(default=None, description="交付/履约时间")


class ExtractedContractInfo(BaseModel):
    contract_id: Optional[str] = Field(default=None, description="合同编号")
    contract_title: Optional[str] = Field(default=None, description="合同标题")
    signing_date: Optional[str] = Field(default=None, description="签署日期")
    parties: List[PartyInfo] = Field(default_factory=list, description="合同当事人列表")
    total_contract_amount: Optional[float] = Field(
        default=None, description="合同总金额"
    )
    performance_obligations: List[PerformanceObligation] = Field(
        default_factory=list, description="履约义务列表"
    )
    revenue_recognition_point: Optional[Literal["时点", "时段"]] = Field(
        default=None, description="收入确认时点类型"
    )
    control_transfer_time: Optional[str] = Field(
        default=None, description="控制权转移时点"
    )
    contract_term: Optional[str] = Field(default=None, description="合同期限")
    raw_text_preview: Optional[str] = Field(
        default=None, description="合同原文预览片段"
    )


class ComplianceIssue(BaseModel):
    rule_id: str = Field(description="规则编号")
    rule_name: str = Field(description="规则名称")
    status: Literal["PASS", "WARNING", "FAIL"] = Field(description="该规则审阅状态")
    description: str = Field(description="问题描述")
    suggestion: Optional[str] = Field(default=None, description="整改建议")


class ComplianceResult(BaseModel):
    overall_status: Literal["PASS", "WARNING", "FAIL"] = Field(
        description="总体合规状态（内部汇总，便于系统流转）"
    )
    issues: List[ComplianceIssue] = Field(
        default_factory=list, description="合规问题列表"
    )
    summary: str = Field(description="合规审阅摘要")


AuditConclusion = Literal["Agrees", "Disagrees", "N/A", "Not Selected"]


class TestingStepResult(BaseModel):
    """对齐 KPMG 审计程序表单步结论。"""

    step_no: int = Field(description="测试步骤编号：1/2/3")
    step_name: str = Field(description="测试步骤名称")
    step_name_en: str = Field(description="测试步骤英文名称")
    conclusion: AuditConclusion = Field(
        description="程序表结论：Agrees/Disagrees/N/A/Not Selected"
    )
    conclusion_zh: Literal["相符", "不符", "不适用", "未选"] = Field(
        description="程序表中文结论"
    )
    notes: str = Field(description="注释/Notes，说明判断依据或待办")


class AuditProgramResult(BaseModel):
    """对齐抽凭测试步骤的三步结论，供人工底稿与下游三单Agent使用。"""

    step1_distinct_obligations: TestingStepResult = Field(
        description="步骤1：可区分履约义务"
    )
    step2_transaction_price: TestingStepResult = Field(
        description="步骤2：交易价格确定"
    )
    step3_revenue_recognition: TestingStepResult = Field(
        description="步骤3：交付证据/控制权转移/期间/收入重算（默认交棒三单Agent）"
    )
    pending_for_three_way_match: bool = Field(
        default=True,
        description="步骤3是否仍待三单智能匹配Agent最终裁定",
    )


class CompanyProfile(BaseModel):
    company_name: str = Field(description="企业名称")
    registration_status: Optional[str] = Field(
        default=None, description="登记状态"
    )
    business_scope: Optional[str] = Field(default=None, description="经营范围")
    legal_representative: Optional[str] = Field(
        default=None, description="法定代表人"
    )
    registered_capital: Optional[str] = Field(
        default=None, description="注册资本"
    )
    establishment_date: Optional[str] = Field(
        default=None, description="成立日期"
    )
    is_abnormal: bool = Field(default=False, description="是否经营异常")
    is_blacklisted: bool = Field(default=False, description="是否被列入黑名单")
    litigation_risk_summary: Optional[str] = Field(
        default=None, description="诉讼风险摘要"
    )
    data_source: Literal[
        "MOCK", "QCC_API", "WIND_API", "EAST_MONEY_API", "UNKNOWN"
    ] = Field(description="数据来源")


class CounterpartyInfo(BaseModel):
    parties: List[CompanyProfile] = Field(
        default_factory=list, description="交易对手企业画像列表"
    )
    confidence_note: Optional[str] = Field(
        default=None, description="置信度说明"
    )


class DeliveryReceiptInfo(BaseModel):
    receipt_date: Optional[str] = Field(
        default=None, description="实际签收/验收日期，格式 YYYY-MM-DD"
    )
    received_quantity: Optional[float] = Field(
        default=None, description="签收数量（如有）"
    )
    receiver_name: Optional[str] = Field(
        default=None, description="签收人姓名（如有）"
    )
    notes: Optional[str] = Field(
        default=None, description="备注（如验收意见等）"
    )


class LedgerEntryInfo(BaseModel):
    entry_date: str = Field(description="账面入账日期，格式 YYYY-MM-DD")
    entry_amount: float = Field(description="账面确认收入金额（万元）")
    voucher_id: Optional[str] = Field(
        default=None, description="凭证编号（如 记-126）"
    )
    customer_name: Optional[str] = Field(
        default=None, description="客户名称（用于交叉核对）"
    )


class CutoffTestResult(BaseModel):
    test_status: Literal["PASS", "WARNING", "FAIL"] = Field(
        description="PASS=合规, WARNING=需关注（无签收单等）, FAIL=不合规"
    )
    expected_revenue_date: Optional[str] = Field(
        default=None, description="根据合同+签收单计算出的应确认日期"
    )
    actual_entry_date: Optional[str] = Field(
        default=None, description="实际入账日期"
    )
    deviation_days: Optional[int] = Field(
        default=None, description="偏差天数（正=延迟，负=提前）"
    )
    issue_description: str = Field(
        description="问题描述，如'提前6天确认收入'"
    )
    calculation_basis: str = Field(
        description="计算依据说明，如'签收日2026-06-01 + 账期10日 = 应确认2026-06-11'"
    )
    calculation_trail: Optional[List[dict]] = Field(
        default=None,
        description="计算轨迹，记录每一步的计算过程",
    )


class AgentFinalReport(BaseModel):
    report_id: str = Field(description="报告唯一标识")
    generated_at: datetime = Field(
        default_factory=datetime.now, description="报告生成时间"
    )
    contract_info: ExtractedContractInfo = Field(description="抽取后的合同信息")
    compliance_result: ComplianceResult = Field(description="合规审阅结果（规则明细）")
    audit_program_result: AuditProgramResult = Field(
        description="对齐KPMG测试步骤的三步结论（相符/不符/不适用/未选）"
    )
    counterparty_info: CounterpartyInfo = Field(description="交易对手信息")
    human_judgment_summary: str = Field(
        default="请人工复核关键风险点后确认最终结论。",
        description="人工判断引导摘要",
    )
    to_downstream_json: Dict = Field(
        default_factory=dict,
        description="供下游三单匹配Agent使用的结构化数据",
    )
    cutoff_test_result: Optional[CutoffTestResult] = Field(
        default=None,
        description="截止性测试结果（如提供了签收单和序时账）",
    )
