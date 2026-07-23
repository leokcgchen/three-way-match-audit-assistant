"""合规审阅规则引擎单元测试（含程序表三步结论）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models import (
    ExtractedContractInfo,
    PartyInfo,
    PerformanceObligation,
)
from src.rules import ComplianceEngine


def _perfect_contract() -> ExtractedContractInfo:
    return ExtractedContractInfo(
        contract_id="HT-2026-PERFECT",
        contract_title="完美测试合同",
        signing_date="2026-03-15",
        parties=[
            PartyInfo(name="上海示例科技有限公司"),
            PartyInfo(name="北京合规审阅有限公司"),
        ],
        total_contract_amount=500.0,
        performance_obligations=[
            PerformanceObligation(
                description="向甲方提供软件开发服务并交付产品",
                amount=500.0,
            )
        ],
        control_transfer_time="验收合格后控制权转移至甲方",
        revenue_recognition_point="时点",
    )


def _missing_amount_contract() -> ExtractedContractInfo:
    info = _perfect_contract()
    info.contract_id = "HT-2026-NO-AMOUNT"
    info.total_contract_amount = None
    info.performance_obligations = [
        PerformanceObligation(description="提供咨询服务并交付报告")
    ]
    return info


def _missing_control_transfer_contract() -> ExtractedContractInfo:
    info = _perfect_contract()
    info.contract_id = "HT-2026-NO-CONTROL"
    info.control_transfer_time = None
    return info


def test_perfect_contract_audit_conclusions() -> None:
    engine = ComplianceEngine()
    result, audit = engine.review(_perfect_contract())

    # 步骤1/2 相符，步骤3 未选（交棒三单）；系统汇总为 WARNING
    assert audit.step1_distinct_obligations.conclusion == "Agrees"
    assert audit.step1_distinct_obligations.conclusion_zh == "相符"
    assert audit.step2_transaction_price.conclusion == "Agrees"
    assert audit.step2_transaction_price.conclusion_zh == "相符"
    assert audit.step3_revenue_recognition.conclusion == "Not Selected"
    assert audit.step3_revenue_recognition.conclusion_zh == "未选"
    assert audit.pending_for_three_way_match is True
    assert result.overall_status == "WARNING"
    assert "步骤1=Agrees/相符" in result.summary
    assert all(i.status == "PASS" for i in result.issues)
    print("test_perfect_contract_audit_conclusions: PASS")


def test_missing_amount_disagree() -> None:
    engine = ComplianceEngine()
    result, audit = engine.review(_missing_amount_contract())
    assert audit.step2_transaction_price.conclusion == "Disagrees"
    assert audit.step2_transaction_price.conclusion_zh == "不符"
    assert result.overall_status == "FAIL"
    print("test_missing_amount_disagree: PASS")


def test_missing_control_transfer_step3_still_not_selected() -> None:
    engine = ComplianceEngine()
    result, audit = engine.review(_missing_control_transfer_contract())
    assert audit.step1_distinct_obligations.conclusion == "Agrees"
    assert audit.step2_transaction_price.conclusion == "Agrees"
    assert audit.step3_revenue_recognition.conclusion == "Not Selected"
    assert "控制权" in audit.step3_revenue_recognition.notes or "截止性" in audit.step3_revenue_recognition.notes
    assert result.overall_status == "WARNING"
    cutoff_issue = next(i for i in result.issues if i.rule_id == "GOSPD01010-4")
    assert cutoff_issue.status == "WARNING"
    print("test_missing_control_transfer_step3_still_not_selected: PASS")


if __name__ == "__main__":
    test_perfect_contract_audit_conclusions()
    test_missing_amount_disagree()
    test_missing_control_transfer_step3_still_not_selected()
    print("全部测试通过：程序表三步结论逻辑正确。")
