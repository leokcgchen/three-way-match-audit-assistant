"""截止性引擎：会计期间主判据（控制权转移日 vs 过账日，不含付款账期）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rules.cutoff_checker import CutoffChecker


def test_pass_same_day():
    result = CutoffChecker().check(
        contract_payment_days=30,
        receipt_date="2026-01-02",
        entry_date="2026-01-02",
    )
    assert result.test_status == "PASS"
    assert result.expected_revenue_date == "2026-01-02"
    assert result.deviation_days == 0


def test_payment_days_ignored():
    """付款账期不得把应确认日往后推。"""
    result = CutoffChecker().check(
        contract_payment_days=30,
        receipt_date="2026-01-02",
        entry_date="2026-01-02",
    )
    assert result.expected_revenue_date == "2026-01-02"
    assert "付款账期不参与" in result.calculation_basis


def test_fail_early_recognition_so25_0281():
    """验收 2026-01-02，过账 2025-12-10 → 跨期提前 FAIL。"""
    result = CutoffChecker().check(
        contract_payment_days=30,
        receipt_date="2026-01-02",
        entry_date="2025-12-10",
    )
    assert result.test_status == "FAIL"
    assert result.expected_revenue_date == "2026-01-02"
    assert result.deviation_days is not None and result.deviation_days < 0
    assert "提前" in result.issue_description


def test_fail_cross_period_delayed():
    """同月内延迟为 PASS；跨月延后为 FAIL。"""
    same = CutoffChecker().check(
        contract_payment_days=None,
        receipt_date="2026-06-01",
        entry_date="2026-06-11",
    )
    assert same.test_status == "PASS"
    assert same.deviation_days == 10
    assert "操作性偏差" in same.issue_description

    cross = CutoffChecker().check(
        contract_payment_days=None,
        receipt_date="2026-06-01",
        entry_date="2026-07-01",
    )
    assert cross.test_status == "FAIL"
    assert "延后" in cross.issue_description or "跨期" in (cross.issue_description + cross.calculation_basis)


def test_pass_same_period_day_diff():
    """同期间日差不再用 ±2 天容差放行跨期。"""
    result = CutoffChecker().check(
        contract_payment_days=10,
        receipt_date="2026-06-01",
        entry_date="2026-06-02",
    )
    assert result.test_status == "PASS"
    assert result.deviation_days == 1


def test_fail_year_end_boundary_not_tolerance():
    """12/31 与次年 1/1：旧 ±2 天会误 PASS，期间口径必须 FAIL。"""
    result = CutoffChecker().check(
        contract_payment_days=None,
        receipt_date="2025-12-31",
        entry_date="2026-01-01",
    )
    assert result.test_status == "FAIL"
    assert result.deviation_days == 1


def test_period_end_boundary_fail():
    """提供报告期末日时：控制权期后、入账期内 → FAIL。"""
    result = CutoffChecker().check(
        contract_payment_days=None,
        receipt_date="2026-01-02",
        entry_date="2025-12-20",
        period_end="2025-12-31",
    )
    assert result.test_status == "FAIL"
    assert "报告期末" in result.issue_description or "报告期末" in result.calculation_basis


def test_period_end_same_side_pass():
    """期末日两侧同属期内且同月 → PASS。"""
    result = CutoffChecker().check(
        contract_payment_days=None,
        receipt_date="2025-12-10",
        entry_date="2025-12-15",
        period_end="2025-12-31",
    )
    assert result.test_status == "PASS"
    assert "报告期末日" in result.calculation_basis


if __name__ == "__main__":
    test_pass_same_day()
    test_payment_days_ignored()
    test_fail_early_recognition_so25_0281()
    test_fail_cross_period_delayed()
    test_pass_same_period_day_diff()
    test_fail_year_end_boundary_not_tolerance()
    test_period_end_boundary_fail()
    test_period_end_same_side_pass()
    print("test_cutoff_checker: ALL PASS")
