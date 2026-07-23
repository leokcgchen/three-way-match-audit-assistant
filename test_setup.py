from src.models import (
    AgentFinalReport,
    AuditProgramResult,
    ComplianceResult,
    CounterpartyInfo,
    ExtractedContractInfo,
    TestingStepResult,
)
from src.utils.logger import logger

logger.info("开始验证 Phase 1...")

audit = AuditProgramResult(
    step1_distinct_obligations=TestingStepResult(
        step_no=1,
        step_name="可区分履约义务",
        step_name_en="Distinct performance obligations",
        conclusion="Agrees",
        conclusion_zh="相符",
        notes="测试",
    ),
    step2_transaction_price=TestingStepResult(
        step_no=2,
        step_name="交易价格确定",
        step_name_en="Transaction price determination",
        conclusion="Agrees",
        conclusion_zh="相符",
        notes="测试",
    ),
    step3_revenue_recognition=TestingStepResult(
        step_no=3,
        step_name="交付证据与收入确认",
        step_name_en="Delivery evidence",
        conclusion="Not Selected",
        conclusion_zh="未选",
        notes="待三单",
    ),
)

test_report = AgentFinalReport(
    report_id="TEST-001",
    contract_info=ExtractedContractInfo(
        contract_id="HT-2026-001",
        parties=[],
        performance_obligations=[],
    ),
    compliance_result=ComplianceResult(
        overall_status="WARNING",
        issues=[],
        summary="测试",
    ),
    audit_program_result=audit,
    counterparty_info=CounterpartyInfo(parties=[]),
)

print(f"✅ AgentFinalReport 实例化成功！报告ID: {test_report.report_id}")
print(
    f"✅ 程序表结论: "
    f"{test_report.audit_program_result.step1_distinct_obligations.conclusion_zh}/"
    f"{test_report.audit_program_result.step2_transaction_price.conclusion_zh}/"
    f"{test_report.audit_program_result.step3_revenue_recognition.conclusion_zh}"
)
logger.info("Phase 1 初始化全部完成！")
