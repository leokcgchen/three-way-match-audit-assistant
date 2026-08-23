"""截止 Mock：对照 truth_manifest（不上传给待测系统）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.rules.cutoff_checker import CutoffChecker

MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "mock"
    / "cutoff_period"
    / "truth_manifest.json"
)


def test_cutoff_truth_manifest_all_cases():
    if not MANIFEST.is_file():
        pytest.skip("cutoff_period mock 未随仓库提供")
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = data["cases"]
    assert len(cases) >= 10
    checker = CutoffChecker()
    for case in cases:
        result = checker.check(
            contract_payment_days=None,
            receipt_date=case["receipt_date"],
            entry_date=case["entry_date"],
        )
        assert result.test_status == case["expected_status"], (
            f"{case['business_id']}: got {result.test_status}, "
            f"want {case['expected_status']}; issue={result.issue_description}"
        )
        blob = (result.issue_description or "") + (result.calculation_basis or "")
        for token in case.get("issue_contains") or []:
            assert token in blob, f"{case['business_id']} missing {token!r} in {blob}"
