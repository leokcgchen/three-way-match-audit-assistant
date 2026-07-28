"""截止性测试核心逻辑单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rules import CutoffChecker
from src.utils.audit_utils import (
    run_builtin_cutoff_self_check,
    verify_cutoff_calculation,
)


def test_fully_compliant() -> None:
    checker = CutoffChecker()
    result = checker.check(
        contract_payment_days=10,
        receipt_date="2026-06-01",
        entry_date="2026-06-11",
    )
    assert result.test_status == "PASS"
    assert result.deviation_days == 0
    assert result.expected_revenue_date == "2026-06-11"
    assert result.actual_entry_date == "2026-06-11"
    print("test_fully_compliant: PASS")


def test_early_recognition() -> None:
    checker = CutoffChecker()
    result = checker.check(
        contract_payment_days=10,
        receipt_date="2026-06-01",
        entry_date="2026-06-05",
    )
    assert result.test_status == "FAIL"
    assert result.deviation_days == -6
    assert "提前6天确认收入" in result.issue_description
    print("test_early_recognition: PASS")


def test_delayed_recognition() -> None:
    checker = CutoffChecker()
    result = checker.check(
        contract_payment_days=10,
        receipt_date="2026-06-01",
        entry_date="2026-06-20",
    )
    assert result.test_status == "WARNING"
    assert result.deviation_days == 9
    assert "延迟9天确认收入" in result.issue_description
    print("test_delayed_recognition: PASS")


def test_missing_receipt_date() -> None:
    checker = CutoffChecker()
    result = checker.check(
        contract_payment_days=10,
        receipt_date=None,
        entry_date="2026-06-11",
    )
    assert result.test_status == "WARNING"
    assert result.deviation_days is None
    assert result.issue_description == "缺少签收日期或入账日期，无法执行截止性测试"
    print("test_missing_receipt_date: PASS")


def test_cutoff_calculation_trail() -> None:
    checker = CutoffChecker()
    result = checker.check(
        contract_payment_days=10,
        receipt_date="2026-06-01",
        entry_date="2026-06-05",
    )
    trail = result.calculation_trail
    assert trail is not None
    assert len(trail) == 6
    assert trail[0]["action"] == "解析签收日期"
    assert trail[0]["input"] == "2026-06-01"
    assert trail[0]["output"] == "2026-06-01"
    assert trail[1]["action"] == "解析入账日期"
    assert trail[1]["output"] == "2026-06-05"
    assert trail[2]["action"] == "读取账期"
    assert trail[2]["output"] == 10
    assert trail[3]["action"] == "计算应确认日期"
    assert trail[3]["formula"] == "2026-06-01 + 10天"
    assert trail[3]["output"] == "2026-06-11"
    assert trail[4]["action"] == "计算偏差天数"
    assert trail[4]["formula"] == "2026-06-05 - 2026-06-11"
    assert trail[4]["output"] == -6
    assert trail[5]["action"] == "判断合规性"
    assert "FAIL" in str(trail[5]["output"])
    print("test_cutoff_calculation_trail: PASS")


def test_cutoff_self_verify() -> None:
    outcome = verify_cutoff_calculation(
        receipt_date="2026-06-01",
        entry_date="2026-06-05",
        payment_days=10,
        expected_result="FAIL",
    )
    assert outcome["match"] is True
    assert outcome["actual"] == "FAIL"
    assert outcome["expected"] == "FAIL"
    assert outcome["deviation_days"] == -6
    assert len(outcome["trail"]) == 6

    report = run_builtin_cutoff_self_check()
    assert report["all_passed"] is True
    assert report["passed"] == 5
    print("test_cutoff_self_verify: PASS")


if __name__ == "__main__":
    test_fully_compliant()
    test_early_recognition()
    test_delayed_recognition()
    test_missing_receipt_date()
    test_cutoff_calculation_trail()
    test_cutoff_self_verify()
    print("全部测试通过：截止性测试核心逻辑正确。")
