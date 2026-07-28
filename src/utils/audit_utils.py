"""截止性测试自校验与审计辅助工具。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.rules.cutoff_checker import CutoffChecker


def verify_cutoff_calculation(
    receipt_date: Optional[str],
    entry_date: Optional[str],
    payment_days: int,
    expected_result: str,
) -> dict:
    """
    用于测试场景：人工提供预期结果，系统自动比对实际结果是否一致。

    返回：{"match": True/False, "actual": "...", "expected": "...", "trail": [...]}
    """
    checker = CutoffChecker()
    result = checker.check(
        contract_payment_days=payment_days,
        receipt_date=receipt_date,
        entry_date=entry_date,
    )
    actual = result.test_status
    expected = str(expected_result).strip().upper()
    return {
        "match": actual == expected,
        "actual": actual,
        "expected": expected,
        "trail": result.calculation_trail or [],
        "deviation_days": result.deviation_days,
        "issue_description": result.issue_description,
        "calculation_basis": result.calculation_basis,
    }


BUILTIN_SELF_CHECK_CASES: List[Dict[str, Any]] = [
    {
        "name": "完全匹配 PASS",
        "receipt_date": "2026-06-01",
        "entry_date": "2026-06-11",
        "payment_days": 10,
        "expected": "PASS",
    },
    {
        "name": "容差内 PASS",
        "receipt_date": "2026-06-01",
        "entry_date": "2026-06-12",
        "payment_days": 10,
        "expected": "PASS",
    },
    {
        "name": "提前确认 FAIL",
        "receipt_date": "2026-06-01",
        "entry_date": "2026-06-05",
        "payment_days": 10,
        "expected": "FAIL",
    },
    {
        "name": "延迟确认 WARNING",
        "receipt_date": "2026-06-01",
        "entry_date": "2026-06-20",
        "payment_days": 10,
        "expected": "WARNING",
    },
    {
        "name": "缺少签收日 WARNING",
        "receipt_date": None,
        "entry_date": "2026-06-11",
        "payment_days": 10,
        "expected": "WARNING",
    },
]


def run_builtin_cutoff_self_check() -> dict:
    """运行内置 5 组自检用例，返回汇总报告。"""
    details: List[Dict[str, Any]] = []
    passed = 0
    for case in BUILTIN_SELF_CHECK_CASES:
        outcome = verify_cutoff_calculation(
            receipt_date=case["receipt_date"],  # type: ignore[arg-type]
            entry_date=case["entry_date"],
            payment_days=int(case["payment_days"]),
            expected_result=str(case["expected"]),
        )
        ok = bool(outcome["match"])
        if ok:
            passed += 1
        details.append(
            {
                "name": case["name"],
                "ok": ok,
                "actual": outcome["actual"],
                "expected": outcome["expected"],
                "deviation_days": outcome.get("deviation_days"),
            }
        )
    total = len(BUILTIN_SELF_CHECK_CASES)
    return {
        "passed": passed,
        "total": total,
        "all_passed": passed == total,
        "details": details,
        "summary": (
            f"✅ 全部通过：{passed}/{total}"
            if passed == total
            else f"❌ 失败：{passed}/{total}，失败详情："
            + "; ".join(
                f"{d['name']}(期望{d['expected']}/实际{d['actual']})"
                for d in details
                if not d["ok"]
            )
        ),
    }


def serialize_calculation_trail(trail: Optional[List[dict]]) -> Optional[str]:
    """将计算轨迹序列化为 JSON 字符串，便于写入 Excel。"""
    if not trail:
        return None
    import json

    return json.dumps(trail, ensure_ascii=False)
