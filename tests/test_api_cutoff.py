"""手动测试 /api/v1/cutoff（需先启动 API：python run_api.py）。"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API = "http://127.0.0.1:8000/api/v1/cutoff"


def _post(payload: dict) -> dict:
    resp = requests.post(API, json=payload, timeout=10)
    print(f"HTTP {resp.status_code} | payload keys={list(payload.keys())}")
    data = resp.json()
    print(data)
    resp.raise_for_status()
    return data


def test_pass() -> None:
    data = _post(
        {
            "业务编号": "SO-001",
            "签收日期": "2026-06-01",
            "入账日期": "2026-06-11",
            "入账金额": 500,
            "合同账期天数": 10,
        }
    )
    assert data["测试状态"] == "PASS", data
    print("test_pass: PASS")


def test_fail_early() -> None:
    data = _post(
        {
            "业务编号": "SO-002",
            "签收日期": "2026-06-01",
            "入账日期": "2026-06-05",
            "入账金额": 500,
            "合同账期天数": 10,
        }
    )
    assert data["测试状态"] == "FAIL", data
    print("test_fail_early: PASS")


def test_warning_no_payment_days() -> None:
    # 无账期时引擎按 0 天处理：签收 06-01、入账 06-11 → 延迟 → WARNING
    data = _post(
        {
            "业务编号": "SO-003",
            "签收日期": "2026-06-01",
            "入账日期": "2026-06-11",
            "入账金额": 500,
        }
    )
    assert data["测试状态"] == "WARNING", data
    print("test_warning_no_payment_days: PASS")


if __name__ == "__main__":
    try:
        test_pass()
        test_fail_early()
        test_warning_no_payment_days()
        print("全部测试通过：/api/v1/cutoff 正常。")
    except requests.exceptions.ConnectionError:
        print("无法连接 API。请先运行: python run_api.py")
        sys.exit(1)
