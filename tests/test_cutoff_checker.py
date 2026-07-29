"""截止性引擎单元测试（控制权转移日 vs 过账日，不含付款账期）。"""

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
    print("test_pass_same_day: PASS")


def test_payment_days_ignored():
    """付款账期不得把应确认日往后推。"""
    result = CutoffChecker().check(
        contract_payment_days=30,
        receipt_date="2026-01-02",
        entry_date="2026-01-02",
    )
    assert result.expected_revenue_date == "2026-01-02"
    assert "付款账期不参与" in result.calculation_basis
    print("test_payment_days_ignored: PASS")


def test_fail_early_recognition_so25_0281():
    """验收 2026-01-02，过账 2025-12-10 → 提前确认 FAIL。"""
    result = CutoffChecker().check(
        contract_payment_days=30,
        receipt_date="2026-01-02",
        entry_date="2025-12-10",
    )
    assert result.test_status == "FAIL"
    assert result.expected_revenue_date == "2026-01-02"
    assert result.deviation_days is not None and result.deviation_days < 0
    assert "提前" in result.issue_description
    print("test_fail_early_recognition_so25_0281: PASS")


def test_warning_delayed():
    result = CutoffChecker().check(
        contract_payment_days=None,
        receipt_date="2026-06-01",
        entry_date="2026-06-11",
    )
    assert result.test_status == "WARNING"
    assert result.expected_revenue_date == "2026-06-01"
    assert result.deviation_days == 10
    print("test_warning_delayed: PASS")


def test_pass_within_tolerance():
    result = CutoffChecker().check(
        contract_payment_days=10,
        receipt_date="2026-06-01",
        entry_date="2026-06-02",
    )
    assert result.test_status == "PASS"
    assert result.deviation_days == 1
    print("test_pass_within_tolerance: PASS")


if __name__ == "__main__":
    test_pass_same_day()
    test_payment_days_ignored()
    test_fail_early_recognition_so25_0281()
    test_warning_delayed()
    test_pass_within_tolerance()
    print("test_cutoff_checker: ALL PASS")
